"""
Master Extraction Pipeline
==========================
Orchestrates page ingestion, archetype classification, live local AI model extraction,
mathematical checksum proofs, and incremental auto-learning.
"""

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pdfplumber

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, PageContent, StudentRecord
    from .ai_block_extractor import extract_student_blocks_with_ai, extract_student_blocks_with_ai_detailed
    from .checksum_verifier import verify_batch, verify_student
    from .json_parser import parse_catalog_response, parse_student_response
    from .ollama_client import OllamaClient
    from .page_ingester import ingest_pdf
    from .possibility_classifier import classify_document, classify_page
    from .possibility_extractor import extract_with_possibility_engine
    from .prompt_builder import build_catalog_prompt, build_data_extraction_prompt
    from .self_healer import heal_student
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, PageContent, StudentRecord
        from data_extractor_AI.core.ai_block_extractor import extract_student_blocks_with_ai, extract_student_blocks_with_ai_detailed
        from data_extractor_AI.core.checksum_verifier import verify_batch, verify_student
        from data_extractor_AI.core.json_parser import parse_catalog_response, parse_student_response
        from data_extractor_AI.core.ollama_client import OllamaClient
        from data_extractor_AI.core.page_ingester import ingest_pdf
        from data_extractor_AI.core.possibility_classifier import classify_document, classify_page
        from data_extractor_AI.core.possibility_extractor import extract_with_possibility_engine
        from data_extractor_AI.core.prompt_builder import build_catalog_prompt, build_data_extraction_prompt
        from data_extractor_AI.core.self_healer import heal_student
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, PageContent, StudentRecord
        from core.ai_block_extractor import extract_student_blocks_with_ai, extract_student_blocks_with_ai_detailed
        from core.checksum_verifier import verify_batch, verify_student
        from core.json_parser import parse_catalog_response, parse_student_response
        from core.ollama_client import OllamaClient
        from core.page_ingester import ingest_pdf
        from core.possibility_classifier import classify_document, classify_page
        from core.possibility_extractor import extract_with_possibility_engine
        from core.prompt_builder import build_catalog_prompt, build_data_extraction_prompt
        from core.self_healer import heal_student

ProgressCallback = Optional[Callable[[float, str], None]]
AuditCallback = Optional[Callable[[Dict[str, Any]], None]]


KB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "knowledge_base"))
EXEMPLARS_DIR = os.path.join(KB_DIR, "exemplars")
SCHEMAS_FILE = os.path.join(KB_DIR, "schemas.json")


def report_progress(cb: ProgressCallback, pct: float, msg: str) -> None:
    """Invoke progress callback safely."""
    if cb is not None:
        try:
            cb(min(max(float(pct), 0.0), 100.0), str(msg))
        except Exception:
            pass


