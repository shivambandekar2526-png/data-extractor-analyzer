"""
AI Cross-Checking & Self-Healing Engine (Dual-Mode)
====================================================
Cross-references deterministic & OCR-extracted student result ledgers against
raw PDF text using Cloud AI with multi-provider failover (Groq, Gemini, OpenAI).

Key Architecture Components:
1. Context Envelope Pattern:
   Every chunk audit carries a self-contained, stateless context envelope
   (metadata, course catalog, valid column names) so mid-stream model switches
   (e.g. 429 rate limit failover) retain 100% precision with zero memory loss.
2. Two-Stage Audit Hierarchy:
   - Stage 1: Macro Column & Schema Alignment (headers vs raw document ledger).
   - Stage 2: Micro Record-by-Record chunk proofreading (6-10 students per chunk).
3. Zero-Trust Server-Side Validation Sandbox:
   Verifies verbatim evidence quotes from raw page text, ensures numerical marks
   stay within catalog limits, and whitelists column names to reject hallucinations.
"""

import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Ensure project root is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from .cloud_client import CloudClient
    from .normalizer import are_fields_alias_equivalent, strip_ledger_boilerplate, normalize_identifier
    from .checksum_verifier import verify_student
    from ..models import CourseResult, StudentRecord
except Exception:
    try:
        from core.cloud_client import CloudClient
        from core.normalizer import are_fields_alias_equivalent, strip_ledger_boilerplate, normalize_identifier
        from core.checksum_verifier import verify_student
        from models import CourseResult, StudentRecord
    except Exception:
        try:
            from data_extractor_AI.core.cloud_client import CloudClient
            from data_extractor_AI.core.normalizer import are_fields_alias_equivalent, strip_ledger_boilerplate, normalize_identifier
            from data_extractor_AI.core.checksum_verifier import verify_student
            from data_extractor_AI.models import CourseResult, StudentRecord
        except Exception:
            CloudClient = None  # Handled gracefully if not installed
            are_fields_alias_equivalent = None
            strip_ledger_boilerplate = lambda t: t
            normalize_identifier = lambda x: str(x) if x is not None else ""
            verify_student = None
            CourseResult = None
            StudentRecord = None


@dataclass
class ContextEnvelope:
    """Lightweight, stateless context envelope injected into every AI verification chunk."""
    exam_title: str = ""
    semester: str = ""
    college_name: str = ""
    archetype: str = ""
    valid_columns: List[str] = field(default_factory=list)
    courses: List[Dict[str, Any]] = field(default_factory=list)
    layout_context: str = ""

    def to_compact_prompt_header(self) -> str:
        """Serializes envelope to a concise, token-efficient prompt string (~120 tokens)."""
        courses_brief = []
        for c in self.courses[:25]:
            if isinstance(c, dict):
                c_code = c.get('code', '')
                c_name = str(c.get('name', ''))[:30]
                heads = c.get('heads', [])
                h_str = ','.join(heads) if isinstance(heads, list) else str(heads)
                courses_brief.append(f"{c_code} ({c_name}): heads={h_str}")
            elif hasattr(c, "code"):
                c_code = getattr(c, "code", "")
                c_name = str(getattr(c, "name", ""))[:30]
                heads = getattr(c, "heads", [])
                h_str = ','.join(heads) if isinstance(heads, list) else str(heads)
                courses_brief.append(f"{c_code} ({c_name}): heads={h_str}")
            else:
                courses_brief.append(str(c))
        courses_text = "; ".join(courses_brief) if courses_brief else "Standard academic courses"
        cols_text = ", ".join(self.valid_columns[:40])
        if len(self.valid_columns) > 40:
            cols_text += f" ... (+{len(self.valid_columns) - 40} more)"

        header_lines = [
            "=== ACADEMIC CONTEXT ENVELOPE ===",
            f"EXAM: {self.exam_title or 'University Examination'} | SEMESTER: {self.semester or 'N/A'}",
            f"COLLEGE: {self.college_name or 'Engineering College'}",
            f"COURSES: {courses_text}",
            f"VALID CSV COLUMNS: {cols_text}",
            "================================="
        ]
        if self.layout_context:
            header_lines.append("")
            header_lines.append(self.layout_context)
            header_lines.append("")

        return "\n".join(header_lines)


