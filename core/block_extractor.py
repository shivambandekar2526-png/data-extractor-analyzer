"""
Universal Block-by-Block Gazette Extraction Pipeline
=====================================================
"Slice & Stitch" Architecture — works on ANY gazette from ANY institution.

7-stage pipeline:
  Stage 0: Page Preparation (render + text-layer extraction)
  Stage 1: Document Fingerprint Discovery (1 Vision AI call on Page 1)
  Stage 2: Header Band Capture (deterministic crop)
  Stage 3: Page Segmentation (deterministic seat-ID search)
  Stage 4: Block Extraction (1 Vision AI call per student)
  Stage 5: Reconstruction & Math Validation (zero AI)
  Stage 6: Targeted Re-extraction (1 AI call per conflict, max 1 retry)

Design Rule:
  - Vision AI = extractor (reads one student row at a time)
  - DocumentContract = schema (discovered from Page 1, never hardcoded)
  - Math validation = auditor (flags conflicts, never overwrites)
"""

import concurrent.futures
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

try:
    from ..models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord, normalize_course_code
    from .checksum_verifier import safe_float, verify_student
    from .cloud_client import CloudClient
    from .normalizer import normalize_head, normalize_identifier, normalize_student_name, strip_ledger_boilerplate
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord, normalize_course_code
        from data_extractor_AI.core.checksum_verifier import safe_float, verify_student
        from data_extractor_AI.core.cloud_client import CloudClient
        from data_extractor_AI.core.normalizer import normalize_head, normalize_identifier, normalize_student_name, strip_ledger_boilerplate
    except Exception:
        from models import Course, CourseCatalog, CourseResult, ExamMeta, StudentRecord, normalize_course_code
        from core.checksum_verifier import safe_float, verify_student
        from core.cloud_client import CloudClient
        from core.normalizer import normalize_head, normalize_identifier, normalize_student_name, strip_ledger_boilerplate

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[float, str], None]]
AuditCallback = Optional[Callable[[Dict[str, Any]], None]]


# ─────────────────────────────────────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PageData:
    """Rendered page image + embedded text layer."""
    page_index: int
    image_bytes: bytes          # JPEG at render DPI
    text_layer: str             # Raw text from PDF stream (may be empty for scans)


@dataclass
class DocumentContract:
    """
    Everything discovered about a gazette from Page 1.
    This is the ONLY place assumptions live — and they are all dynamic.
    """
    institution: str = ""
    exam_title: str = ""
    semester: str = ""

    # Grading
    grading_scale: Dict[str, float] = field(default_factory=dict)   # {"O": 10, "A+": 9, ...}
    max_gpa: float = 10.0

    # Candidate identification
    candidate_id_label: str = "Seat_No"                # "Seat_No", "USN", "Roll_No"
    candidate_id_regex: str = r"\b[0-9]{7,8}\b"        # discovered pattern
    secondary_id_label: str = "PRN"                    # "PRN", "Enrollment_No"
    secondary_id_regex: str = r"\b[0-9]{16}\b"

    # Courses
    courses: List[Dict[str, Any]] = field(default_factory=list)

    # Header band pixel location (percentage of page height)
    header_y_start_pct: float = 0.0
    header_y_end_pct: float = 25.0

    # Conventions
    female_indicator: str = "/"
    absent_tokens: List[str] = field(default_factory=lambda: ["--", "AB", "ABS"])
    grace_tokens: List[str] = field(default_factory=lambda: ["#", "*", "+"])
    diploma_indicator: str = "DIPLOMA"

    # History
    has_history: bool = False
    history_labels: List[str] = field(default_factory=list)       # ["SEM-I", "SEM-II", ...]
    students_per_page: int = 3


@dataclass
class StudentBlock:
    """A single student's bounding region on a page."""
    page_index: int
    seat_hint: Optional[str]    # Seat/Roll/USN detected from text layer (may be None)
    y_start: int                # Top pixel coordinate in rendered image
    y_end: int                  # Bottom pixel coordinate


@dataclass
class ComposedBlock:
    """A student block image ready for Vision AI extraction."""
    page_index: int
    block_index: int
    seat_hint: Optional[str]
    image_bytes: bytes          # Header band + student row splice
    text_hint: str              # Text layer excerpt for this block region


@dataclass
class Conflict:
    """A validation conflict flagged by the math auditor."""
    student_index: int
    seat_no: str
    course_code: str
    conflict_type: str          # "COMPONENT_SUM", "GRADE_GP", "CG_PRODUCT", "SGPI_MISMATCH"
    detail: str
    block: Optional[ComposedBlock] = None   # Original block for re-extraction


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 0: Page Preparation
# ─────────────────────────────────────────────────────────────────────────────

def prepare_pages(pdf_path: str, dpi: int = 200) -> List[PageData]:
    """Render every PDF page to JPEG and extract text layers."""
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(dpi=dpi)
        image_bytes = pix.tobytes("jpeg")
        text_layer = doc[i].get_text("text")
        pages.append(PageData(
            page_index=i,
            image_bytes=image_bytes,
            text_layer=text_layer
        ))
    doc.close()
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: Document Fingerprint Discovery (1 Vision AI Call)
# ─────────────────────────────────────────────────────────────────────────────

FINGERPRINT_SYSTEM = (
    "You are an expert academic document analyzer. Inspect this examination "
    "result page and discover its complete schema. Do not assume any specific "
    "university, college, or format. Describe exactly what you see. "
    "Return pure valid JSON."
)

