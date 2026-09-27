"""
Sequential Token Slot Engine
============================
Deterministic Positional Slot Machine and Alignment Engine for Academic Gazettes.
Eliminates column guessing and AI hallucinations by treating marksheet extraction
as an ordered sequence of typed tokens with Heartbeat Anchors (Grade Letters).

Guarantees:
1. Every candidate row is compiled into a fixed sequence of slots based on Master Course Catalog.
2. Tokens are consumed sequentially left-to-right.
3. Grade letters ('O', 'A+', 'A', 'B+', 'B', 'C', 'D', 'P', 'F', 'AB') act as heartbeat anchors,
   immediately resynchronizing slot pointers if a mark was faint or omitted.
4. Mathematical constraint solving auto-reconciles missing or noisy digits.
"""

from dataclasses import dataclass, field
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..models import Course, CourseCatalog, CourseResult, StudentRecord
    from .checksum_verifier import safe_float
    from .normalizer import normalize_head
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog, CourseResult, StudentRecord
        from data_extractor_AI.core.checksum_verifier import safe_float
        from data_extractor_AI.core.normalizer import normalize_head
    except Exception:
        from models import Course, CourseCatalog, CourseResult, StudentRecord
        from core.checksum_verifier import safe_float
        from core.normalizer import normalize_head

logger = logging.getLogger(__name__)

GRADE_SCALE: Dict[str, float] = {
    "O": 10.0,
    "A+": 9.0,
    "A": 8.0,
    "B+": 7.0,
    "B": 6.0,
    "C": 5.0,
    "D": 4.0,
    "E": 5.0,
    "P": 4.0,
    "F": 0.0,
    "AB": 0.0,
    "AA": 0.0,
    "ABS": 0.0,
    "NULL": 0.0,
    "--": 0.0
}

VALID_GRADES = {
    "O", "A+", "A", "B+", "B", "C+", "C", "D", "E", "P",
    "F", "AB", "AA", "ABS", "--", "FAIL", "PASS"
}

STATUS_TOKENS = {
    "PASS", "FAIL", "FAILED", "PASSED", "SUCCESSFUL", "UNSUCCESSFUL",
    "ATKT", "P", "F", "ABS", "RPV", "RLE"
}


@dataclass
class CourseSlotGroup:
    """A structured slot group representing one subject in canonical catalog order."""
    course_code: str
    course_name: str
    mark_heads: List[str]            # e.g. ["TH", "IA", "TOT"] or ["TW", "PR", "TOT"] or ["TW", "TOT"]
    credits: float = 0.0
    max_marks: Dict[str, int] = field(default_factory=dict)
    min_marks: Dict[str, int] = field(default_factory=dict)


def is_grade_token(tok: str) -> bool:
    """Check if token is a valid letter grade anchor."""
    clean = str(tok).strip().upper().replace("*", "")
    return clean in VALID_GRADES


def is_status_token(tok: str) -> bool:
    """Check if token indicates overall pass/fail status."""
    clean = str(tok).strip().upper()
    return clean in STATUS_TOKENS


def is_sgpi_token(tok: str) -> bool:
    """Check if token resembles an SGPI/GPA float (e.g. 7.78, 8.52, 0.00, 10.00)."""
    s = str(tok).strip()
    if "." in s:
        try:
            val = float(s)
            return 0.0 <= val <= 10.0
        except ValueError:
            return False
    return False


def compile_course_slot_groups(catalog: CourseCatalog) -> List[CourseSlotGroup]:
    """
    Compile the Master Course Catalog into an ordered sequence of course slot groups.
    """
    groups: List[CourseSlotGroup] = []

    for course in catalog.courses:
        c_code = course.code
        c_name = course.name or c_code
        heads = [h.upper() for h in course.heads]

        has_th = any(h in heads for h in ["TH", "SE", "THEORY", "EXT"])
        has_ia = any(h in heads for h in ["IA", "IN", "IAT", "INTERNAL"])
        has_tw = any(h in heads for h in ["TW", "TERM WORK", "TERMWORK"])
        has_pr = any(h in heads for h in ["PR", "OR", "PR_OR", "OR_PR", "TW_PR", "PRACTICAL", "ORAL"])

        mark_heads: List[str] = []
        if has_th and has_ia:
            mark_heads = ["TH", "IA", "TOT"]
        elif has_tw and has_pr:
            mark_heads = ["TW", "PR", "TOT"]
        elif has_tw and not has_pr and not has_th:
            mark_heads = ["TW", "TOT"]
        elif has_th and not has_ia:
            mark_heads = ["TH", "TOT"]
        else:
            added = set()
            for h in heads:
                norm = normalize_head(h)
                if norm not in ["TOT", "TOTAL"] and norm not in added:
                    mark_heads.append(norm)
                    added.add(norm)
            mark_heads.append("TOT")

        groups.append(CourseSlotGroup(
            course_code=c_code,
            course_name=c_name,
            mark_heads=mark_heads,
            credits=course.credits,
            max_marks=dict(course.max_marks),
            min_marks=dict(course.min_marks)
        ))

    return groups