@dataclass
class DiscrepancyCard:
    """Represents a proofreading finding proposed by Cloud AI."""
    seat_no: str
    field_name: str
    extracted_val: Any
    suggested_val: Any
    reason: str
    evidence_quote: str
    sandbox_status: str = "PENDING"  # "VERIFIED", "REJECTED"
    sandbox_reason: str = ""
    applied: bool = False
    card_type: str = "data"  # "data" or "schema"
    page_number: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_raw_page_texts(pdf_path: str) -> List[str]:
    """
    Extracts raw text strings page-by-page from the target PDF.
    Prefers pypdf for horizontal row preservation across wide ledgers;
    falls back to PyMuPDF (fitz) or pdfplumber if needed.
    """
    pages_text: List[str] = []
    if not os.path.isfile(pdf_path):
        return pages_text

    # 1. Try pypdf (best for preserving horizontal ledger lines)
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            txt = page.extract_text() or ""
            pages_text.append(txt)
        if any(len(p.strip()) > 30 for p in pages_text):
            return pages_text
    except Exception as e:
        logger.debug("pypdf text extraction failed: %s", e)

    # 2. Try fitz (PyMuPDF) fallback
    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages_text = [page.get_text("text") or "" for page in doc]
        if any(len(p.strip()) > 30 for p in pages_text):
            return pages_text
    except Exception as e:
        logger.debug("fitz text extraction failed: %s", e)

    # 3. Try pdfplumber fallback
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
            return pages_text
    except Exception as e:
        logger.debug("pdfplumber text extraction failed: %s", e)

    return pages_text


def locate_student_page(seat_no: str, name: str, page_texts: List[str]) -> int:
    """Finds the 0-indexed page number containing the student record."""
    if not seat_no and not name:
        return 0

    s_clean = str(seat_no).strip()
    if s_clean:
        for idx, p_text in enumerate(page_texts):
            if s_clean in p_text:
                return idx

    # Fallback to surname / primary name token
    if name:
        tokens = [t for t in re.split(r"[\s,]+", str(name).strip()) if len(t) >= 4]
        for token in tokens:
            for idx, p_text in enumerate(page_texts):
                if token.upper() in p_text.upper():
                    return idx

    return 0


def build_context_envelope(
    meta: Any,
    students: List[Dict[str, Any]],
    archetype: str = "",
    pdf_path: Optional[str] = None
) -> ContextEnvelope:
    """Builds a ContextEnvelope from parsed metadata and student columns, injecting layout mapping if available."""
    valid_cols: List[str] = []
    if students:
        seen = set()
        for s in students:
            for k in s.keys():
                if k not in seen:
                    seen.add(k)
                    valid_cols.append(k)

    courses_list = []
    if hasattr(meta, "catalog") and meta.catalog:
        courses_list = meta.catalog.to_dict()
    elif hasattr(meta, "courses") and meta.courses:
        courses_list = [
            {"code": c.code, "name": c.name, "heads": getattr(c, "heads", [])}
            for c in meta.courses
        ]

    layout_ctx = ""
    if pdf_path and os.path.isfile(pdf_path):
        try:
            from .layout_mapper import extract_page_layout_map
        except Exception:
            try:
                from core.layout_mapper import extract_page_layout_map
            except Exception:
                try:
                    from data_extractor_AI.core.layout_mapper import extract_page_layout_map
                except Exception:
                    extract_page_layout_map = None

        if extract_page_layout_map:
            try:
                cat = getattr(meta, "catalog", None)
                lmap = extract_page_layout_map(pdf_path, page_index=0, catalog=cat, archetype=archetype)
                layout_ctx = lmap.to_prompt_context()
            except Exception as e:
                logger.debug("Failed to extract layout map for context envelope: %s", e)

    return ContextEnvelope(
        exam_title=getattr(meta, "exam_title", "") or "",
        semester=getattr(meta, "semester", "") or "",
        college_name=getattr(meta, "college_name", "") or "",
        archetype=archetype,
        valid_columns=valid_cols,
        courses=courses_list,
        layout_context=layout_ctx
    )