FINGERPRINT_PROMPT = (
    "Analyze this examination result page (Page 1) and discover the complete "
    "document schema.\n\n"
    "Return JSON with this exact structure:\n"
    "{\n"
    '  "institution": "Name of university or college",\n'
    '  "exam_title": "Full examination title as printed",\n'
    '  "semester": "Semester label (e.g. VIII, VII, IV, 2nd Year)",\n'
    '  "grading_scale": {\n'
    '    "description": "e.g. 10-point CGPA, 4-point GPA, percentage-based",\n'
    '    "grade_points": {"O": 10, "A+": 9, "A": 8, "B+": 7, "B": 6, "C": 5, "P": 4, "F": 0},\n'
    '    "max_gpa": 10\n'
    "  },\n"
    '  "candidate_id": {\n'
    '    "label": "Seat_No or USN or Roll_No or Registration_No",\n'
    '    "format": "numeric or alphanumeric",\n'
    '    "example": "e.g. 1033195 or 1MS21CS045",\n'
    '    "regex_hint": "e.g. [0-9]{7} or [A-Z0-9]{10}"\n'
    "  },\n"
    '  "secondary_id": {\n'
    '    "label": "PRN or Enrollment_No or ABC_ID or null",\n'
    '    "regex_hint": "e.g. [0-9]{16} or null"\n'
    "  },\n"
    '  "courses": [\n'
    "    {\n"
    '      "code": "Course code (strip serial prefix, e.g. CSC801 not 01.CSC801)",\n'
    '      "name": "Full subject name",\n'
    '      "type": "Theory or Lab or Project or Seminar or Audit",\n'
    '      "heads": ["TH", "IA", "TOT"],\n'
    '      "max_marks": {"TH": 80, "IA": 20, "TOT": 100},\n'
    '      "credits": 3\n'
    "    }\n"
    "  ],\n"
    '  "header_band": {\n'
    '    "y_start_percent": 15,\n'
    '    "y_end_percent": 35\n'
    "  },\n"
    '  "conventions": {\n'
    '    "female_indicator": "leading slash / or (F) or none",\n'
    '    "absent_tokens": ["--", "AB", "ABS"],\n'
    '    "grace_tokens": ["#", "*", "+"],\n'
    '    "diploma_indicator": "DIPLOMA or D.S.Y. or none"\n'
    "  },\n"
    '  "has_history": true,\n'
    '  "history_labels": ["SEM-I", "SEM-II"],\n'
    '  "students_per_page": 3\n'
    "}\n"
)


def discover_document_contract(
    page: PageData,
    client: CloudClient,
    audit_cb: AuditCallback = None
) -> DocumentContract:
    """
    Stage 1: Send Page 1 image to Vision AI and discover the complete
    document schema. Zero hardcoded assumptions.
    """
    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_fingerprint_start",
                "badge": "STAGE 1 FINGERPRINT",
                "title": "Discovering document schema from Page 1 (universal mode)...",
                "details": {"image_size_kb": len(page.image_bytes) // 1024}
            })
        except Exception:
            pass

    text_hint = strip_ledger_boilerplate(page.text_layer)[:3000] if page.text_layer else ""
    prompt = FINGERPRINT_PROMPT
    if text_hint:
        prompt += f"\nFALLBACK TEXT HINT FROM PAGE 1:\n{text_hint}\n"

    t0 = time.time()
    resp = client.generate_direct(
        user_prompt=prompt,
        system_instruction=FINGERPRINT_SYSTEM,
        format_json=True,
        timeout=60,
        temperature=0.0,
        audit_cb=audit_cb,
        image_bytes=page.image_bytes,
        image_mime="image/jpeg"
    )
    dur = time.time() - t0

    try:
        data = json.loads(resp)
    except Exception as e:
        logger.error("Failed to parse fingerprint response: %s", e)
        data = {}

    # Build DocumentContract from response
    contract = DocumentContract()
    contract.institution = data.get("institution", "")
    contract.exam_title = data.get("exam_title", "")
    contract.semester = data.get("semester", "")

    # Grading scale
    gs = data.get("grading_scale", {})
    if isinstance(gs, dict):
        gp = gs.get("grade_points", {})
        if isinstance(gp, dict):
            contract.grading_scale = {k.strip().upper(): float(v) for k, v in gp.items()}
        contract.max_gpa = float(gs.get("max_gpa", 10))

    # Candidate identification
    cid = data.get("candidate_id", {})
    if isinstance(cid, dict):
        contract.candidate_id_label = cid.get("label", "Seat_No")
        regex = cid.get("regex_hint", "")
        if regex:
            # Ensure the regex is wrapped in word boundaries
            if not regex.startswith(r"\b"):
                regex = r"\b(" + regex + r")\b"
            try:
                re.compile(regex)
                contract.candidate_id_regex = regex
            except re.error:
                logger.warning("Invalid candidate_id regex from AI: %s, using default", regex)

    sid = data.get("secondary_id", {})
    if isinstance(sid, dict):
        contract.secondary_id_label = sid.get("label", "PRN") or "PRN"
        sec_regex = sid.get("regex_hint", "")
        if sec_regex and sec_regex != "null":
            try:
                re.compile(sec_regex)
                contract.secondary_id_regex = r"\b(" + sec_regex + r")\b"
            except re.error:
                pass

    # Courses
    raw_courses = data.get("courses", [])
    if isinstance(raw_courses, list):
        contract.courses = []
        for c in raw_courses:
            if not isinstance(c, dict):
                continue
            code = normalize_course_code(c.get("code", ""))
            if not code:
                continue
            contract.courses.append({
                "code": code,
                "name": c.get("name", ""),
                "type": c.get("type", "Theory"),
                "heads": c.get("heads", []),
                "max_marks": c.get("max_marks", {}),
                "credits": c.get("credits", 0)
            })

    # Header band
    hb = data.get("header_band", {})
    if isinstance(hb, dict):
        contract.header_y_start_pct = float(hb.get("y_start_percent", 0))
        contract.header_y_end_pct = float(hb.get("y_end_percent", 25))
        # Sanity: header must be in [0, 60%] range
        contract.header_y_start_pct = max(0.0, min(60.0, contract.header_y_start_pct))
        contract.header_y_end_pct = max(contract.header_y_start_pct + 5, min(65.0, contract.header_y_end_pct))

    # Conventions
    conv = data.get("conventions", {})
    if isinstance(conv, dict):
        contract.female_indicator = conv.get("female_indicator", "/")
        contract.absent_tokens = conv.get("absent_tokens", ["--", "AB", "ABS"])
        contract.grace_tokens = conv.get("grace_tokens", ["#", "*", "+"])
        contract.diploma_indicator = conv.get("diploma_indicator", "DIPLOMA")

    # History
    contract.has_history = bool(data.get("has_history", False))
    contract.history_labels = data.get("history_labels", [])
    contract.students_per_page = int(data.get("students_per_page", 3))

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_fingerprint_done",
                "badge": "FINGERPRINT COMPLETE",
                "title": f"Schema discovered in {dur:.2f}s: {len(contract.courses)} courses, "
                         f"scale={list(contract.grading_scale.keys())[:4]}..., "
                         f"ID format={contract.candidate_id_label}",
                "details": {
                    "institution": contract.institution,
                    "semester": contract.semester,
                    "courses_count": len(contract.courses),
                    "grading_grades": list(contract.grading_scale.keys()),
                    "id_label": contract.candidate_id_label,
                    "has_history": contract.has_history,
                    "duration_sec": round(dur, 2)
                }
            })
        except Exception:
            pass

    return contract


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: Header Band Capture (Zero AI)
# ─────────────────────────────────────────────────────────────────────────────