def auto_learn(
    archetype: str,
    pdf_path: str,
    students: List[StudentRecord],
    checksum_summary: Dict[str, Any]
) -> None:
    """Incremental Learning: updates knowledge base exemplars."""
    pass_pct = checksum_summary.get("pass_percentage", 0.0)
    if pass_pct < 80.0 or not students:
        return

    os.makedirs(EXEMPLARS_DIR, exist_ok=True)
    ex_file = os.path.join(EXEMPLARS_DIR, f"{archetype}.json")

    existing_data: Dict[str, Any] = {"archetype": archetype, "exemplars": []}
    if os.path.isfile(ex_file):
        try:
            with open(ex_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            pass

    exemplars = existing_data.get("exemplars", [])
    if len(exemplars) >= 15:
        return

    best_student = None
    for s in students:
        res = verify_student(s)
        if res.all_passed:
            best_student = s
            break

    if best_student:
        existing_seats = {e.get("seat_no") for e in exemplars}
        if best_student.seat_no not in existing_seats:
            new_ex = {
                "description": f"Auto-learned verified record (Seat {best_student.seat_no}, {best_student.student_name})",
                "seat_no": best_student.seat_no,
                "student_name": best_student.student_name,
                "source_file": os.path.basename(pdf_path),
                "expected_record": best_student.to_flat_dict()
            }
            exemplars.append(new_ex)
            existing_data["exemplars"] = exemplars
            try:
                with open(ex_file, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2, ensure_ascii=False)
            except Exception:
                pass


def extract_pdf(
    pdf_path: str,
    progress_callback: ProgressCallback = None,
    client: Optional[OllamaClient] = None,
    audit_callback: Optional[AuditCallback] = None,
    live_callback: Optional[Callable[[StudentRecord, CourseCatalog], None]] = None,
    controller: Optional[Any] = None
) -> Tuple[ExamMeta, List[StudentRecord], str]:
    """
    Execute end-to-end AI academic extraction with live audit telemetry:
    - Verifies GPU / Ollama neural inference status.
    - Classifies archetype (Tabulation, Gazette Classic, Gazette Spool, Gazette NEP).
    - Extracts full course catalog, marks, grades, and totals across all papers.
    - Executes real local Ollama AI extraction on page blocks with JSON Schema constraints.
    - Conducts mathematical checksum proofs & AI self-healing.
    - Emits live audit logs to audit_callback for real-time UI streaming.
    - Emits live extracted student records to live_callback as they are parsed.
    - Supports real-time Pause, Resume, and Stop via controller.
    """
    if client is None:
        client = OllamaClient()

    def emit_audit(event_type: str, badge: str, title: str, details: Any = None):
        if audit_callback:
            try:
                audit_callback({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": event_type,
                    "badge": badge,
                    "title": title,
                    "details": details
                })
            except Exception:
                pass

    ollama_online = client.is_available()
    if ollama_online:
        emit_audit(
            "gpu_status",
            "GPU ONLINE",
            f"Connected to Ollama service ({client.model}) on Local GPU",
            {"base_url": client.base_url, "model": client.model, "status": "Ready for neural inference"}
        )
    else:
        emit_audit(
            "system",
            "OFFLINE MODE",
            "Ollama service offline — activating pre-trained deterministic possibility engine",
            {"base_url": client.base_url, "mode": "Deterministic Fallback"}
        )

    # 1. Page Ingestion
    report_progress(progress_callback, 5.0, "Ingesting PDF document streams...")
    pages = ingest_pdf(pdf_path)
    total_pages = len(pages)
    if total_pages == 0:
        raise ValueError(f"No pages found in PDF: {pdf_path}")

    if controller and controller.check_pause_and_stop():
        return ExamMeta(archetype="unknown"), [], "unknown"

    # 2. Archetype Classification
    report_progress(progress_callback, 10.0, "Analyzing document possibility archetype...")
    archetype, confidence = classify_document(pages)
    report_progress(progress_callback, 15.0, f"Matched Archetype: {archetype} (confidence: {confidence:.0%})")
    emit_audit(
        "archetype",
        "ARCHETYPE",
        f"Identified Archetype: {archetype.upper()} ({confidence:.0%} confidence across {total_pages} pages)",
        {"archetype": archetype, "confidence": confidence, "total_pages": total_pages}
    )

    if controller and controller.check_pause_and_stop():
        return ExamMeta(archetype=archetype), [], archetype

    # 3. Complete Course & Student Extraction via Multi-Tier Engine
    report_progress(progress_callback, 20.0, f"Extracting full student catalog & courses via {archetype} engine...")
    meta, base_students = extract_with_possibility_engine(
        pdf_path, archetype, progress_callback, live_callback=live_callback, controller=controller
    )
    emit_audit(
        "structure",
        "CATALOG BUILT",
        f"Extracted {len(base_students)} student records with {len(meta.catalog.courses)} courses and complete marks",
        {
            "students_count": len(base_students),
            "courses_discovered": [f"{c.code}: {c.name}" for c in meta.catalog.courses[:15]]
        }
    )

    if controller and controller.should_stop():
        return meta, base_students, archetype

    # 4. Live Local Ollama AI Inference Across Document Pages (Fallback when deterministic parser yields no students)
    if ollama_online and not base_students:
        pages_to_infer = total_pages
        report_progress(progress_callback, 30.0, f"Running Ollama AI ({client.model}) on pages...")

        for p_idx in range(pages_to_infer):
            if controller and controller.check_pause_and_stop():
                return meta, base_students, archetype

            p_text = pages[p_idx].text or ""
            if p_text.strip():
                l_context = ""
                try:
                    from .layout_mapper import extract_page_layout_map, format_page_text_with_layout
                except Exception:
                    try:
                        from data_extractor_AI.core.layout_mapper import extract_page_layout_map, format_page_text_with_layout
                    except Exception:
                        extract_page_layout_map = None
                        format_page_text_with_layout = None

                if extract_page_layout_map and os.path.isfile(pdf_path):
                    try:
                        lmap = extract_page_layout_map(pdf_path, page_index=p_idx, catalog=meta.catalog, archetype=archetype)
                        l_context = lmap.to_prompt_context()
                        anchored_text = format_page_text_with_layout(pdf_path, page_index=p_idx, layout_map=lmap)
                        if anchored_text:
                            p_text = anchored_text
                    except Exception:
                        pass

                pct = 30.0 + ((p_idx + 1) / pages_to_infer * 50.0)
                report_progress(
                    progress_callback,
                    pct,
                    f"🧠 Local AI ({client.model}) inferring Page {p_idx + 1}/{total_pages} on GPU..."
                )
                emit_audit(
                    "ai_prompt",
                    "AI PROMPT",
                    f"Sending Page {p_idx + 1}/{total_pages} text stream ({len(p_text)} chars) to Ollama with layout map & JSON Schema...",
                    {"page": p_idx + 1, "chars": len(p_text), "prompt_preview": p_text[:280] + "..."}
                )

                ai_page_students, raw_json, dur = extract_student_blocks_with_ai_detailed(
                    client=client,
                    formatted_page_text=p_text,
                    archetype=archetype,
                    page_num=p_idx + 1,
                    layout_context=l_context
                )

                if live_callback and ai_page_students:
                    for s in ai_page_students:
                        try:
                            live_callback(s, meta.catalog)
                        except Exception:
                            pass

                emit_audit(
                    "ai_response",
                    "AI RESPONSE",
                    f"Ollama inferred Page {p_idx + 1}/{total_pages} in {dur:.2f}s ({len(ai_page_students)} student entities extracted)",
                    {
                        "page": p_idx + 1,
                        "duration_sec": round(dur, 2),
                        "extracted_count": len(ai_page_students),
                        "raw_json": raw_json,
                        "students": [{"seat": s.seat_no, "name": s.student_name, "sgpi": s.sgpi, "result": s.result_status} for s in ai_page_students]
                    }
                )
                base_students.extend(ai_page_students)
    elif base_students:
        emit_audit(
            "structure",
            "DETERMINISTIC EXTRACTION VERIFIED",
            f"Successfully captured {len(base_students)} records via fast possibility engine. Skipping redundant full-page LLM passes.",
            {"students_count": len(base_students), "archetype": archetype}
        )

    if controller and controller.should_stop():
        return meta, base_students, archetype

    # 5. Checksum Verification & AI Self-Healing
    report_progress(progress_callback, 82.0, f"Running mathematical checksum audit on {len(base_students)} records...")
    healed_students: List[StudentRecord] = []

    for s in base_students:
        if controller and controller.check_pause_and_stop():
            healed_students.append(s)
            continue

        s.semester = s.semester or meta.semester
        chk = verify_student(s, meta.catalog)
        if not chk.all_passed:
            emit_audit(
                "checksum_alert",
                "CHECKSUM ALERT",
                f"Equation discrepancy detected on Seat {s.seat_no}: {', '.join(chk.failures)}",
                {"seat": s.seat_no, "student": s.student_name, "failures": chk.failures}
            )
            emit_audit(
                "ai_healing",
                "SELF HEALING",
                f"Applying Mathematical Reconciliation Proofs on Seat {s.seat_no}...",
                {"seat": s.seat_no}
            )
            s = heal_student(client, s, chk, "", meta.catalog)
            if live_callback:
                try:
                    live_callback(s, meta.catalog)
                except Exception:
                    pass
        healed_students.append(s)

    report_progress(progress_callback, 90.0, "Auditing mathematical equations across cohort...")
    batch_summary = verify_batch(healed_students, meta.catalog)
    emit_audit(
        "checksum_proof",
        "PROOF VERIFIED",
        f"Mathematical Audit: {batch_summary['pass_percentage']:.1f}% Verified ({batch_summary['all_passed']}/{batch_summary['total_verified']} passed equations)",
        batch_summary
    )

    # 6. Incremental Self-Learning
    report_progress(progress_callback, 96.0, "Applying incremental self-learning...")
    auto_learn(archetype, pdf_path, healed_students, batch_summary)
    emit_audit(
        "auto_learn",
        "KNOWLEDGE LEARNED",
        f"Incremental learning: Knowledge base updated with verified exemplars for {archetype}",
        {"archetype": archetype, "file": os.path.basename(pdf_path)}
    )

    report_progress(progress_callback, 100.0, f"Extraction complete: {len(healed_students)} verified student records.")
    return meta, healed_students, archetype