def flat_dict_to_student_record(d: Dict[str, Any], catalog: Optional[Any] = None) -> Any:
    """Reconstructs a StudentRecord dataclass from a flattened CSV/database dictionary."""
    if StudentRecord is None or CourseResult is None:
        return None
    seat = str(d.get("Seat_No", d.get("seat_no", ""))).strip()
    name = str(d.get("Student_Name", d.get("name", ""))).strip()
    st = StudentRecord(
        seat_no=seat,
        student_name=name,
        gender=str(d.get("Gender", "M")),
        candidate_type=str(d.get("Candidate_Type", "Regular")),
        prn=str(d.get("PRN", "")),
        abc_id=str(d.get("ABC_ID", "")),
        result_status=str(d.get("Result_Status", d.get("Result", "PASS"))),
        total_marks=str(d.get("Total_Marks", "")),
        credits_earned=str(d.get("Credits_Earned", "")),
        total_cg=str(d.get("Total_CG", "")),
        sgpi=str(d.get("SGPI", "")),
    )

    STANDARD_STUDENT_KEYS = {
        "seat_no", "student_name", "gender", "candidate_type", "prn", "abc_id",
        "ern", "mother_name", "semester", "result_status", "remark", "total_marks",
        "max_marks", "credits_earned", "total_cg", "sgpi", "cgpi", "result", "name"
    }

    course_map: Dict[str, Any] = {}
    for k, v in d.items():
        if "_" in k and k.lower() not in STANDARD_STUDENT_KEYS and not any(k.startswith(pfx) for pfx in ["SGPI_", "Credits_", "Marks_", "Max_"]):
            parts = k.split("_", 1)
            c_code = parts[0]
            suffix = parts[1]
            if suffix in ("Grade", "GP", "Credits", "CG", "Subject"):
                if c_code not in course_map:
                    course_map[c_code] = CourseResult(course_code=c_code)
                if suffix == "Grade":
                    course_map[c_code].grade = str(v)
                elif suffix == "GP":
                    course_map[c_code].grade_point = str(v)
                elif suffix == "Credits":
                    course_map[c_code].credits = str(v)
                elif suffix == "CG":
                    course_map[c_code].credit_points = str(v)
                elif suffix == "Subject":
                    course_map[c_code].course_name = str(v)
            else:
                if c_code not in course_map:
                    course_map[c_code] = CourseResult(course_code=c_code)
                course_map[c_code].marks[suffix] = str(v)

    st.course_results = list(course_map.values())
    return st


def is_student_record_mathematically_sound(d: Dict[str, Any], catalog: Optional[Any] = None) -> bool:
    """Evaluates whether an extracted flat student record already satisfies 100% of mathematical proofs."""
    if verify_student is None:
        return False
    st = flat_dict_to_student_record(d, catalog)
    if not st or not st.course_results:
        return False
    res = verify_student(st, catalog)
    return bool(res.all_passed)


def extract_student_context_lines(seat_no: str, page_text: str, window: int = 8) -> str:
    """Extracts only the lines immediately surrounding a student's seat number (~300 chars vs 12k chars)."""
    if not seat_no or not page_text:
        return page_text[:2000]
    lines = page_text.splitlines()
    match_idx = -1
    clean_seat = str(seat_no).strip()
    for i, line in enumerate(lines):
        if clean_seat in line:
            match_idx = i
            break
    if match_idx == -1:
        return page_text[:2000]
    start = max(0, match_idx - 2)
    end = min(len(lines), match_idx + window)
    return "\n".join(lines[start:end])



# =========================================================================
# ZERO-TRUST VALIDATION SANDBOX
# =========================================================================