def capture_header_band(page_image: bytes, contract: DocumentContract) -> bytes:
    """
    Crop the header band from Page 1 using the percentage-based
    coordinates discovered in Stage 1.
    """
    from PIL import Image
    img = Image.open(io.BytesIO(page_image))

    y_start = int(img.height * contract.header_y_start_pct / 100)
    y_end = int(img.height * contract.header_y_end_pct / 100)

    # Ensure minimum header height of 80px
    if y_end - y_start < 80:
        y_end = min(img.height, y_start + max(80, int(img.height * 0.15)))

    header_crop = img.crop((0, y_start, img.width, y_end))

    buf = io.BytesIO()
    header_crop.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3: Page Segmentation (Zero AI, Deterministic)
# ─────────────────────────────────────────────────────────────────────────────

def segment_page_by_search(
    pdf_path: str,
    page_index: int,
    contract: DocumentContract,
    render_dpi: int = 200
) -> List[StudentBlock]:
    """
    Strategy B (preferred): Use PyMuPDF search_for() to locate each
    candidate ID with pixel-accurate bounding boxes.
    """
    doc = fitz.open(pdf_path)
    if page_index >= len(doc):
        doc.close()
        return []

    page = doc[page_index]
    text = page.get_text("text")

    # Find candidate IDs using the discovered regex
    try:
        matches = re.findall(contract.candidate_id_regex, text)
    except re.error:
        matches = re.findall(r"\b([0-9]{7,8})\b", text)

    # Deduplicate while preserving order
    seen = set()
    seat_numbers = []
    for m in matches:
        val = m if isinstance(m, str) else m[0] if isinstance(m, tuple) else str(m)
        if val not in seen:
            seen.add(val)
            seat_numbers.append(val)

    scale = render_dpi / 72.0
    blocks = []

    for i, seat in enumerate(seat_numbers):
        rects = page.search_for(seat)
        if not rects:
            continue
        r = rects[0]

        # Student row starts ~15pt above the seat number
        y_start = int(max(0, (r.y0 - 15)) * scale)

        # Student row ends at the next seat number or page bottom
        if i + 1 < len(seat_numbers):
            next_rects = page.search_for(seat_numbers[i + 1])
            if next_rects:
                y_end = int(max(0, (next_rects[0].y0 - 5)) * scale)
            else:
                y_end = int((r.y1 + 120) * scale)
        else:
            y_end = int(page.rect.height * scale)

        blocks.append(StudentBlock(
            page_index=page_index,
            seat_hint=seat,
            y_start=y_start,
            y_end=min(y_end, int(page.rect.height * scale))
        ))

    doc.close()
    return blocks


def segment_page_by_text(
    page_text: str,
    page_height_px: int,
    page_index: int,
    contract: DocumentContract
) -> List[StudentBlock]:
    """
    Strategy A: Find candidate IDs in text layer and estimate Y positions
    from character offset proportions.
    """
    try:
        matches = list(re.finditer(contract.candidate_id_regex, page_text))
    except re.error:
        matches = list(re.finditer(r"\b([0-9]{7,8})\b", page_text))

    if not matches:
        return []

    total_chars = max(len(page_text), 1)
    blocks = []

    for i, m in enumerate(matches):
        seat_val = m.group() if not m.groups() else m.group(1)
        # Estimate Y position from character offset proportion
        y_ratio = m.start() / total_chars
        y_start = max(0, int(page_height_px * y_ratio) - 20)

        if i + 1 < len(matches):
            next_ratio = matches[i + 1].start() / total_chars
            y_end = int(page_height_px * next_ratio)
        else:
            y_end = page_height_px

        blocks.append(StudentBlock(
            page_index=page_index,
            seat_hint=seat_val,
            y_start=y_start,
            y_end=y_end
        ))

    return blocks


def segment_page_fixed(
    page_height_px: int,
    header_end_y: int,
    page_index: int,
    students_per_page: int
) -> List[StudentBlock]:
    """
    Strategy C (fallback): Divide the student region into equal-height strips.
    Used for scanned PDFs with no text layer.
    """
    usable_height = page_height_px - header_end_y
    if usable_height <= 0 or students_per_page <= 0:
        return []

    strip_height = usable_height // students_per_page
    return [
        StudentBlock(
            page_index=page_index,
            seat_hint=None,
            y_start=header_end_y + i * strip_height,
            y_end=header_end_y + (i + 1) * strip_height
        )
        for i in range(students_per_page)
    ]


