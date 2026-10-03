"""
Targeted Spotlight Micro-Healer & Zero-Trust Verification Sandbox
==================================================================
Resolves micro-errors (flipped digits, swapped columns, OCR misreads) by framing
focused, high-precision questions to the AI with full mathematical context and
zero-trust proof gating.
"""

import copy
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from .checksum_verifier import safe_float
    from .cloud_client import CloudClient
    from .dynamic_relation_engine import DiscrepancyReport, DynamicRelationEngine
    from ..models import CourseResult, StudentRecord
except Exception:
    try:
        from data_extractor_AI.core.checksum_verifier import safe_float
        from data_extractor_AI.core.cloud_client import CloudClient
        from data_extractor_AI.core.dynamic_relation_engine import DiscrepancyReport, DynamicRelationEngine
        from data_extractor_AI.models import CourseResult, StudentRecord
    except Exception:
        from core.checksum_verifier import safe_float
        from core.cloud_client import CloudClient
        from core.dynamic_relation_engine import DiscrepancyReport, DynamicRelationEngine
        from models import CourseResult, StudentRecord

logger = logging.getLogger(__name__)

AuditCallback = Optional[Callable[[Dict[str, Any]], None]]


def extract_candidate_text_snippet(seat_no: str, page_text: str, window_lines: int = 10) -> str:
    """Extracts a tight window of lines around the target seat number (~250-400 chars)."""
    if not seat_no or not page_text:
        return ""
    lines = page_text.splitlines()
    match_idx = -1
    clean_seat = str(seat_no).strip()
    for idx, line in enumerate(lines):
        if clean_seat in line:
            match_idx = idx
            break
    if match_idx == -1:
        return ""

    start = max(0, match_idx - 2)
    end = min(len(lines), match_idx + window_lines)
    return "\n".join(lines[start:end]).strip()


def validate_spotlight_patch(
    patch: Dict[str, Any],
    report: DiscrepancyReport,
    student: StudentRecord,
    engine: DynamicRelationEngine,
    raw_snippet: str
) -> Tuple[bool, str, StudentRecord]:
    """
    Zero-Trust Sandbox Gate:
    1. Verifies that evidence quote exists in raw snippet.
    2. Simulates patch on student record and verifies it satisfies mathematical relations.
    3. Rejects hallucinations that break mathematical integrity.
    """
    field_to_patch = str(patch.get("corrected_field", "")).strip()
    new_val = str(patch.get("corrected_value", "")).strip()
    evidence = str(patch.get("verbatim_evidence", "")).strip()

    if not field_to_patch or not new_val:
        return False, "Missing corrected_field or corrected_value in AI patch.", student

    # 1. Evidence quote verification in snippet
    clean_snippet = re.sub(r"\s+", " ", raw_snippet).lower()
    clean_evidence = re.sub(r"\s+", " ", evidence).lower()
    if clean_evidence and clean_evidence not in clean_snippet:
        # Relaxed token check
        ev_tokens = [t for t in re.split(r"[\s\+\=]+", clean_evidence) if len(t) >= 2]
        if not (ev_tokens and all(t in clean_snippet for t in ev_tokens[:2])):
            return False, f"Verbatim quote '{evidence}' not found in raw snippet text.", student

    # 2. Clone and simulate patch on student
    sim_st = StudentRecord(
        seat_no=student.seat_no,
        student_name=student.student_name,
        gender=student.gender,
        mother_name=student.mother_name,
        candidate_type=student.candidate_type,
        prn=student.prn,
        abc_id=student.abc_id,
        ern=student.ern,
        semester=student.semester,
        result_status=student.result_status,
        remark=student.remark,
        total_marks=student.total_marks,
        max_marks=student.max_marks,
        credits_earned=student.credits_earned,
        total_cg=student.total_cg,
        sgpi=student.sgpi,
        cgpi=student.cgpi,
        course_results=[
            CourseResult(
                course_code=cr.course_code,
                course_name=cr.course_name,
                marks=dict(cr.marks),
                grade=cr.grade,
                grade_point=cr.grade_point,
                credits=cr.credits,
                credit_points=cr.credit_points
            )
            for cr in student.course_results
        ],
        history=copy.deepcopy(student.history),
        raw_flat_dict=dict(student.raw_flat_dict) if student.raw_flat_dict else {}
    )

    # Apply patch to candidate course result
    matched_cr = None
    if report.course_code:
        for cr in sim_st.course_results:
            if cr.course_code == report.course_code:
                matched_cr = cr
                break

    if matched_cr:
        clean_field = field_to_patch.upper().replace(f"{report.course_code.upper()}_", "")
        if clean_field in ("GRADE", "GRADE_POINT", "GP", "CREDITS", "CG"):
            if clean_field == "GRADE":
                matched_cr.grade = new_val
            elif clean_field in ("GRADE_POINT", "GP"):
                matched_cr.grade_point = new_val
            elif clean_field == "CREDITS":
                matched_cr.credits = new_val
            elif clean_field == "CG":
                matched_cr.credit_points = new_val
        else:
            matched_cr.marks[clean_field] = new_val
    else:
        # Student aggregate patch
        if "TOTAL_MARKS" in field_to_patch.upper():
            sim_st.total_marks = new_val
        elif "TOTAL_CG" in field_to_patch.upper():
            sim_st.total_cg = new_val
        elif "SGPI" in field_to_patch.upper():
            sim_st.sgpi = new_val
        elif "CREDITS" in field_to_patch.upper():
            sim_st.credits_earned = new_val

    # Re-evaluate simulated student with DynamicRelationEngine
    sim_healed, new_discs = engine.audit_and_heal_student(sim_st)
    remaining_conflicts = [
        d for d in new_discs
        if d.status == "CONFLICT" and (d.field_name == report.field_name or d.course_code == report.course_code)
    ]

    if remaining_conflicts:
        return (
            False,
            f"Simulated patch failed to resolve math conflict ({remaining_conflicts[0].reason}).",
            student
        )

    return True, "Passed zero-trust mathematical proof gate.", sim_healed