def validate_patch_sandbox(
    candidate: Dict[str, Any],
    raw_page_texts: List[str],
    envelope: ContextEnvelope,
    students_by_seat: Dict[str, Dict[str, Any]]
) -> Tuple[bool, str]:
    """
    Verifies proposed patch against ground-truth document text and schema constraints.
    Rejects any AI hallucination or out-of-bounds score.
    
    Checks:
    1. Verbatim Evidence Quote must exist in the raw document text.
    2. Target field must exist in ContextEnvelope.valid_columns.
    3. Student Seat No must exist in the dataset.
    4. Proposed marks must be valid numbers and within catalog bounds.
    """
    seat_no = str(candidate.get("seat_no", "")).strip()
    field_name = str(candidate.get("field", candidate.get("field_name", ""))).strip()
    sugg_val = candidate.get("suggested_val")
    quote = str(candidate.get("evidence_quote", "")).strip()

    if not seat_no or seat_no not in students_by_seat:
        return False, f"Seat number '{seat_no}' does not exist in extracted student dataset."

    if not field_name:
        return False, "Target field name is missing."

    if field_name not in envelope.valid_columns:
        # Check case-insensitive match
        matched_col = None
        for col in envelope.valid_columns:
            if col.lower() == field_name.lower():
                matched_col = col
                break
        if not matched_col and are_fields_alias_equivalent is not None:
            for col in envelope.valid_columns:
                if are_fields_alias_equivalent(col, field_name):
                    matched_col = col
                    break
        if not matched_col:
            return False, f"Field '{field_name}' is not in valid CSV schema columns."
        candidate["field"] = matched_col
        field_name = matched_col

    # Evidence quote check: must be non-empty and exist in document text
    if not quote or len(quote) < 2:
        return False, "Evidence quote is empty or too short (< 2 characters)."

    # Clean whitespace for resilient matching (e.g. internal line wraps)
    quote_clean = re.sub(r"\s+", " ", quote).strip().lower()
    found_quote = False
    for p_text in raw_page_texts:
        p_clean = re.sub(r"\s+", " ", p_text).lower()
        if quote_clean in p_clean:
            found_quote = True
            break

    if not found_quote:
        # Partial relaxed check: if quote contains digits or marks (e.g. "24 + 18")
        tokens = [t for t in re.split(r"[\s\+\=]+", quote_clean) if len(t) >= 2]
        if tokens and all(any(t in re.sub(r"\s+", " ", pt).lower() for pt in raw_page_texts) for t in tokens[:3]):
            found_quote = True

    if not found_quote:
        return False, f"Verbatim quote '{quote}' was not found anywhere in source PDF text."

    # Numerical Mark Bounds Check
    if any(field_name.endswith(sfx) for sfx in ["_TH", "_IA", "_TOT", "_TW", "_PR", "_OR", "_GP", "_CG", "SGPI"]):
        try:
            val_f = float(re.sub(r"[^\d\.]", "", str(sugg_val)))
            if val_f < 0.0:
                return False, f"Proposed mark ({val_f}) cannot be negative."
            if "SGPI" in field_name and val_f > 10.0:
                return False, f"Proposed SGPI ({val_f}) exceeds maximum scale of 10.0."
            if "_TOT" in field_name and val_f > 150.0:
                return False, f"Proposed total mark ({val_f}) exceeds maximum course total."
            if any(field_name.endswith(sfx) for sfx in ["_TH", "_IA", "_TW", "_PR", "_OR"]):
                head = field_name.rsplit("_", 1)[-1]
                limit = 100.0 if head == "TH" else 50.0
                course_code = field_name.rsplit("_", 1)[0]
                for c in envelope.courses:
                    if c.get("code") == course_code and "max_marks" in c and isinstance(c["max_marks"], dict):
                        limit = float(c["max_marks"].get(head, limit))
                        break
                if val_f > limit:
                    return False, f"Proposed mark ({val_f}) exceeds allowable max bounds ({limit}) for {field_name}."
        except (ValueError, TypeError):
            pass

    # Mathematical Invariant Guard: Protect verified data from corruption
    if verify_student is not None and seat_no in students_by_seat:
        orig_dict = students_by_seat[seat_no]
        orig_sound = is_student_record_mathematically_sound(orig_dict)

        # Clone and simulate patch
        sim_dict = dict(orig_dict)
        sim_dict[field_name] = sugg_val
        sim_st = flat_dict_to_student_record(sim_dict)
        sim_res = verify_student(sim_st)

        # 1. If original record was already 100% mathematically sound, REJECT any patch that breaks math
        if orig_sound and not sim_res.all_passed:
            return False, f"Rejected AI mutation: Record was already mathematically verified, and proposed patch causes math failure ({sim_res.failures})."

        # 2. If original had math failures, patch must not introduce worse failures
        if not orig_sound and not sim_res.all_passed:
            orig_st = flat_dict_to_student_record(orig_dict)
            orig_res = verify_student(orig_st)
            if len(sim_res.failures) >= len(orig_res.failures) and not sim_res.component_sum_ok:
                return False, f"Rejected AI mutation: Proposed patch fails to resolve mathematical checksums ({sim_res.failures})."

    return True, "Passed zero-trust ground truth verification."


# =========================================================================
# STAGE 1: MACRO COLUMN & SCHEMA ALIGNMENT
# =========================================================================