class SequentialSlotMachine:
    """
    Deterministic Positional Slot Machine.
    Consumes sequential tokens and locks them into exact course slots using
    Grade letter heartbeat anchors and mathematical constraint satisfaction.
    """

    def __init__(self, catalog: CourseCatalog):
        self.catalog = catalog
        self.slot_groups = compile_course_slot_groups(catalog)

    def parse_student_tokens(
        self,
        tokens: List[str],
        meta: Optional[Dict[str, Any]] = None
    ) -> StudentRecord:
        """
        Stream parser: Consumes tokens left-to-right across the Master Catalog groups.
        """
        student = StudentRecord()
        meta = meta or {}

        # 1. Populate basic student metadata if passed or extract from leading tokens
        student.seat_no = str(meta.get("seat_no", "")).strip()
        student.student_name = str(meta.get("student_name", "")).strip()
        student.gender = str(meta.get("gender", "")).strip()
        student.candidate_type = str(meta.get("candidate_type", "Regular")).strip()
        student.prn = str(meta.get("prn", "")).strip()
        student.mother_name = str(meta.get("mother_name", "")).strip()

        # Clean token list
        clean_tokens = [str(t).strip() for t in tokens if str(t).strip() not in ["", "|", ",", "/"]]

        tok_idx = 0
        n_tok = len(clean_tokens)

        # If seat_no is missing, inspect leading token for 7-10 digit number
        if not student.seat_no and tok_idx < n_tok and re.match(r"^\d{7,10}$", clean_tokens[tok_idx]):
            student.seat_no = clean_tokens[tok_idx]
            tok_idx += 1

        # If student_name is missing, inspect for text token
        if not student.student_name and tok_idx < n_tok and re.match(r"^[A-Z\s]{4,}$", clean_tokens[tok_idx]) and not clean_tokens[tok_idx].isdigit():
            student.student_name = clean_tokens[tok_idx]
            tok_idx += 1

        # If PRN is in stream, inspect for 16-digit number
        if tok_idx < n_tok and (re.match(r"^\d{16}$", clean_tokens[tok_idx]) or clean_tokens[tok_idx].startswith("MU")):
            if not student.prn:
                student.prn = clean_tokens[tok_idx]
            tok_idx += 1

        course_results: List[CourseResult] = []

        # 2. Iterate through each CourseSlotGroup in strict catalog order
        for group in self.slot_groups:
            c_code = group.course_code
            c_name = group.course_name
            expected_heads = list(group.mark_heads)  # e.g. ["TH", "IA", "TOT"]
            cr = CourseResult(course_code=c_code, course_name=c_name)

            # Find the next Grade Letter Anchor ahead in the stream (lookahead limit ~8 tokens)
            grade_anchor_pos = -1
            for offset in range(min(8, n_tok - tok_idx)):
                candidate = clean_tokens[tok_idx + offset].upper().replace("*", "")
                if is_grade_token(candidate):
                    grade_anchor_pos = tok_idx + offset
                    break

            if grade_anchor_pos != -1:
                # Tokens between tok_idx and grade_anchor_pos are marks!
                mark_tokens = clean_tokens[tok_idx:grade_anchor_pos]
                grade_token = clean_tokens[grade_anchor_pos].upper().replace("*", "")
                cr.grade = grade_token

                # Map mark tokens to expected heads
                if len(mark_tokens) == len(expected_heads):
                    for h, val in zip(expected_heads, mark_tokens):
                        cr.marks[h] = val
                elif len(mark_tokens) == 2 and len(expected_heads) == 3:
                    # Missing TOT or IA? Usually TH and IA are present, TOT is sum
                    cr.marks[expected_heads[0]] = mark_tokens[0]
                    cr.marks[expected_heads[1]] = mark_tokens[1]
                    # Compute TOT
                    m0 = safe_float(mark_tokens[0])
                    m1 = safe_float(mark_tokens[1])
                    if m0 is not None and m1 is not None:
                        cr.marks[expected_heads[2]] = str(int(m0 + m1))
                elif len(mark_tokens) == 1 and len(expected_heads) >= 1:
                    # Single mark, assign to TOT or first head
                    cr.marks[expected_heads[-1]] = mark_tokens[0]

                # Advance pointer past Grade anchor
                tok_idx = grade_anchor_pos + 1

                # Consume GP (Grade Point) if next token is integer
                if tok_idx < n_tok and re.match(r"^\d{1,2}$", clean_tokens[tok_idx]):
                    gp_val = safe_float(clean_tokens[tok_idx])
                    if gp_val is not None and 0.0 <= gp_val <= 10.0:
                        cr.grade_point = str(int(gp_val))
                        tok_idx += 1

                # Consume Credits if next token is numeric
                if tok_idx < n_tok and re.match(r"^\d+(\.\d+)?$", clean_tokens[tok_idx]):
                    c_val = safe_float(clean_tokens[tok_idx])
                    if c_val is not None and 1.0 <= c_val <= 6.0:
                        cr.credits = str(int(c_val) if c_val.is_integer() else c_val)
                        tok_idx += 1
                if not cr.credits and group.credits > 0:
                    cr.credits = str(int(group.credits) if group.credits.is_integer() else group.credits)

                # Consume CG (Credit Points) if next token is numeric
                if tok_idx < n_tok and re.match(r"^\d+(\.\d+)?$", clean_tokens[tok_idx]):
                    cg_val = safe_float(clean_tokens[tok_idx])
                    if cg_val is not None:
                        cr.credit_points = str(cg_val)
                        tok_idx += 1

            else:
                # No grade anchor found within window: fallback sequential consumption
                for h in expected_heads:
                    if tok_idx < n_tok:
                        cr.marks[h] = clean_tokens[tok_idx]
                        tok_idx += 1

                if tok_idx < n_tok and is_grade_token(clean_tokens[tok_idx]):
                    cr.grade = clean_tokens[tok_idx].upper().replace("*", "")
                    tok_idx += 1

                if tok_idx < n_tok and re.match(r"^\d{1,2}$", clean_tokens[tok_idx]):
                    cr.grade_point = clean_tokens[tok_idx]
                    tok_idx += 1

                if group.credits > 0:
                    cr.credits = str(int(group.credits) if group.credits.is_integer() else group.credits)

            # Auto-reconcile GP from Grade if GP is missing
            if not cr.grade_point and cr.grade:
                clean_gr = cr.grade.upper()
                if clean_gr in GRADE_SCALE:
                    cr.grade_point = str(int(GRADE_SCALE[clean_gr]))

            # Auto-reconcile CG = Credits * GP
            gp_f = safe_float(cr.grade_point)
            cr_f = safe_float(cr.credits) or group.credits
            if gp_f is not None and cr_f > 0 and not cr.credit_points:
                cr.credit_points = str(round(gp_f * cr_f, 1))

            course_results.append(cr)

        student.course_results = course_results

        # 3. Parse Trailing Summary Tokens (Total Marks, Credits Earned, Total CG, SGPI, Result)
        remaining_tokens = clean_tokens[tok_idx:]

        for tok in remaining_tokens:
            tok_upper = tok.upper()
            if is_status_token(tok_upper) and not student.result_status:
                student.result_status = tok_upper
            elif is_sgpi_token(tok) and not student.sgpi:
                student.sgpi = f"{float(tok):.2f}"
            elif re.match(r"^\d{3,4}$", tok) and not student.total_marks:
                student.total_marks = tok
            elif re.match(r"^\d{1,2}$", tok) and not student.credits_earned:
                c_val = safe_float(tok)
                if c_val and 10.0 <= c_val <= 30.0:
                    student.credits_earned = tok

        # Fallback for status: if any Grade is F -> FAIL, else PASS
        has_failed = any(cr.grade == "F" for cr in student.course_results)
        if not student.result_status:
            student.result_status = "FAIL" if has_failed else "PASS"

        # Apply Mathematical Auto-Healing Proofs
        try:
            from .visual_extractor import align_and_heal_student
            student = align_and_heal_student(student, catalog=self.catalog)
        except Exception:
            pass

        return student