def segment_page(
    page: PageData,
    contract: DocumentContract,
    pdf_path: str,
    render_dpi: int = 200
) -> List[StudentBlock]:
    """
    Master segmentation dispatcher: tries Strategy B (search_for),
    falls back to A (text offset), then C (fixed height).
    """
    # Strategy B: pixel-accurate PyMuPDF search
    blocks = segment_page_by_search(pdf_path, page.page_index, contract, render_dpi)
    if blocks:
        return blocks

    # Strategy A: text-layer offset estimation
    if page.text_layer.strip():
        from PIL import Image
        img = Image.open(io.BytesIO(page.image_bytes))
        blocks = segment_page_by_text(
            page.text_layer, img.height, page.page_index, contract
        )
        if blocks:
            return blocks

    # Strategy C: fixed-height fallback
    from PIL import Image
    img = Image.open(io.BytesIO(page.image_bytes))
    header_end_y = int(img.height * contract.header_y_end_pct / 100)
    return segment_page_fixed(
        img.height, header_end_y, page.page_index, contract.students_per_page
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 4: Block Extraction (1 Vision AI Call Per Student)
# ─────────────────────────────────────────────────────────────────────────────

def compose_block_image(
    page_image: bytes,
    header_band: bytes,
    block: StudentBlock
) -> bytes:
    """
    Splice header band on top + cropped student row on bottom.
    Result: a small image showing column headers directly above the marks.
    """
    from PIL import Image

    page_img = Image.open(io.BytesIO(page_image))
    header_img = Image.open(io.BytesIO(header_band))

    # Crop the student row
    y_end_clamped = min(block.y_end, page_img.height)
    y_start_clamped = max(0, min(block.y_start, y_end_clamped - 10))
    student_crop = page_img.crop((0, y_start_clamped, page_img.width, y_end_clamped))

    # Resize header to match student row width
    if header_img.width != student_crop.width:
        ratio = student_crop.width / max(header_img.width, 1)
        new_h = max(1, int(header_img.height * ratio))
        header_img = header_img.resize((student_crop.width, new_h))

    # Stitch vertically
    combined = Image.new(
        "RGB",
        (student_crop.width, header_img.height + student_crop.height),
        (255, 255, 255)
    )
    combined.paste(header_img, (0, 0))
    combined.paste(student_crop, (0, header_img.height))

    buf = io.BytesIO()
    combined.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def build_block_system_instruction(contract: DocumentContract) -> str:
    """
    Build the system instruction for block extraction.
    Sent once and cached by Gemini API for all subsequent calls.
    """
    # Compressed course list
    course_lines = []
    for idx, c in enumerate(contract.courses, 1):
        heads_str = ",".join(c.get("heads", [])) or "AUDIT"
        course_lines.append(
            f"{idx}.{c['code']}[{heads_str}]"
        )
    courses_compact = " ".join(course_lines)

    # Grading scale compact
    if contract.grading_scale:
        grade_str = ", ".join(
            f"{g}={int(p) if float(p).is_integer() else p}"
            for g, p in contract.grading_scale.items()
        )
    else:
        grade_str = "Read grades exactly as printed"

    # History labels
    if contract.has_history and contract.history_labels:
        hist_str = ", ".join(contract.history_labels)
        hist_directive = (
            f"Extract semester history using these EXACT labels: {hist_str}. "
            "Map each label's SGPI to its corresponding key in 'history'. "
            "If a label is missing for a student (e.g. Diploma without SEM-I), "
            "OMIT that key — do NOT shift other semesters into its slot."
        )
    elif contract.has_history:
        hist_directive = (
            "Extract any prior semester SGPIs if printed. Use labels like "
            "'sem1', 'sem2', etc. Never shift values between semester slots."
        )
    else:
        hist_directive = "No history expected. Omit 'history' or leave empty."

    return (
        "You are transcribing ONE student's examination result from a cropped image.\n"
        "The TOP portion shows column headers (course codes and assessment heads).\n"
        "The BOTTOM portion shows one student's marks row.\n\n"
        f"COURSE ORDER: {courses_compact}\n\n"
        "RULES:\n"
        "1. Map each number in the student row to the column header DIRECTLY above it.\n"
        "   Read left-to-right. The i-th group of numbers = the i-th course above.\n"
        "2. NEVER shift values between columns. If a cell is empty, output \"\".\n"
        "3. Output ONLY the assessment heads declared for each course.\n"
        "   If a course has heads [TW,PR,TOT], output ONLY those. NEVER add TH.\n"
        "4. Transcribe grades and grade points EXACTLY as printed. Do NOT recalculate.\n"
        f"5. GRADING SCALE: {grade_str}. AB/ABS = Absent.\n"
        f"6. {hist_directive}\n"
        "7. For absent marks (dashes, AB, ABS): marks=\"AB\", grade=\"AB\", gp=\"0\".\n"
        "8. For grace marks (e.g. 36+4#): output the final total (40), note in remark.\n"
        "9. Return pure valid JSON.\n"
    )


def build_block_user_prompt(block: ComposedBlock, contract: DocumentContract) -> str:
    """Build the minimal per-student user prompt."""
    seat_hint = f"Expected ID: {block.seat_hint}\n" if block.seat_hint else ""
    text_hint = ""
    if block.text_hint:
        # Truncate to 800 chars to minimize tokens
        text_hint = f"\nTEXT HINT: {block.text_hint[:800]}\n"

    return (
        f"Extract this student's result.{text_hint}\n"
        f"{seat_hint}"
        "Return JSON:\n"
        "{\n"
        f'  "id": "candidate {contract.candidate_id_label}",\n'
        '  "name": "full name (strip leading /)",\n'
        '  "gender": "M or F",\n'
        '  "type": "Regular or Diploma",\n'
        f'  "secondary_id": "{contract.secondary_id_label} if visible",\n'
        '  "status": "PASS/FAIL/KT/ATKT",\n'
        '  "remark": "any special remark or empty",\n'
        '  "total": "total marks",\n'
        '  "credits": "credits earned",\n'
        '  "total_cg": "sum of credit points",\n'
        '  "sgpi": "SGPI value",\n'
        '  "cgpi": "CGPI if present",\n'
        '  "courses": [\n'
        '    {"code":"...","marks":{"HEAD":"val"},"grade":"...","gp":"...","cr":"...","cg":"..."}\n'
        "  ],\n"
        '  "history": {"label": "sgpi_value"}\n'
        "}\n"
    )


def parse_block_response(
    resp_text: str,
    contract: DocumentContract,
    page_text: str = ""
) -> Optional[StudentRecord]:
    """Parse a single block's JSON response into a StudentRecord."""
    try:
        data = json.loads(resp_text)
    except Exception:
        return None

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None

    # Extract candidate ID
    raw_id = str(data.get("id") or data.get("seat_no") or data.get("seat") or "").strip()
    if not raw_id:
        return None

    # Student name
    raw_name = str(data.get("name") or data.get("student_name") or "").strip()
    norm_name_info = normalize_student_name(raw_name)
    gender = str(data.get("gender") or "").strip().upper()
    if not gender:
        gender = norm_name_info.get("gender", "M")

    # Secondary ID
    secondary_id = normalize_identifier(data.get("secondary_id") or data.get("prn") or "")

    # Result status
    status = str(data.get("status") or data.get("result_status") or "").strip().upper()

    # Build course results
    course_results: List[CourseResult] = []
    raw_courses = data.get("courses", [])
    if isinstance(raw_courses, list):
        for rc in raw_courses:
            if not isinstance(rc, dict):
                continue
            c_code = normalize_course_code(rc.get("code", ""))
            if not c_code:
                continue

            # Build marks dict with normalized heads
            raw_marks = rc.get("marks", {})
            marks: Dict[str, str] = {}
            if isinstance(raw_marks, dict):
                for h, v in raw_marks.items():
                    norm_h = normalize_head(h.strip().upper()) if h else h
                    marks[norm_h] = str(v).strip() if v is not None else ""

            course_results.append(CourseResult(
                course_code=c_code,
                course_name="",
                marks=marks,
                grade=str(rc.get("grade", "")).strip(),
                grade_point=str(rc.get("gp") or rc.get("grade_point", "")).strip(),
                credits=str(rc.get("cr") or rc.get("credits", "")).strip(),
                credit_points=str(rc.get("cg") or rc.get("credit_points", "")).strip()
            ))

    # Populate course names from contract
    code_to_name = {c["code"]: c["name"] for c in contract.courses}
    for cr in course_results:
        if not cr.course_name:
            cr.course_name = code_to_name.get(cr.course_code, "")

    # Build history
    history: Dict[str, Dict[str, str]] = {}
    raw_hist = data.get("history", {})
    if isinstance(raw_hist, dict):
        for hk, hv in raw_hist.items():
            clean_key = hk.lower().replace("sem-", "").replace("sem_", "").replace("sem", "").replace(" ", "").strip()
            # Map roman numerals
            roman_map = {"i": "1", "ii": "2", "iii": "3", "iv": "4",
                         "v": "5", "vi": "6", "vii": "7", "viii": "8"}
            clean_key = roman_map.get(clean_key, clean_key)
            if isinstance(hv, dict):
                history[clean_key] = {k: str(v) for k, v in hv.items()}
            elif hv and str(hv).strip():
                history[clean_key] = {"sgpi": str(hv).strip()}

    return StudentRecord(
        seat_no=raw_id,
        student_name=norm_name_info.get("cleaned", raw_name),
        gender=gender,
        candidate_type=str(data.get("type") or data.get("candidate_type") or "Regular").strip(),
        prn=secondary_id,
        result_status=status,
        remark=str(data.get("remark") or "").strip(),
        total_marks=str(data.get("total") or data.get("total_marks") or "").strip(),
        credits_earned=str(data.get("credits") or data.get("credits_earned") or "").strip(),
        total_cg=str(data.get("total_cg") or "").strip(),
        sgpi=str(data.get("sgpi") or "").strip(),
        cgpi=str(data.get("cgpi") or "").strip(),
        course_results=course_results,
        history=history
    )


def extract_single_block(
    block: ComposedBlock,
    client: CloudClient,
    system_instruction: str,
    contract: DocumentContract,
    audit_cb: AuditCallback = None
) -> Optional[StudentRecord]:
    """Extract a single student block via Vision AI."""
    user_prompt = build_block_user_prompt(block, contract)

    t0 = time.time()
    try:
        resp_text = client.generate_direct(
            user_prompt=user_prompt,
            system_instruction=system_instruction,
            format_json=True,
            timeout=30,
            temperature=0.0,
            audit_cb=None,    # Suppress per-block audit noise
            image_bytes=block.image_bytes,
            image_mime="image/jpeg"
        )
    except Exception as e:
        logger.warning("Block extraction failed for block %d (seat=%s): %s",
                       block.block_index, block.seat_hint, e)
        return None
    dur = time.time() - t0

    student = parse_block_response(resp_text, contract)

    call_tokens = getattr(client, "last_call_tokens", {})
    in_tok = call_tokens.get("input_tokens", 0) or max(1, len(user_prompt) // 4 + 400)
    out_tok = call_tokens.get("output_tokens", 0) or max(1, len(resp_text) // 4)
    tot_tok = call_tokens.get("total_tokens", in_tok + out_tok)

    if student and audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_extracted",
                "badge": "BLOCK EXTRACTED",
                "title": f"Block {block.block_index}: Seat {student.seat_no} extracted in {dur:.2f}s "
                         f"({len(student.course_results)} courses)",
                "details": {
                    "seat_no": student.seat_no,
                    "name": student.student_name[:40],
                    "courses": len(student.course_results),
                    "sgpi": student.sgpi,
                    "duration_sec": round(dur, 2),
                    "tokens": {
                        "input": in_tok,
                        "output": out_tok,
                        "total": tot_tok
                    }
                }
            })
        except Exception:
            pass

    return student


def extract_all_blocks(
    blocks: List[ComposedBlock],
    client: CloudClient,
    system_instruction: str,
    contract: DocumentContract,
    max_workers: int = 5,
    progress_cb: ProgressCallback = None,
    audit_cb: AuditCallback = None
) -> List[Optional[StudentRecord]]:
    """
    Extract all student blocks in parallel with up to max_workers
    concurrent API calls. Rate-limit-aware with semaphore.
    """
    results: List[Optional[StudentRecord]] = [None] * len(blocks)
    completed = [0]  # mutable counter for progress
    lock = threading.Lock()

    def extract_one(idx: int, block: ComposedBlock) -> Tuple[int, Optional[StudentRecord]]:
        student = extract_single_block(block, client, system_instruction, contract, audit_cb)
        with lock:
            completed[0] += 1
            if progress_cb:
                pct = 20.0 + (completed[0] / max(len(blocks), 1)) * 60.0
                progress_cb(pct, f"Extracting block {completed[0]}/{len(blocks)} "
                                 f"(Seat: {block.seat_hint or '?'})...")
        return idx, student

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(extract_one, i, b): i
            for i, b in enumerate(blocks)
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, student = future.result()
                results[idx] = student
            except Exception as e:
                logger.warning("Block extraction worker error: %s", e)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5: Reconstruction & Math Validation (Zero AI)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_num(val: Any) -> Optional[float]:
    """Parse a numeric value, handling grace marks like '40#' or '36+4#'."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("", "AB", "ABS", "--", "-"):
        return None
    # Strip trailing grace symbols
    s = re.sub(r"[#*+]+$", "", s).strip()
    # Handle additive grace: "36+4" -> 40
    if "+" in s:
        parts = s.split("+")
        try:
            return sum(float(p.strip()) for p in parts if p.strip())
        except ValueError:
            pass
    try:
        return float(s)
    except ValueError:
        return None


def validate_record(
    student: StudentRecord,
    contract: DocumentContract
) -> List[Conflict]:
    """
    Validate a single student record against the discovered grading scale
    and course schema. Returns a list of conflicts (empty = all passed).
    """
    conflicts: List[Conflict] = []
    code_to_course = {c["code"]: c for c in contract.courses}

    for cr in student.course_results:
        c_code = normalize_course_code(cr.course_code)
        course_def = code_to_course.get(c_code)
        declared_heads = course_def["heads"] if course_def else list(cr.marks.keys())

        # Rule 1: Component Sum Check
        if "TOT" in declared_heads:
            component_heads = [h for h in declared_heads if h != "TOT"]
            component_vals = [_parse_num(cr.marks.get(h)) for h in component_heads]
            total_val = _parse_num(cr.marks.get("TOT"))

            if all(v is not None for v in component_vals) and total_val is not None:
                expected_sum = sum(component_vals)
                if abs(expected_sum - total_val) > 0.5:
                    conflicts.append(Conflict(
                        student_index=-1,
                        seat_no=student.seat_no,
                        course_code=c_code,
                        conflict_type="COMPONENT_SUM",
                        detail=f"Sum of {component_heads} = {expected_sum}, but TOT = {total_val}"
                    ))

        # Rule 2: Grade ↔ GP Consistency
        grade = (cr.grade or "").strip().upper()
        gp = _parse_num(cr.grade_point)
        if grade in contract.grading_scale and gp is not None:
            expected_gp = contract.grading_scale[grade]
            if abs(expected_gp - gp) > 0.1:
                conflicts.append(Conflict(
                    student_index=-1,
                    seat_no=student.seat_no,
                    course_code=c_code,
                    conflict_type="GRADE_GP",
                    detail=f"Grade '{grade}' expects GP {expected_gp}, got {gp}"
                ))

        # Rule 3: Credits × GP = CG
        cr_val = _parse_num(cr.credits)
        cg_val = _parse_num(cr.credit_points)
        if gp is not None and cr_val is not None and cg_val is not None:
            expected_cg = cr_val * gp
            if abs(expected_cg - cg_val) > 0.5:
                conflicts.append(Conflict(
                    student_index=-1,
                    seat_no=student.seat_no,
                    course_code=c_code,
                    conflict_type="CG_PRODUCT",
                    detail=f"Credits({cr_val}) × GP({gp}) = {expected_cg}, but CG = {cg_val}"
                ))

    # Rule 4: SGPI = sum(CG) / sum(Credits)
    total_cg = sum(_parse_num(cr.credit_points) or 0 for cr in student.course_results)
    total_cr = sum(_parse_num(cr.credits) or 0 for cr in student.course_results)
    sgpi = _parse_num(student.sgpi)
    if total_cr > 0 and sgpi is not None:
        expected_sgpi = round(total_cg / total_cr, 2)
        if abs(expected_sgpi - sgpi) > 0.05:
            conflicts.append(Conflict(
                student_index=-1,
                seat_no=student.seat_no,
                course_code="OVERALL",
                conflict_type="SGPI_MISMATCH",
                detail=f"sum(CG)/sum(CR) = {expected_sgpi}, but SGPI = {sgpi}"
            ))

    return conflicts


def validate_all(
    students: List[StudentRecord],
    blocks: List[ComposedBlock],
    contract: DocumentContract
) -> List[Conflict]:
    """Validate all students and attach block references for re-extraction."""
    all_conflicts: List[Conflict] = []

    for idx, student in enumerate(students):
        if student is None:
            continue
        conflicts = validate_record(student, contract)
        for c in conflicts:
            c.student_index = idx
            if idx < len(blocks):
                c.block = blocks[idx]
        all_conflicts.extend(conflicts)

    return all_conflicts


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 6: Targeted Re-extraction (1 AI Call Per Conflict)
# ─────────────────────────────────────────────────────────────────────────────

def reextract_conflicting_block(
    conflict: Conflict,
    client: CloudClient,
    system_instruction: str,
    contract: DocumentContract,
    audit_cb: AuditCallback = None
) -> Optional[StudentRecord]:
    """
    Re-extract a student block with explicit conflict annotation
    to guide the AI's attention.
    """
    if not conflict.block:
        return None

    # Augmented prompt with conflict info
    user_prompt = (
        f"Previous extraction of this student had an error:\n"
        f"  Course: {conflict.course_code}\n"
        f"  Error: {conflict.detail}\n\n"
        f"Re-read the image VERY carefully. Focus on the column for {conflict.course_code}.\n"
        f"Extract the COMPLETE student record again with corrected values.\n\n"
        "Return JSON:\n"
        "{\n"
        f'  "id": "candidate {contract.candidate_id_label}",\n'
        '  "name": "full name",\n'
        '  "gender": "M or F",\n'
        '  "type": "Regular or Diploma",\n'
        '  "status": "PASS/FAIL/KT/ATKT",\n'
        '  "total": "total marks",\n'
        '  "credits": "credits earned",\n'
        '  "total_cg": "sum of credit points",\n'
        '  "sgpi": "SGPI value",\n'
        '  "courses": [\n'
        '    {"code":"...","marks":{"HEAD":"val"},"grade":"...","gp":"...","cr":"...","cg":"..."}\n'
        "  ],\n"
        '  "history": {"label": "sgpi_value"}\n'
        "}\n"
    )

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_reextraction",
                "badge": "RE-EXTRACTION",
                "title": f"Re-extracting Seat {conflict.seat_no} (conflict: {conflict.conflict_type} on {conflict.course_code})",
                "details": {
                    "seat_no": conflict.seat_no,
                    "course": conflict.course_code,
                    "type": conflict.conflict_type,
                    "detail": conflict.detail
                }
            })
        except Exception:
            pass

    try:
        resp = client.generate_direct(
            user_prompt=user_prompt,
            system_instruction=system_instruction,
            format_json=True,
            timeout=30,
            temperature=0.0,
            audit_cb=None,
            image_bytes=conflict.block.image_bytes,
            image_mime="image/jpeg"
        )
        call_tokens = getattr(client, "last_call_tokens", {})
        in_tok = call_tokens.get("input_tokens", 0) or max(1, len(user_prompt) // 4 + 400)
        out_tok = call_tokens.get("output_tokens", 0) or max(1, len(resp) // 4)
        tot_tok = call_tokens.get("total_tokens", in_tok + out_tok)
        if audit_cb:
            try:
                audit_cb({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "block_reextracted_success",
                    "badge": "RE-EXTRACTED",
                    "title": f"Seat {conflict.seat_no} re-extracted [Tokens: {tot_tok:,}]",
                    "details": {
                        "seat_no": conflict.seat_no,
                        "tokens": {
                            "input": in_tok,
                            "output": out_tok,
                            "total": tot_tok
                        }
                    }
                })
            except Exception:
                pass
        return parse_block_response(resp, contract)
    except Exception as e:
        logger.warning("Re-extraction failed for Seat %s: %s", conflict.seat_no, e)
        return None


def apply_resolutions(
    students: List[StudentRecord],
    conflicts: List[Conflict],
    client: CloudClient,
    system_instruction: str,
    contract: DocumentContract,
    audit_cb: AuditCallback = None
) -> int:
    """
    Re-extract conflicting blocks and apply resolutions.
    Returns the number of successful corrections.
    """
    # Group conflicts by student index (max 1 re-extraction per student)
    seen_students: set = set()
    corrections = 0

    for conflict in conflicts:
        if conflict.student_index in seen_students:
            continue
        seen_students.add(conflict.student_index)

        re_student = reextract_conflicting_block(
            conflict, client, system_instruction, contract, audit_cb
        )
        if re_student is None:
            continue

        original = students[conflict.student_index]
        if original is None:
            continue

        # Count math errors in both versions
        orig_errors = len(validate_record(original, contract))
        new_errors = len(validate_record(re_student, contract))

        if new_errors < orig_errors:
            students[conflict.student_index] = re_student
            corrections += 1
            logger.info(
                "Seat %s: Re-extraction improved (%d errors → %d errors)",
                conflict.seat_no, orig_errors, new_errors
            )
        else:
            logger.debug(
                "Seat %s: Re-extraction did not improve (%d → %d), keeping original",
                conflict.seat_no, orig_errors, new_errors
            )

    return corrections


# ─────────────────────────────────────────────────────────────────────────────
# Build ExamMeta & CourseCatalog from Contract
# ─────────────────────────────────────────────────────────────────────────────

def build_exam_meta(contract: DocumentContract) -> ExamMeta:
    """Convert a DocumentContract into the standard ExamMeta structure."""
    catalog = CourseCatalog(courses=[
        Course(
            code=c["code"],
            name=c.get("name", ""),
            heads=c.get("heads", []),
            max_marks=c.get("max_marks", {}),
            credits=float(c.get("credits", 0))
        )
        for c in contract.courses
    ])

    # Parse semester numeral
    sem_str = contract.semester.upper().strip()
    roman_to_num = {
        "I": 1, "II": 2, "III": 3, "IV": 4,
        "V": 5, "VI": 6, "VII": 7, "VIII": 8
    }
    sem_num = roman_to_num.get(sem_str, 0)
    if not sem_num:
        m = re.search(r"(\d+)", sem_str)
        if m:
            sem_num = int(m.group(1))

    return ExamMeta(
        exam_title=contract.exam_title,
        college_name=contract.institution,
        semester=contract.semester,
        semester_num=sem_num,
        scheme=f"{'%.0f' % contract.max_gpa}-point scale",
        archetype="block_extraction",
        catalog=catalog
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_block_extraction_pipeline(
    pdf_path: str,
    progress_cb: ProgressCallback = None,
    audit_cb: AuditCallback = None,
    client: Optional[CloudClient] = None,
    max_workers: int = 5,
    render_dpi: int = 200
) -> Tuple[ExamMeta, List[StudentRecord], str]:
    """
    Universal Block-by-Block Gazette Extraction Pipeline.
    Works on any gazette format from any institution.

    Returns: (ExamMeta, List[StudentRecord], archetype_string)
    """
    if client is None:
        client = CloudClient()

    if not client.is_configured():
        raise RuntimeError(
            "Cloud AI is not configured. Please add an API key (Gemini or Groq) in settings."
        )

    # ── Stage 0: Page Preparation ──
    if progress_cb:
        progress_cb(2.0, "Stage 0: Rendering PDF pages...")

    pages = prepare_pages(pdf_path, dpi=render_dpi)
    if not pages:
        raise ValueError(f"No pages in PDF: {pdf_path}")

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_pages_ready",
                "badge": "PAGES READY",
                "title": f"Rendered {len(pages)} pages at {render_dpi} DPI",
                "details": {"total_pages": len(pages), "dpi": render_dpi}
            })
        except Exception:
            pass

    # ── Stage 1: Document Fingerprint Discovery ──
    if progress_cb:
        progress_cb(5.0, "Stage 1: Discovering document schema from Page 1...")

    contract = discover_document_contract(pages[0], client, audit_cb)

    if not contract.courses:
        raise ValueError(
            "Stage 1 failed: No courses discovered from Page 1. "
            "The document may not be a recognizable examination gazette."
        )

    # ── Stage 2: Header Band Capture ──
    if progress_cb:
        progress_cb(10.0, "Stage 2: Capturing header band...")

    header_band = capture_header_band(pages[0].image_bytes, contract)

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_header_captured",
                "badge": "HEADER CAPTURED",
                "title": f"Header band cropped ({len(header_band) // 1024} KB) "
                         f"— Y range: {contract.header_y_start_pct:.0f}%-{contract.header_y_end_pct:.0f}%",
                "details": {
                    "size_kb": len(header_band) // 1024,
                    "y_start_pct": contract.header_y_start_pct,
                    "y_end_pct": contract.header_y_end_pct
                }
            })
        except Exception:
            pass

    # ── Stage 3: Page Segmentation ──
    if progress_cb:
        progress_cb(12.0, "Stage 3: Segmenting pages into student blocks...")

    all_blocks: List[ComposedBlock] = []
    block_counter = 0

    for page in pages:
        raw_blocks = segment_page(page, contract, pdf_path, render_dpi)

        for sb in raw_blocks:
            # Compose the block image (header + student row)
            block_image = compose_block_image(page.image_bytes, header_band, sb)

            # Extract text hint for this block's region
            text_hint = ""
            if page.text_layer and sb.seat_hint:
                # Find the text around this seat number
                seat_pos = page.text_layer.find(sb.seat_hint)
                if seat_pos != -1:
                    text_hint = page.text_layer[seat_pos:seat_pos + 600]

            all_blocks.append(ComposedBlock(
                page_index=page.page_index,
                block_index=block_counter,
                seat_hint=sb.seat_hint,
                image_bytes=block_image,
                text_hint=text_hint
            ))
            block_counter += 1

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_segmentation_done",
                "badge": "SEGMENTATION COMPLETE",
                "title": f"Segmented {len(all_blocks)} student blocks across {len(pages)} pages",
                "details": {
                    "total_blocks": len(all_blocks),
                    "total_pages": len(pages),
                    "avg_blocks_per_page": round(len(all_blocks) / max(len(pages), 1), 1)
                }
            })
        except Exception:
            pass

    if not all_blocks:
        logger.warning("No student blocks detected in document.")
        meta = build_exam_meta(contract)
        return meta, [], "block_extraction"

    # ── Stage 4: Block-by-Block Extraction ──
    if progress_cb:
        progress_cb(15.0, f"Stage 4: Extracting {len(all_blocks)} student blocks "
                          f"({max_workers} parallel workers)...")

    system_instruction = build_block_system_instruction(contract)

    raw_students = extract_all_blocks(
        all_blocks, client, system_instruction, contract,
        max_workers=max_workers,
        progress_cb=progress_cb,
        audit_cb=audit_cb
    )

    # Filter out failed extractions and deduplicate by seat
    students: List[StudentRecord] = []
    seen_seats: set = set()
    for s in raw_students:
        if s and s.seat_no and s.seat_no not in seen_seats:
            seen_seats.add(s.seat_no)
            s.semester = s.semester or contract.semester
            students.append(s)

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_extraction_done",
                "badge": "EXTRACTION COMPLETE",
                "title": f"Extracted {len(students)}/{len(all_blocks)} student records",
                "details": {
                    "extracted": len(students),
                    "total_blocks": len(all_blocks),
                    "failed": len(all_blocks) - len(students)
                }
            })
        except Exception:
            pass

    # ── Stage 5: Math Validation ──
    if progress_cb:
        progress_cb(82.0, "Stage 5: Running math validation audit...")

    # Build a mapping from student to block for re-extraction
    student_blocks = []
    block_idx = 0
    for s in raw_students:
        if s and s.seat_no:
            if block_idx < len(all_blocks):
                student_blocks.append(all_blocks[block_idx])
            else:
                student_blocks.append(None)
        block_idx += 1

    conflicts = validate_all(students, all_blocks, contract)

    verified_count = len(students) - len({c.student_index for c in conflicts})

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_validation_done",
                "badge": "VALIDATION COMPLETE",
                "title": f"Math audit: {verified_count}/{len(students)} passed, "
                         f"{len(conflicts)} conflicts detected",
                "details": {
                    "verified": verified_count,
                    "total": len(students),
                    "conflicts": len(conflicts),
                    "conflict_types": list({c.conflict_type for c in conflicts})
                }
            })
        except Exception:
            pass

    # ── Stage 6: Targeted Re-extraction ──
    if conflicts:
        if progress_cb:
            progress_cb(85.0, f"Stage 6: Re-extracting {len({c.student_index for c in conflicts})} "
                              f"conflicting blocks...")

        corrections = apply_resolutions(
            students, conflicts, client, system_instruction, contract, audit_cb
        )

        if audit_cb:
            try:
                audit_cb({
                    "timestamp": time.strftime("%H:%M:%S"),
                    "type": "block_reextraction_done",
                    "badge": "RE-EXTRACTION COMPLETE",
                    "title": f"Corrected {corrections} records via targeted re-extraction",
                    "details": {"corrections": corrections}
                })
            except Exception:
                pass

    # ── Final: Build ExamMeta and return ──
    meta = build_exam_meta(contract)

    # Dynamic catalog expansion: add courses discovered in later pages
    for s in students:
        for cr in s.course_results:
            c_code = normalize_course_code(cr.course_code)
            if not meta.catalog.get_by_code(c_code):
                try:
                    cr_val = float(cr.credits) if cr.credits and cr.credits.replace(".", "").isdigit() else 0.0
                except Exception:
                    cr_val = 0.0
                meta.catalog.courses.append(Course(
                    code=c_code,
                    name=cr.course_name or c_code,
                    heads=list(cr.marks.keys()),
                    credits=cr_val
                ))

    final_verified = sum(
        1 for s in students
        if not validate_record(s, contract)
    )

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "block_pipeline_complete",
                "badge": "PIPELINE COMPLETE",
                "title": f"Block extraction complete! {len(students)} students "
                         f"({final_verified} math-verified)",
                "details": {
                    "total_students": len(students),
                    "verified_count": final_verified,
                    "courses_count": len(meta.catalog.courses),
                    "institution": contract.institution
                }
            })
        except Exception:
            pass

    if progress_cb:
        progress_cb(95.0, f"Block extraction complete: {len(students)} students ready for export.")

    return meta, students, "block_extraction"