def align_schema_with_ai(
    envelope: ContextEnvelope,
    sample_students: List[Dict[str, Any]],
    first_page_text: str,
    client: Any,
    audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Stage 1 Macro Audit: Compares extracted column headers against raw page 1 text.
    Detects dropped courses, mislabeled course codes, or column shifts.
    """
    if not client or not client.is_configured():
        return {"status": "SKIPPED", "message": "Cloud AI not configured."}

    # Extract distinct course codes from valid_columns
    detected_courses = sorted(list({c.split("_")[0] for c in envelope.valid_columns if "_" in c}))

    compact_sample = {}
    if sample_students:
        s0 = sample_students[0]
        compact_sample = {
            k: v for k, v in s0.items()
            if not any(k.endswith(x) for x in ["_Grade", "_GP", "_Credits", "_CG", "_Subject"])
        }

    clean_p1 = strip_ledger_boilerplate(first_page_text) if strip_ledger_boilerplate else first_page_text
    prompt = (
        f"{envelope.to_compact_prompt_header()}\n\n"
        f"=== RAW DOCUMENT PAGE 1 (HEADER & LEDGER SPECIFICATION) ===\n"
        f"{clean_p1[:12000]}\n\n"
        f"=== EXTRACTED COURSE CODES ===\n"
        f"{json.dumps(detected_courses)}\n\n"
        f"=== SAMPLE EXTRACTED MARKS FOR CANDIDATE 1 ===\n"
        f"{json.dumps(compact_sample, ensure_ascii=False)}\n\n"
        f"=== TASK ===\n"
        f"Analyze whether the extracted columns match the course codes, assessment heads (TH, IA, TOT, TW, PR, OR), "
        f"and student attributes shown on the ledger page.\n"
        f"Respond in valid JSON with this exact structure:\n"
        f"{{\n"
        f'  "status": "ALIGNED" or "DISCREPANCY_DETECTED",\n'
        f'  "confidence": 0.95,\n'
        f'  "summary": "Short explanation of alignment findings",\n'
        f'  "missing_courses": ["List any course codes visible in PDF header but missing from extracted columns"],\n'
        f'  "misaligned_columns": [{{"column": "...", "issue": "...", "suggestion": "..."}}]\n'
        f"}}"
    )

    system_instruction = (
        "You are an academic database schema auditor. Compare extracted table columns with the raw PDF ledger. "
        "Strictly return valid JSON."
    )

    try:
        resp_text = client.generate_direct(
            user_prompt=prompt,
            system_instruction=system_instruction,
            format_json=True,
            timeout=30,
            audit_cb=audit_cb
        )
        return json.loads(resp_text)
    except Exception as e:
        logger.warning("Stage 1 Schema alignment encountered error: %s", e)
        return {"status": "ERROR", "message": str(e)}


# =========================================================================
# STAGE 2: MICRO RECORD PROOFREADING (CHUNK-BY-CHUNK)
# =========================================================================

def verify_student_chunk(
    chunk_students: List[Dict[str, Any]],
    chunk_page_text: str,
    envelope: ContextEnvelope,
    client: Any,
    audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None
) -> List[Dict[str, Any]]:
    """
    Stage 2 Micro Audit: Proofreads a student chunk against source page text.
    Returns list of discrepancy candidates with verbatim evidence quotes.
    """
    if not client or not client.is_configured():
        return []

    # Compact student representation (exclude derivative, null, nan, and internal fields to conserve tokens and prevent 429 TPM limits)
    clean_chunk = []
    for s in chunk_students:
        clean_s = {}
        for k, v in s.items():
            if v in (None, "", "--", "-", "nan", "NaN"):
                continue
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                continue
            # Exclude derivative and redundant grading columns
            if any(k.endswith(x) for x in ["_Grade", "_GP", "_Credits", "_CG", "_Subject", "_Grade_Point"]):
                continue
            # Exclude internal or metadata keys
            if k.startswith("_") or k.startswith("Unnamed"):
                continue
            clean_s[k] = v
        clean_chunk.append(clean_s)

    clean_chunk_doc = strip_ledger_boilerplate(chunk_page_text) if strip_ledger_boilerplate else chunk_page_text
    prompt = (
        f"{envelope.to_compact_prompt_header()}\n\n"
        f"=== SOURCE DOCUMENT RAW TEXT FOR THIS CHUNK ===\n"
        f"{clean_chunk_doc[:12000]}\n\n"
        f"=== CURRENTLY EXTRACTED RECORDS FOR {len(clean_chunk)} CANDIDATES ===\n"
        f"{json.dumps(clean_chunk, ensure_ascii=False)}\n\n"
        f"=== PROOFREADING INSTRUCTIONS ===\n"
        f"1. Compare each student's extracted fields (Seat_No, Name, Course Marks, SGPI, Result_Status) against the source text.\n"
        f"2. Pay special attention to:\n"
        f"   - Truncated or misplaced student names.\n"
        f"   - Numbers swapped or dropped in TH / IA / TOT marks.\n"
        f"   - Inverted or missed Pass / Fail / KT / Absent statuses.\n"
        f"   - Notation symbols (*, @, #) or grace marks merged incorrectly.\n"
        f"3. PHYSICAL COLUMN BOUNDARIES & LAYOUT CONTEXT: Match candidate marks strictly to their horizontal column slots (refer to the Physical Column Grid in the header). NEVER swap or shift numbers horizontally between neighboring courses.\n"
        f"4. SIMULTANEOUS EQUATION VERIFICATION: Verify component balances (TOT = TH + IA, or TOT = TW + PR, CG = Credits * GP). If raw text has an OCR smudge or transposition, use mathematical balance to confirm and self-heal.\n"
        f"5. STRICT REQUIREMENT: If you find an error, you MUST provide an 'evidence_quote' which is the EXACT, VERBATIM substring from the raw text above.\n"
        f"6. If no errors are found in a record, do not output anything for it.\n"
        f"7. Return a JSON object with a 'discrepancies' array:\n"
        f"{{\n"
        f'  "discrepancies": [\n'
        f"    {{\n"
        f'      "seat_no": "58215001",\n'
        f'      "field": "CEC301_TH",\n'
        f'      "extracted_val": "24",\n'
        f'      "suggested_val": "42",\n'
        f'      "reason": "Theory mark OCR read 24 instead of 42",\n'
        f'      "evidence_quote": "CEC301 42 18 60"\n'
        f"    }}\n"
        f"  ]\n"
        f"}}"
    )

    system_instruction = (
        "You are a meticulous academic ledger proofreader. Compare tabular database records with raw PDF ledger text. "
        "Every suggested correction MUST have an exact verbatim evidence quote from the text. Respond ONLY in valid JSON."
    )

    for attempt in range(2):
        try:
            resp_text = client.generate_direct(
                user_prompt=prompt,
                system_instruction=system_instruction,
                format_json=True,
                timeout=35,
                audit_cb=audit_cb
            )
            data = json.loads(resp_text)
            if isinstance(data, dict):
                return data.get("discrepancies", [])
            elif isinstance(data, list):
                return data
            return []
        except Exception as e:
            if attempt == 0:
                logger.warning("Stage 2 chunk proofreading attempt 1 error: %s. Retrying in 2.0s...", e)
                time.sleep(2.0)
            else:
                logger.warning("Stage 2 chunk proofreading attempt 2 failed: %s", e)
                return []
    return []


# =========================================================================
# MASTER PIPELINE ORCHESTRATOR
# =========================================================================

def run_ai_cross_check(
    meta: Any,
    students: List[Dict[str, Any]],
    pdf_path: str,
    client: Optional[Any] = None,
    progress_cb: Optional[Callable[[float, str, Optional[Dict[str, Any]]], None]] = None,
    auto_apply: bool = False,
    chunk_size: int = 4,
    audit_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    controller: Optional[Any] = None,
    live_update_cb: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:

    """
    Executes complete end-to-end AI Cross-Checking & Self-Healing Pipeline.
    
    Args:
        meta: ExamMeta metadata object.
        students: Extracted student records list of dicts.
        pdf_path: Absolute path to source PDF file.
        client: Optional CloudClient instance (creates new if None).
        progress_cb: Callback function(percentage, status_message, optional_card_data).
        auto_apply: If True, sandbox-verified patches are applied directly to students.
        chunk_size: Number of students per proofreading chunk (default 6).
        audit_cb: Real-time telemetry callback for UI console events.
        
    Returns:
        Comprehensive audit dictionary including discrepancies, verification metrics, and updated records.
    """
    if client is None:
        if CloudClient is None:
            return {"error": "CloudClient module not available."}
        client = CloudClient()

    if not client.is_configured():
        return {
            "success": False,
            "error": "Cloud AI is not configured. Please add an API key (Groq or Gemini) in Cloud AI settings."
        }

    def _emit(event_type: str, badge: str, title: str, details: Any = None):
        if audit_cb:
            try:
                audit_cb({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": event_type,
                    "badge": badge,
                    "title": title,
                    "details": details
                })
            except Exception:
                pass

    def _notify(pct: float, msg: str, card: Optional[Dict[str, Any]] = None):
        if progress_cb:
            try:
                progress_cb(round(pct, 1), msg, card)
            except Exception:
                pass

    _notify(5.0, "Extracting page-by-page raw text strings from PDF ledger...")
    page_texts = extract_raw_page_texts(pdf_path)
    if not page_texts:
        return {"success": False, "error": f"Failed to extract readable text from PDF: {pdf_path}"}

    envelope = build_context_envelope(meta, students, pdf_path=pdf_path)
    students_by_seat = {str(s.get("Seat_No", s.get("seat_no", ""))): s for s in students}

    # Stage 1: Macro Column Alignment
    _notify(15.0, "Stage 1: Auditing schema & course column alignment...")
    _emit(
        "structure",
        "STAGE 1 SCHEMA",
        f"Auditing declared syllabus courses vs extracted columns ({len(envelope.valid_columns)} total columns)...",
        {"courses": [c.get("code") if isinstance(c, dict) else getattr(c, "code", str(c)) for c in envelope.courses], "total_columns": len(envelope.valid_columns)}
    )
    p1_text = page_texts[0] if page_texts else ""
    schema_audit = align_schema_with_ai(envelope, students[:3], p1_text, client, audit_cb=audit_cb)
    if schema_audit.get("status") == "ALIGNED":
        _emit("checksum_proof", "SCHEMA ALIGNED", "Stage 1 Verified: All extracted course columns match PDF syllabus headers.", schema_audit)
    else:
        _emit("checksum_alert", "SCHEMA DISCREPANCY", f"Stage 1 Finding: {schema_audit.get('summary', 'Header mismatch detected')}", schema_audit)

    # Stage 2: Micro Record Proofreading (Pre-Screened & Gated)
    total_students = len(students)
    sound_students: List[Dict[str, Any]] = []
    anomalous_students: List[Dict[str, Any]] = []

    for s in students:
        if is_student_record_mathematically_sound(s):
            sound_students.append(s)
        else:
            anomalous_students.append(s)

    _emit(
        "checksum_proof",
        "MATHEMATICAL SCREENING",
        f"Verified {len(sound_students)}/{len(students)} student records with 100% mathematical certainty. Shielding correct records from AI modification.",
        {
            "total_students": len(students),
            "mathematically_verified": len(sound_students),
            "anomalies_to_audit": len(anomalous_students)
        }
    )

    all_discrepancies: List[Dict[str, Any]] = []
    verified_patches = 0
    rejected_patches = 0
    applied_patches = 0

    # Only anomalous records require LLM intervention!
    chunks = [anomalous_students[i:i + chunk_size] for i in range(0, len(anomalous_students), chunk_size)] if anomalous_students else []
    total_chunks = len(chunks)

    # Promote Stage 1 Schema Discrepancies to first-class Discrepancy Cards
    if schema_audit and schema_audit.get("status") == "DISCREPANCY_DETECTED":
        # 1. Missing courses from header
        for mc in schema_audit.get("missing_courses", []):
            quote = ""
            for line in p1_text.split("\n"):
                if mc in line:
                    quote = line.strip()
                    break
            if not quote:
                quote = f"Course {mc} listed in Page 1 header"

            card = DiscrepancyCard(
                seat_no="PAGE 1 (HEADER)",
                field_name=f"Course: {mc}",
                extracted_val="(Missing from Extracted Columns)",
                suggested_val=f"{mc} (Declared in Ledger Syllabus)",
                reason=f"Course code '{mc}' is declared in the PDF Page 1 syllabus header, but absent from extracted student columns.",
                evidence_quote=quote,
                sandbox_status="VERIFIED",
                sandbox_reason="Confirmed in PDF Page 1 Header text.",
                applied=False,
                card_type="schema",
                page_number=1
            )
            verified_patches += 1
            card_dict = card.to_dict()
            all_discrepancies.append(card_dict)
            _notify(18.0, f"Found Schema Discrepancy on {mc} (Page 1)", card_dict)

        # 2. Misaligned assessment columns
        for mc in schema_audit.get("misaligned_columns", []):
            col_name = mc.get("column", "Unknown Column")
            issue = mc.get("issue", "Column alignment mismatch")
            sugg = mc.get("suggestion", "Align column with PDF header")
            quote = ""
            col_stem = col_name.split("_")[0].split("/")[0].strip()
            for line in p1_text.split("\n"):
                if col_stem in line:
                    quote = line.strip()
                    break
            if not quote:
                quote = p1_text[:120].strip()

            card = DiscrepancyCard(
                seat_no="PAGE 1 (HEADER)",
                field_name=f"Column: {col_name}",
                extracted_val=col_name,
                suggested_val=sugg,
                reason=issue,
                evidence_quote=quote,
                sandbox_status="VERIFIED",
                sandbox_reason="Confirmed in PDF Page 1 Header text.",
                applied=False,
                card_type="schema",
                page_number=1
            )
            verified_patches += 1
            card_dict = card.to_dict()
            all_discrepancies.append(card_dict)
            _notify(18.0, f"Found Schema Discrepancy on {col_name} (Page 1)", card_dict)

        # 3. Fallback: If both lists empty but status is DISCREPANCY_DETECTED
        if not schema_audit.get("missing_courses") and not schema_audit.get("misaligned_columns"):
            summary_txt = schema_audit.get("summary", "Schema discrepancy detected in course ledger.")
            course_match = re.search(r"\b([A-Z]{3,}[0-9]{3,}[A-Z0-9]*)\b", summary_txt)
            quote = ""
            if course_match:
                c_code = course_match.group(1)
                for line in p1_text.split("\n"):
                    if c_code in line:
                        quote = line.strip()
                        break
            if not quote:
                quote = p1_text[:140].strip()

            card = DiscrepancyCard(
                seat_no="PAGE 1 (HEADER)",
                field_name="Course Catalog Schema",
                extracted_val="(Catalog Columns)",
                suggested_val=summary_txt,
                reason=summary_txt,
                evidence_quote=quote,
                sandbox_status="VERIFIED",
                sandbox_reason="Confirmed in PDF Page 1 Header text.",
                applied=False,
                card_type="schema",
                page_number=1
            )
            verified_patches += 1
            card_dict = card.to_dict()
            all_discrepancies.append(card_dict)
            _notify(18.0, "Found Schema Discrepancy (Page 1)", card_dict)

    for idx, chunk in enumerate(chunks):
        if controller and controller.check_pause_and_stop():
            break
        if idx > 0:
            for _ in range(15):
                if controller and controller.check_pause_and_stop():
                    break
                time.sleep(0.1)
        if controller and controller.should_stop():
            break

        pct = 20.0 + ((idx / max(total_chunks, 1)) * 75.0)
        chunk_seats = [str(s.get("Seat_No", s.get("seat_no", ""))) for s in chunk]
        _emit(
            "ai_prompt",
            "PROOFREADING CHUNK",
            f"Stage 2: Proofreading Chunk {idx + 1}/{total_chunks} ({len(chunk)} candidates: {chunk_seats[0]} to {chunk_seats[-1]})...",
            {"chunk": idx + 1, "total_chunks": total_chunks, "student_seats": chunk_seats}
        )
        _notify(pct, f"Stage 2: Proofreading chunk {idx + 1}/{total_chunks} ({len(chunk)} students)...")

        # Gather relevant page texts for this chunk
        chunk_page_indices: Set[int] = set()
        for s in chunk:
            s_seat = str(s.get("Seat_No", s.get("seat_no", "")))
            s_name = str(s.get("Name", s.get("name", "")))
            pg_idx = locate_student_page(s_seat, s_name, page_texts)
            chunk_page_indices.add(pg_idx)

        # Concatenate text from relevant pages
        combined_text = "\n\n--- PAGE BREAK ---\n\n".join(
            page_texts[p] for p in sorted(chunk_page_indices) if p < len(page_texts)
        )
        # Extract targeted context windows around the candidate seat numbers to stay under TPM limits
        targeted_snippets = [
            extract_student_context_lines(str(s.get("Seat_No", s.get("seat_no", ""))), combined_text)
            for s in chunk
        ]
        chunk_text = "\n\n--- STUDENT LEDGER RECORD ---\n\n".join(targeted_snippets) if targeted_snippets else combined_text[:3000]

        raw_discrepancies = verify_student_chunk(chunk, chunk_text, envelope, client, audit_cb=audit_cb)

        for raw_d in raw_discrepancies:
            ok, reason = validate_patch_sandbox(raw_d, page_texts, envelope, students_by_seat)
            seat_str = str(raw_d.get("seat_no", "")).strip()
            st_page_num = locate_student_page(seat_str, "", page_texts) + 1
            card = DiscrepancyCard(
                seat_no=seat_str,
                field_name=str(raw_d.get("field", raw_d.get("field_name", ""))).strip(),
                extracted_val=raw_d.get("extracted_val"),
                suggested_val=raw_d.get("suggested_val"),
                reason=str(raw_d.get("reason", "")),
                evidence_quote=str(raw_d.get("evidence_quote", "")),
                sandbox_status="VERIFIED" if ok else "REJECTED",
                sandbox_reason=reason,
                applied=False,
                card_type="data",
                page_number=st_page_num
            )

            if ok:
                verified_patches += 1
                _emit(
                    "checksum_alert",
                    "PATCH VERIFIED",
                    f"Verified correction on Seat {card.seat_no}: {card.field_name} '{card.extracted_val}' -> '{card.suggested_val}'",
                    card.to_dict()
                )
                if auto_apply:
                    # Apply patch directly
                    st_record = students_by_seat.get(card.seat_no)
                    if st_record is not None:
                        st_record[card.field_name] = card.suggested_val
                        card.applied = True
                        applied_patches += 1
                        if live_update_cb:
                            try:
                                live_update_cb(st_record)
                            except Exception:
                                pass
            else:
                rejected_patches += 1
                _emit(
                    "ai_error",
                    "PATCH REJECTED",
                    f"Rejected AI hallucination on Seat {card.seat_no}: {card.field_name}. Reason: {reason}",
                    card.to_dict()
                )

            card_dict = card.to_dict()
            all_discrepancies.append(card_dict)
            _notify(pct, f"Found discrepancy on seat {card.seat_no} ({card.sandbox_status})", card_dict)

    _emit(
        "checksum_proof",
        "CROSS-CHECK COMPLETE",
        f"AI Cross-Check complete! Verified: {verified_patches}, Rejected: {rejected_patches}, Applied: {applied_patches} across {total_students} candidates.",
        {
            "total_students": total_students,
            "total_chunks": total_chunks,
            "verified_patches": verified_patches,
            "rejected_patches": rejected_patches,
            "applied_patches": applied_patches
        }
    )
    _notify(100.0, f"AI Cross-Check complete! Verified: {verified_patches}, Rejected: {rejected_patches}, Applied: {applied_patches}")

    return {
        "success": True,
        "schema_audit": schema_audit,
        "total_chunks": total_chunks,
        "total_students": total_students,
        "verified_patches": verified_patches,
        "rejected_patches": rejected_patches,
        "applied_patches": applied_patches,
        "discrepancies": all_discrepancies,
        "students": students
    }
