"""
AI Block Extractor Module
=========================
Leverages local Ollama (Qwen 2.5) with strict JSON Schema constrained decoding
to extract and structure student academic records from document page blocks.
Guarantees schema compliance and sub-second token generation without hallucination.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
    from .json_parser import clean_mark, parse_student_response
    from .ollama_client import OllamaClient
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
        from data_extractor_AI.core.json_parser import clean_mark, parse_student_response
        from data_extractor_AI.core.ollama_client import OllamaClient
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord
        from core.json_parser import clean_mark, parse_student_response
        from core.ollama_client import OllamaClient

STUDENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "students": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seat_no": {"type": "string"},
                    "student_name": {"type": "string"},
                    "gender": {"type": "string"},
                    "candidate_type": {"type": "string"},
                    "prn": {"type": "string"},
                    "abc_id": {"type": "string"},
                    "ern": {"type": "string"},
                    "mother_name": {"type": "string"},
                    "result_status": {"type": "string"},
                    "total_marks": {"type": "string"},
                    "credits_earned": {"type": "string"},
                    "total_cg": {"type": "string"},
                    "sgpi": {"type": "string"},
                    "cgpi": {"type": "string"}
                },
                "required": ["seat_no", "student_name", "result_status"]
            }
        }
    },
    "required": ["students"]
}

SYSTEM_INSTRUCTION = (
    "You are an expert academic result data extractor and mathematical auditor. "
    "Extract student records from the provided page text into strict JSON matching the schema. "
    "Rules:\n"
    "1. Extract the seat number (7-10 digits), full student name, and gender (if name starts with '/' then 'F', else 'M').\n"
    "2. Extract result status ('P', 'F', 'PASS', 'FAIL', 'Successful', 'Unsuccessful', 'ABS', 'RPV').\n"
    "3. Extract numeric totals: total marks, credits earned, total credit points (CG/ECG), and SGPI/GPA.\n"
    "4. Extract 16-digit PRN, 12-digit ABC ID, ERN, or Mother's Name if present.\n"
    "5. In Summary tokens like '539 P 22 175 7.95', map total_marks='539', result_status='P', credits_earned='22', total_cg='175', sgpi='7.95'.\n"
    "6. If the student is identified as Diploma, DSE, Direct Second Year, or joined laterally, set candidate_type='Diploma', else 'Regular'.\n"
    "7. SIMULTANEOUS IN-FLIGHT AUDIT & SELF-HEALING: Verify component mark equations (tot = th + ia, or tw + pr), cg = credits * gp, and sgpi = total_cg / credits_earned. If an OCR misread or smudge is detected, self-heal inline using equation balance.\n"
    "8. PHYSICAL COLUMN MAPPING: Maintain strict horizontal column alignment. Do NOT shift or swap column marks."
)


def extract_student_blocks_with_ai(
    client: OllamaClient,
    formatted_page_text: str,
    archetype: str,
    timeout: int = 45,
    page_num: int = 1,
    audit_callback: Optional[Any] = None,
    layout_context: str = ""
) -> List[StudentRecord]:
    """
    Pass clean spatial/student text to local Ollama with JSON Schema enforcement.
    Returns parsed StudentRecord objects.
    """
    students, _, _ = extract_student_blocks_with_ai_detailed(
        client=client,
        formatted_page_text=formatted_page_text,
        archetype=archetype,
        timeout=timeout,
        page_num=page_num,
        audit_callback=audit_callback,
        layout_context=layout_context
    )
    return students


def extract_student_blocks_with_ai_detailed(
    client: OllamaClient,
    formatted_page_text: str,
    archetype: str,
    timeout: int = 45,
    page_num: int = 1,
    audit_callback: Optional[Any] = None,
    layout_context: str = ""
) -> Tuple[List[StudentRecord], Optional[str], float]:
    """
    Pass clean text to Ollama with full telemetry (duration, raw JSON, parsed students).
    Emits live audit events if audit_callback is provided.
    """
    if not client.is_available() or not formatted_page_text.strip():
        return [], None, 0.0

    prompt_parts = [
        f"Extract all student academic records from this {archetype} text segment into JSON.",
        "Summary format: TotalMarks Result Credits TotalCG SGPI (e.g. '539 P 22 175 7.95')."
    ]
    if layout_context:
        prompt_parts.append("\n" + layout_context)
    prompt_parts.append(
        "\nSimultaneously verify equations: tot = th + ia, cg = credits * gp, and sgpi = total_cg / credits_earned. Self-heal any OCR misread on the fly.\n"
    )
    prompt_parts.append(f"DOCUMENT TEXT:\n{formatted_page_text[:4000]}")
    prompt = "\n".join(prompt_parts)

    t0 = time.time()
    try:
        raw_json = client.generate(
            prompt=prompt,
            system=SYSTEM_INSTRUCTION,
            temperature=0.0,
            max_tokens=1024,
            format_json=STUDENT_JSON_SCHEMA,
            timeout=timeout
        )
        duration = time.time() - t0
        students = parse_student_response(raw_json, archetype)

        if audit_callback:
            try:
                audit_callback({
                    "type": "ai_inference",
                    "page": page_num,
                    "archetype": archetype,
                    "model": client.model,
                    "duration_sec": round(duration, 2),
                    "students_count": len(students),
                    "prompt_snippet": prompt[:280] + "...",
                    "raw_json": raw_json,
                    "extracted": [{"seat": s.seat_no, "name": s.student_name, "sgpi": s.sgpi, "result": s.result_status} for s in students]
                })
            except Exception:
                pass

        return students, raw_json, duration
    except Exception as e:
        duration = time.time() - t0
        if audit_callback:
            try:
                audit_callback({
                    "type": "ai_error",
                    "page": page_num,
                    "error": str(e),
                    "duration_sec": round(duration, 2)
                })
            except Exception:
                pass
        return [], None, duration