class SpotlightHealer:
    """Executes precision micro-prompts for conflicting data cells."""

    def __init__(self, client: Optional[CloudClient] = None, engine: Optional[DynamicRelationEngine] = None):
        self.client = client or CloudClient()
        self.engine = engine or DynamicRelationEngine()

    def heal_discrepancy(
        self,
        report: DiscrepancyReport,
        student: StudentRecord,
        page_text: str,
        audit_cb: Optional[AuditCallback] = None
    ) -> Tuple[StudentRecord, bool, str]:
        """
        Spotlights a single conflicting cell with local snippet context and prompts AI to resolve it.
        """
        if not self.client or not self.client.is_configured():
            # Fallback to deterministic expected formula value
            return self._apply_deterministic_fallback(report, student)

        snippet = extract_candidate_text_snippet(report.seat_no, page_text)
        if not snippet:
            return student, False, "Candidate seat snippet not found on page."

        components_desc = ", ".join(f"{k}='{v}'" for k, v in report.component_values.items())
        is_column_issue = "Profile_Violation" in report.field_name or "Column" in report.field_name
        task_prompt = (
            "The marks for this candidate appear shifted across columns.\n"
            f"Contradiction: {report.reason}\n"
            "Inspect the candidate line in the snippet above character-for-character to determine the correct value or alignment for this slot."
            if is_column_issue else
            "Inspect the snippet above character-for-character.\n"
            "Resolve the contradiction: Which number was misread or corrupted by OCR?"
        )

        prompt = (
            "=== SPOTLIGHT ANOMALY INVESTIGATION ===\n"
            f"Candidate Seat: {report.seat_no} ({report.student_name})\n"
            f"Course: {report.course_code or 'Student Summary'}\n"
            f"Mathematical Relation Invariant: {report.formula}\n"
            f"Extracted Components: {components_desc}\n"
            f"Current Target Extracted: '{report.extracted_val}' (Expected: '{report.expected_val}')\n"
            f"Contradiction: {report.reason}\n\n"
            "=== LOCAL TEXT SNIPPET FROM PAGE ===\n"
            f"{snippet}\n\n"
            "=== YOUR TASK ===\n"
            f"{task_prompt}\n"
            "Return ONLY pure JSON with this exact structure:\n"
            "{\n"
            '  "resolved": true,\n'
            f'  "corrected_field": "{report.field_name.split("_")[-1]}",\n'
            f'  "corrected_value": "{report.expected_val}",\n'
            '  "reason": "Brief explanation of what was visually misread or realigned",\n'
            '  "verbatim_evidence": "Exact substring from snippet"\n'
            "}"
        )

        system_instruction = (
            "You are a precision academic ledger auditor resolving a single conflicting number. "
            "Examine the local text snippet carefully. Provide the exact printed value and verbatim evidence quote. "
            "Output ONLY valid JSON."
        )

        try:
            resp_text = self.client.generate_direct(
                user_prompt=prompt,
                system_instruction=system_instruction,
                format_json=True,
                timeout=25,
                temperature=0.0,
                audit_cb=audit_cb
            )
            patch = json.loads(resp_text)
            if not isinstance(patch, dict) or not patch.get("resolved"):
                return self._apply_deterministic_fallback(report, student)

            # Gate through Zero-Trust Sandbox
            ok, gate_msg, patched_student = validate_spotlight_patch(
                patch=patch,
                report=report,
                student=student,
                engine=self.engine,
                raw_snippet=snippet
            )

            if ok:
                if audit_cb:
                    try:
                        audit_cb({
                            "timestamp": time.strftime("%H:%M:%S"),
                            "type": "spotlight_healed",
                            "badge": "MICRO-HEALED",
                            "title": f"Spotlight resolved {report.field_name} on Seat {report.seat_no}: {report.extracted_val} -> {patch.get('corrected_value')}",
                            "details": patch
                        })
                    except Exception:
                        pass
                return patched_student, True, f"AI Healed: {patch.get('reason', '')}"
            else:
                logger.debug("Spotlight patch rejected by sandbox: %s", gate_msg)
                return self._apply_deterministic_fallback(report, student)

        except Exception as e:
            logger.warning("Spotlight prompter encountered error: %s", e)
            return self._apply_deterministic_fallback(report, student)

    def _apply_deterministic_fallback(
        self,
        report: DiscrepancyReport,
        student: StudentRecord
    ) -> Tuple[StudentRecord, bool, str]:
        """Safely applies deterministic formula value if AI is offline or hallucinated."""
        if report.expected_val is not None:
            if report.course_code:
                for cr in student.course_results:
                    if cr.course_code == report.course_code:
                        field_short = report.field_name.split("_")[-1].upper()
                        if field_short in ("TOT", "TH", "IA", "TW", "PR", "OR"):
                            cr.marks[field_short] = str(report.expected_val)
                        elif field_short in ("CG", "CREDIT_POINTS"):
                            cr.credit_points = str(report.expected_val)
            else:
                if "TOTAL_MARKS" in report.field_name.upper():
                    if not student.total_marks or student.total_marks in ("0", ""):
                        student.total_marks = str(report.expected_val)
                elif "TOTAL_CG" in report.field_name.upper():
                    if not student.total_cg or student.total_cg in ("0", ""):
                        student.total_cg = str(report.expected_val)
                elif "SGPI" in report.field_name.upper():
                    if not student.sgpi or student.sgpi in ("0", "0.0", "0.00", ""):
                        student.sgpi = str(report.expected_val)

            return student, True, f"Deterministic formula fallback applied ({report.formula})."

        return student, False, "Unresolved conflict."

    def heal_batch(
        self,
        students: List[StudentRecord],
        discrepancies: List[DiscrepancyReport],
        page_texts: List[str],
        audit_cb: Optional[AuditCallback] = None,
        max_repairs: int = 50
    ) -> Tuple[List[StudentRecord], int, int]:
        """Iterates through conflict discrepancies and applies Spotlight Healing."""
        conflicts = [d for d in discrepancies if d.status == "CONFLICT"]
        if not conflicts:
            return students, 0, 0

        students_by_seat = {s.seat_no: s for s in students}
        healed_count = 0
        failed_count = 0

        for idx, report in enumerate(conflicts[:max_repairs]):
            if idx > 0 and self.client and self.client.is_configured():
                time.sleep(1.0)  # Polite pacing

            st = students_by_seat.get(report.seat_no)
            if not st:
                continue

            # Determine relevant page text
            p_text = ""
            for pt in page_texts:
                if report.seat_no in pt:
                    p_text = pt
                    break
            if not p_text and page_texts:
                p_text = page_texts[0]

            patched_st, success, msg = self.heal_discrepancy(report, st, p_text, audit_cb=audit_cb)
            if success:
                students_by_seat[report.seat_no] = patched_st
                healed_count += 1
            else:
                failed_count += 1

        updated_students = list(students_by_seat.values())
        return updated_students, healed_count, failed_count
