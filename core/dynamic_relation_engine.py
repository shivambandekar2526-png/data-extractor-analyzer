"""
Dynamic Relation Engine & Mathematical Rule Evaluator
======================================================
Executes AI-compiled DocumentExecutionSpec formulas deterministically:
1. AST-based / safe arithmetic evaluation (zero eval() security risks).
2. Auto-filling missing components and totals without extra LLM calls.
3. Strict discrepancy auditing (identifying conflicts for Spotlight Micro-Healer).
"""

from dataclasses import asdict, dataclass, field
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from .checksum_verifier import safe_float
    from .document_spec import AlgebraicRelation, DocumentExecutionSpec
    from ..models import CourseResult, StudentRecord
except Exception:
    try:
        from data_extractor_AI.core.checksum_verifier import safe_float
        from data_extractor_AI.core.document_spec import AlgebraicRelation, DocumentExecutionSpec
        from data_extractor_AI.models import CourseResult, StudentRecord
    except Exception:
        from core.checksum_verifier import safe_float
        from core.document_spec import AlgebraicRelation, DocumentExecutionSpec
        from models import CourseResult, StudentRecord

logger = logging.getLogger(__name__)


@dataclass
class DiscrepancyReport:
    """Detailed discrepancy found by the Dynamic Relation Engine."""
    seat_no: str
    student_name: str
    course_code: Optional[str]
    field_name: str
    extracted_val: Any
    expected_val: Any
    discrepancy_magnitude: float
    formula: str
    component_values: Dict[str, Any]
    status: str = "CONFLICT"                   # "CONFLICT", "MISSING_AUTO_RESOLVED", "VERIFIED"
    page_number: int = 1
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DynamicRelationEngine:
    """Evaluates mathematical relations compiled by the AI and resolves micro-gaps."""

    def __init__(self, spec: Optional[DocumentExecutionSpec] = None):
        self.spec = spec or DocumentExecutionSpec()

    def set_spec(self, spec: DocumentExecutionSpec) -> None:
        self.spec = spec

    # -------------------------------------------------------------------------
    # Grade & Grade-Point Harmonization
    # -------------------------------------------------------------------------
    def harmonize_grade_and_gp(self, cr: CourseResult) -> None:
        """Harmonizes grade and GP using spec.grading_contract without hallucinating."""
        grading = self.spec.grading_contract
        scale = grading.scale
        valid_grades = grading.valid_grades

        raw_g = (cr.grade or "").strip().upper()
        raw_gp = (cr.grade_point or "").strip()

        # Inversion check: Did model output GP in Grade field and vice versa?
        if (raw_g.isdigit() or not raw_g) and raw_gp.upper() in valid_grades:
            raw_g, raw_gp = raw_gp.upper(), raw_g

        # Clean synthetic +/- if not in valid grades
        if raw_g and raw_g not in valid_grades:
            stripped = raw_g.rstrip("+-")
            if stripped in valid_grades:
                raw_g = stripped

        cr.grade = raw_g

        # Fill missing GP if Grade exists
        if (not raw_gp or raw_gp in ("0", "0.0", "")) and raw_g and raw_g not in grading.fail_grades:
            if raw_g in scale:
                cr.grade_point = str(int(scale[raw_g]))

        # Fill missing Grade if GP exists
        elif not raw_g and raw_gp and raw_gp not in ("--", "-"):
            try:
                gp_num = float(raw_gp)
                # Find matching grade with same points
                for g_name, g_pts in scale.items():
                    if abs(g_pts - gp_num) < 0.01:
                        cr.grade = g_name
                        break
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Course-Level Relation Evaluation
    # -------------------------------------------------------------------------
    def evaluate_course_result(self, cr: CourseResult, student: StudentRecord) -> List[DiscrepancyReport]:
        """Audits and auto-fills course-level components (TOT, CG)."""
        discrepancies: List[DiscrepancyReport] = []
        self.harmonize_grade_and_gp(cr)

        # 1. Course Slot Metadata Enforcement (Credits & heads)
        slot = self.spec.get_slot_by_code(cr.course_code)
        if slot:
            if slot.credits > 0:
                cur_cr = safe_float(cr.credits)
                # Auto-fill missing credits or fix credits duplicated from CG
                if cur_cr == 0 or cur_cr > 10.0 or (cur_cr == safe_float(cr.credit_points) and cur_cr != slot.credits):
                    cr.credits = str(int(slot.credits) if slot.credits.is_integer() else slot.credits)
            if not cr.course_name and slot.name:
                cr.course_name = slot.name

        # 2. Per-Course Algebraic Relations
        course_relations = [r for r in self.spec.algebraic_relations if r.scope == "per_course"]
        for rel in course_relations:
            if rel.target_field == "TOT":
                tot_extracted = safe_float(cr.marks.get("TOT"))
                has_raw_tot = bool(cr.marks.get("TOT") and str(cr.marks["TOT"]).strip() not in ("--", "-"))

                # Test formulas: TH + IA or TW + PR
                if "TH" in rel.formula and "IA" in rel.formula:
                    th_val = safe_float(cr.marks.get("TH"))
                    ia_val = safe_float(cr.marks.get("IA"))
                    if th_val > 0 or ia_val > 0:
                        expected_tot = round(th_val + ia_val, 1)
                        if not has_raw_tot or tot_extracted == 0:
                            # Auto-fill missing total
                            cr.marks["TOT"] = str(int(expected_tot) if expected_tot.is_integer() else expected_tot)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TOT",
                                extracted_val="",
                                expected_val=cr.marks["TOT"],
                                discrepancy_magnitude=expected_tot,
                                formula="TH + IA",
                                component_values={"TH": th_val, "IA": ia_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-populated missing TOT from TH + IA."
                            ))
                        elif not cr.marks.get("TH") and ia_val > 0 and tot_extracted > ia_val:
                            derived_th = round(tot_extracted - ia_val, 1)
                            cr.marks["TH"] = str(int(derived_th) if derived_th.is_integer() else derived_th)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TH",
                                extracted_val="",
                                expected_val=cr.marks["TH"],
                                discrepancy_magnitude=derived_th,
                                formula="TOT - IA",
                                component_values={"TOT": tot_extracted, "IA": ia_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-calculated missing TH from TOT - IA."
                            ))
                        elif not cr.marks.get("IA") and th_val > 0 and tot_extracted > th_val:
                            derived_ia = round(tot_extracted - th_val, 1)
                            cr.marks["IA"] = str(int(derived_ia) if derived_ia.is_integer() else derived_ia)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_IA",
                                extracted_val="",
                                expected_val=cr.marks["IA"],
                                discrepancy_magnitude=derived_ia,
                                formula="TOT - TH",
                                component_values={"TOT": tot_extracted, "TH": th_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-calculated missing IA from TOT - TH."
                            ))
                        elif abs(tot_extracted - expected_tot) > rel.tolerance:
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TOT",
                                extracted_val=cr.marks.get("TOT"),
                                expected_val=int(expected_tot) if expected_tot.is_integer() else expected_tot,
                                discrepancy_magnitude=abs(tot_extracted - expected_tot),
                                formula="TH + IA",
                                component_values={"TH": th_val, "IA": ia_val},
                                status="CONFLICT",
                                reason=f"Extracted TOT ({tot_extracted}) != TH+IA ({expected_tot})"
                            ))

                elif "TW" in rel.formula and "PR" in rel.formula:
                    tw_val = safe_float(cr.marks.get("TW"))
                    pr_val = safe_float(cr.marks.get("PR"))
                    if tw_val > 0 or pr_val > 0:
                        expected_tot = round(tw_val + pr_val, 1)
                        if not has_raw_tot or tot_extracted == 0:
                            cr.marks["TOT"] = str(int(expected_tot) if expected_tot.is_integer() else expected_tot)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TOT",
                                extracted_val="",
                                expected_val=cr.marks["TOT"],
                                discrepancy_magnitude=expected_tot,
                                formula="TW + PR",
                                component_values={"TW": tw_val, "PR": pr_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-populated missing TOT from TW + PR."
                            ))
                        elif not cr.marks.get("TW") and pr_val > 0 and tot_extracted > pr_val:
                            derived_tw = round(tot_extracted - pr_val, 1)
                            cr.marks["TW"] = str(int(derived_tw) if derived_tw.is_integer() else derived_tw)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TW",
                                extracted_val="",
                                expected_val=cr.marks["TW"],
                                discrepancy_magnitude=derived_tw,
                                formula="TOT - PR",
                                component_values={"TOT": tot_extracted, "PR": pr_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-calculated missing TW from TOT - PR."
                            ))
                        elif not cr.marks.get("PR") and tw_val > 0 and tot_extracted > tw_val:
                            derived_pr = round(tot_extracted - tw_val, 1)
                            cr.marks["PR"] = str(int(derived_pr) if derived_pr.is_integer() else derived_pr)
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_PR",
                                extracted_val="",
                                expected_val=cr.marks["PR"],
                                discrepancy_magnitude=derived_pr,
                                formula="TOT - TW",
                                component_values={"TOT": tot_extracted, "TW": tw_val},
                                status="MISSING_AUTO_RESOLVED",
                                reason="Auto-calculated missing PR from TOT - TW."
                            ))
                        elif abs(tot_extracted - expected_tot) > rel.tolerance:
                            discrepancies.append(DiscrepancyReport(
                                seat_no=student.seat_no,
                                student_name=student.student_name,
                                course_code=cr.course_code,
                                field_name=f"{cr.course_code}_TOT",
                                extracted_val=cr.marks.get("TOT"),
                                expected_val=int(expected_tot) if expected_tot.is_integer() else expected_tot,
                                discrepancy_magnitude=abs(tot_extracted - expected_tot),
                                formula="TW + PR",
                                component_values={"TW": tw_val, "PR": pr_val},
                                status="CONFLICT",
                                reason=f"Extracted TOT ({tot_extracted}) != TW+PR ({expected_tot})"
                            ))

            elif rel.target_field in ("credit_points", "CG"):
                c_credits = safe_float(cr.credits)
                c_gp = safe_float(cr.grade_point)
                cur_cg = safe_float(cr.credit_points)
                has_cg = bool(cr.credit_points and str(cr.credit_points).strip() not in ("--", "-"))

                if c_credits > 0 and c_gp >= 0:
                    expected_cg = round(c_credits * c_gp, 1)
                    if not has_cg or cur_cg == 0:
                        cr.credit_points = str(int(expected_cg) if expected_cg.is_integer() else expected_cg)
                        discrepancies.append(DiscrepancyReport(
                            seat_no=student.seat_no,
                            student_name=student.student_name,
                            course_code=cr.course_code,
                            field_name=f"{cr.course_code}_CG",
                            extracted_val="",
                            expected_val=cr.credit_points,
                            discrepancy_magnitude=expected_cg,
                            formula="credits * grade_point",
                            component_values={"credits": c_credits, "grade_point": c_gp},
                            status="MISSING_AUTO_RESOLVED",
                            reason="Auto-calculated missing CG from Credits * GP."
                        ))
                    elif abs(cur_cg - expected_cg) > rel.tolerance:
                        discrepancies.append(DiscrepancyReport(
                            seat_no=student.seat_no,
                            student_name=student.student_name,
                            course_code=cr.course_code,
                            field_name=f"{cr.course_code}_CG",
                            extracted_val=cr.credit_points,
                            expected_val=int(expected_cg) if expected_cg.is_integer() else expected_cg,
                            discrepancy_magnitude=abs(cur_cg - expected_cg),
                            formula="credits * grade_point",
                            component_values={"credits": c_credits, "grade_point": c_gp},
                            status="CONFLICT",
                            reason=f"Extracted CG ({cur_cg}) != Credits * GP ({expected_cg})"
                        ))

        return discrepancies

    # -------------------------------------------------------------------------
    # Student-Aggregate Evaluation
    # -------------------------------------------------------------------------
    def evaluate_student_aggregates(self, student: StudentRecord) -> List[DiscrepancyReport]:
        """Audits and auto-fills student-level totals (total_marks, total_cg, credits_earned, sgpi)."""
        discrepancies: List[DiscrepancyReport] = []

        tot_marks_calc = sum(safe_float(cr.marks.get("TOT")) for cr in student.course_results)
        tot_cg_calc = sum(safe_float(cr.credit_points) for cr in student.course_results)
        tot_cr_calc = sum(safe_float(cr.credits) for cr in student.course_results if cr.grade not in self.spec.grading_contract.fail_grades)
        total_sched_cr = sum(safe_float(cr.credits) for cr in student.course_results)

        # 0. Student Attributes Completion
        if not student.gender:
            student.gender = "F" if student.student_name.startswith("/") else "M"
        if not student.candidate_type:
            student.candidate_type = "Regular"

        # 1. Total Marks
        cur_tm = safe_float(student.total_marks)
        if not student.total_marks or cur_tm == 0:
            if tot_marks_calc > 0:
                student.total_marks = str(int(tot_marks_calc))
                discrepancies.append(DiscrepancyReport(
                    seat_no=student.seat_no,
                    student_name=student.student_name,
                    course_code=None,
                    field_name="Total_Marks",
                    extracted_val="",
                    expected_val=student.total_marks,
                    discrepancy_magnitude=tot_marks_calc,
                    formula="sum(course_results.*.marks.TOT)",
                    component_values={"sum_tot": tot_marks_calc},
                    status="MISSING_AUTO_RESOLVED",
                    reason="Auto-calculated missing Total_Marks from sum of course TOT marks."
                ))
        elif abs(cur_tm - tot_marks_calc) > 5.0 and tot_marks_calc > 0:
            discrepancies.append(DiscrepancyReport(
                seat_no=student.seat_no,
                student_name=student.student_name,
                course_code=None,
                field_name="Total_Marks",
                extracted_val=student.total_marks,
                expected_val=int(tot_marks_calc),
                discrepancy_magnitude=abs(cur_tm - tot_marks_calc),
                formula="sum(course_results.*.marks.TOT)",
                component_values={"extracted": cur_tm, "calculated": tot_marks_calc},
                status="CONFLICT",
                reason=f"Extracted Total_Marks ({cur_tm}) differs from sum of course totals ({tot_marks_calc})"
            ))

        # 2. Total CG
        cur_tcg = safe_float(student.total_cg)
        if not student.total_cg or cur_tcg == 0:
            if tot_cg_calc > 0:
                student.total_cg = str(int(tot_cg_calc) if tot_cg_calc.is_integer() else tot_cg_calc)
                discrepancies.append(DiscrepancyReport(
                    seat_no=student.seat_no,
                    student_name=student.student_name,
                    course_code=None,
                    field_name="Total_CG",
                    extracted_val="",
                    expected_val=student.total_cg,
                    discrepancy_magnitude=tot_cg_calc,
                    formula="sum(course_results.*.credit_points)",
                    component_values={"sum_cg": tot_cg_calc},
                    status="MISSING_AUTO_RESOLVED",
                    reason="Auto-calculated missing Total_CG from sum of course CG."
                ))
        elif abs(cur_tcg - tot_cg_calc) > 0.5 and tot_cg_calc > 0:
            discrepancies.append(DiscrepancyReport(
                seat_no=student.seat_no,
                student_name=student.student_name,
                course_code=None,
                field_name="Total_CG",
                extracted_val=student.total_cg,
                expected_val=int(tot_cg_calc) if tot_cg_calc.is_integer() else tot_cg_calc,
                discrepancy_magnitude=abs(cur_tcg - tot_cg_calc),
                formula="sum(course_results.*.credit_points)",
                component_values={"extracted": cur_tcg, "calculated": tot_cg_calc},
                status="CONFLICT",
                reason=f"Extracted Total_CG ({cur_tcg}) != sum of course CG ({tot_cg_calc})"
            ))

        # 3. Credits Earned
        cur_cr = safe_float(student.credits_earned)
        if not student.credits_earned or cur_cr == 0:
            if tot_cr_calc > 0:
                student.credits_earned = str(int(tot_cr_calc) if tot_cr_calc.is_integer() else tot_cr_calc)
        elif abs(cur_cr - tot_cr_calc) > 0.5 and tot_cr_calc > 0:
            discrepancies.append(DiscrepancyReport(
                seat_no=student.seat_no,
                student_name=student.student_name,
                course_code=None,
                field_name="Credits_Earned",
                extracted_val=student.credits_earned,
                expected_val=int(tot_cr_calc) if tot_cr_calc.is_integer() else tot_cr_calc,
                discrepancy_magnitude=abs(cur_cr - tot_cr_calc),
                formula="sum(credits where grade != 'F')",
                component_values={"extracted": cur_cr, "calculated": tot_cr_calc},
                status="CONFLICT",
                reason=f"Extracted Credits_Earned ({cur_cr}) != sum of passed course credits ({tot_cr_calc})"
            ))

        # 4. SGPI
        is_failed = (
            student.result_status.upper() in self.spec.exception_tokens.get("unsuccessful_statuses", ["FAILED", "FAIL", "ATKT", "F"])
            or any(cr.grade in self.spec.grading_contract.fail_grades for cr in student.course_results)
        )
        if is_failed:
            if not student.result_status or student.result_status.upper() in ("PASS", "P"):
                student.result_status = "FAIL"
            # Failed students legitimately have 0.00 SGPI or --
            if not student.sgpi or student.sgpi == "0":
                student.sgpi = "0.00"
        else:
            divisor = total_sched_cr or safe_float(student.credits_earned)
            dividend = tot_cg_calc or safe_float(student.total_cg)
            if divisor > 0 and dividend > 0:
                expected_sgpi = round(dividend / divisor, 2)
                cur_sgpi = safe_float(student.sgpi)
                if not student.sgpi or cur_sgpi == 0:
                    student.sgpi = f"{expected_sgpi:.2f}"
                    discrepancies.append(DiscrepancyReport(
                        seat_no=student.seat_no,
                        student_name=student.student_name,
                        course_code=None,
                        field_name="SGPI",
                        extracted_val="",
                        expected_val=student.sgpi,
                        discrepancy_magnitude=expected_sgpi,
                        formula="total_cg / total_credits",
                        component_values={"total_cg": dividend, "total_credits": divisor},
                        status="MISSING_AUTO_RESOLVED",
                        reason="Auto-calculated missing SGPI from Total_CG / Total_Credits."
                    ))
                elif abs(cur_sgpi - expected_sgpi) > 0.03:
                    discrepancies.append(DiscrepancyReport(
                        seat_no=student.seat_no,
                        student_name=student.student_name,
                        course_code=None,
                        field_name="SGPI",
                        extracted_val=student.sgpi,
                        expected_val=f"{expected_sgpi:.2f}",
                        discrepancy_magnitude=abs(cur_sgpi - expected_sgpi),
                        formula="total_cg / total_credits",
                        component_values={"total_cg": dividend, "total_credits": divisor},
                        status="CONFLICT",
                        reason=f"Extracted SGPI ({cur_sgpi}) != Total_CG / Credits ({expected_sgpi:.2f})"
                    ))

        return discrepancies

    # -------------------------------------------------------------------------
    # Anti-Column-Shifting & Permutation Lock
    # -------------------------------------------------------------------------
    def audit_and_heal_column_alignment(self, student: StudentRecord) -> List[DiscrepancyReport]:
        """
        Detects and resolves column-shifting anomalies (e.g. Theory marks shifted into Lab slots,
        or omitted elective causing off-by-one misalignment).
        Uses slot structural profiles and the Permutation Checksum Lock:
            sum(Credits * GP) == printed Total CG
        """
        discrepancies: List[DiscrepancyReport] = []
        if not self.spec.slots or not student.course_results:
            return discrepancies

        num_slots = len(self.spec.slots)
        num_cr = len(student.course_results)

        # 1. Profile violation check (e.g., Theory marks in Lab slot)
        profile_violations = []
        for i, cr in enumerate(student.course_results):
            slot = self.spec.get_slot_by_code(cr.course_code)
            if not slot and i < num_slots:
                slot = self.spec.slots[i]

            if slot:
                is_proj_slot = slot.is_project() if hasattr(slot, "is_project") else ("proj" in str(slot.course_type).lower() or "project" in str(slot.name).lower())
                is_lab_slot = (slot.is_lab() if hasattr(slot, "is_lab") else ("lab" in str(slot.course_type).lower())) and not is_proj_slot
                is_th_slot = (slot.is_theory() if hasattr(slot, "is_theory") else ("th" in str(slot.course_type).lower())) and not is_proj_slot
                max_tot = slot.get_max_tot() if hasattr(slot, "get_max_tot") else (150.0 if is_proj_slot else 50.0)

                cur_tot = safe_float(cr.marks.get("TOT"))
                cur_th = safe_float(cr.marks.get("TH"))
                if is_lab_slot:
                    if cur_tot > (max_tot + 5.0) or cur_th > 30.0:
                        profile_violations.append((i, cr, slot, f"Lab slot {slot.code} received Theory mark (TOT={cur_tot}, TH={cur_th} > max {max_tot})"))
                elif is_th_slot:
                    if (not cur_tot or cur_tot == 0) and cur_th == 0 and "TW" in cr.marks and safe_float(cr.marks.get("TW")) > 0:
                        profile_violations.append((i, cr, slot, f"Theory slot {slot.code} received Lab TW mark without Theory"))

        printed_tcg = safe_float(student.total_cg)
        calc_tcg = sum(safe_float(cr.credit_points) for cr in student.course_results)

        has_tcg_conflict = bool(printed_tcg > 0 and abs(calc_tcg - printed_tcg) > 1.0)
        has_shift_evidence = bool(profile_violations or (has_tcg_conflict and num_cr != num_slots))

        if not has_shift_evidence:
            return discrepancies

        # 2. Algebraic Permutation Alignment Test
        # Attempt to slide marks if an off-by-one shift occurred (e.g. an elective omitted or shifted)
        if printed_tcg > 0 and num_slots >= num_cr:
            best_k = -1
            best_score = -1
            for k in range(num_slots):
                test_cr_list = []
                for s_idx in range(num_slots):
                    slot = self.spec.slots[s_idx]
                    if s_idx < k:
                        cr_cand = student.course_results[s_idx] if s_idx < num_cr else None
                    elif s_idx == k:
                        cr_cand = CourseResult(course_code=slot.code, course_name=slot.name, credits=str(slot.credits), grade="ABS", grade_point="0", credit_points="0")
                    else:
                        cr_idx = s_idx - 1
                        cr_cand = student.course_results[cr_idx] if cr_idx < num_cr else None

                    if cr_cand:
                        test_cr_list.append((slot, cr_cand))

                if len(test_cr_list) == num_slots:
                    perm_cg = sum(slot.credits * safe_float(cr.grade_point) for slot, cr in test_cr_list)
                    all_profiles_ok = True
                    for slot, cr in test_cr_list:
                        is_proj = slot.is_project() if hasattr(slot, "is_project") else ("proj" in str(slot.course_type).lower() or "project" in str(slot.name).lower())
                        is_lab_slot = (slot.is_lab() if hasattr(slot, "is_lab") else ("lab" in str(slot.course_type).lower())) and not is_proj
                        max_tot = slot.get_max_tot() if hasattr(slot, "get_max_tot") else (150.0 if is_proj else 50.0)
                        if is_lab_slot and (safe_float(cr.marks.get("TOT")) > max_tot + 5.0 or safe_float(cr.marks.get("TH")) > 30.0):
                            all_profiles_ok = False
                            break

                    if all_profiles_ok and abs(perm_cg - printed_tcg) <= 0.5:
                        score = 0
                        for s_idx, (slot, cr) in enumerate(test_cr_list):
                            if s_idx == k:
                                continue
                            if cr.course_code and slot.code and (cr.course_code in slot.code or slot.code in cr.course_code):
                                score += 10
                            is_lab = slot.is_lab() if hasattr(slot, "is_lab") else False
                            if is_lab and ("TW" in cr.marks or "PR" in cr.marks):
                                score += 3
                            elif not is_lab and ("TH" in cr.marks or "IA" in cr.marks):
                                score += 3
                        if score > best_score:
                            best_score = score
                            best_k = k

            if best_k != -1:
                healed_courses = []
                for s_idx in range(num_slots):
                    slot = self.spec.slots[s_idx]
                    if s_idx < best_k:
                        orig_cr = student.course_results[s_idx]
                    elif s_idx == best_k:
                        orig_cr = CourseResult(
                            course_code=slot.code,
                            course_name=slot.name,
                            marks={"TOT": "--"},
                            grade="ABS",
                            grade_point="0",
                            credits=str(int(slot.credits) if slot.credits.is_integer() else slot.credits),
                            credit_points="0"
                        )
                    else:
                        orig_cr = student.course_results[s_idx - 1]

                    orig_cr.course_code = slot.code
                    orig_cr.course_name = slot.name or orig_cr.course_name
                    orig_cr.credits = str(int(slot.credits) if slot.credits.is_integer() else slot.credits)
                    orig_cg = round(slot.credits * safe_float(orig_cr.grade_point), 1)
                    orig_cr.credit_points = str(int(orig_cg) if orig_cg.is_integer() else orig_cg)
                    healed_courses.append(orig_cr)

                student.course_results = healed_courses
                discrepancies.append(DiscrepancyReport(
                    seat_no=student.seat_no,
                    student_name=student.student_name,
                    course_code=self.spec.slots[best_k].code,
                    field_name="Column_Alignment",
                    extracted_val=f"Shifted at Slot {best_k + 1}",
                    expected_val=f"Realigned across {num_slots} slots",
                    discrepancy_magnitude=abs(calc_tcg - printed_tcg),
                    formula="Permutation Checksum Lock: sum(C * GP) == Total_CG",
                    component_values={"best_k": best_k, "printed_tcg": printed_tcg},
                    status="MISSING_AUTO_RESOLVED",
                    reason=f"Algebraically healed off-by-one column shift at Slot {best_k + 1} ({self.spec.slots[best_k].code})."
                ))
                return discrepancies

        # If not algebraically auto-healed, report CONFLICT for Spotlight Micro-Healer
        for idx, cr, slot, viol_reason in profile_violations:
            discrepancies.append(DiscrepancyReport(
                seat_no=student.seat_no,
                student_name=student.student_name,
                course_code=cr.course_code or slot.code,
                field_name=f"Slot_{idx + 1}_Profile_Violation",
                extracted_val=cr.marks.get("TOT", cr.marks.get("TH", "")),
                expected_val=f"<= {slot.get_max_tot() if hasattr(slot, 'get_max_tot') else 50.0}",
                discrepancy_magnitude=safe_float(cr.marks.get("TOT")) - (slot.get_max_tot() if hasattr(slot, "get_max_tot") else 50.0),
                formula=f"Max TOT <= {slot.get_max_tot() if hasattr(slot, 'get_max_tot') else 50.0}",
                component_values={"marks": dict(cr.marks), "slot_profile": slot.to_dict() if hasattr(slot, "to_dict") else {}},
                status="CONFLICT",
                reason=viol_reason
            ))

        return discrepancies

    # -------------------------------------------------------------------------
    # Full Student Record Audit & Healing
    # -------------------------------------------------------------------------
    def audit_and_heal_student(self, student: StudentRecord) -> Tuple[StudentRecord, List[DiscrepancyReport]]:
        """Complete evaluation of a student: harmonizes, auto-fills missing pieces, logs conflicts."""
        all_discrepancies: List[DiscrepancyReport] = []

        # 0. Anti-Column-Shifting & Permutation Lock
        align_discs = self.audit_and_heal_column_alignment(student)
        all_discrepancies.extend(align_discs)

        # 1. Course results audit
        for cr in student.course_results:
            c_discs = self.evaluate_course_result(cr, student)
            all_discrepancies.extend(c_discs)

        # 2. Aggregates audit
        agg_discs = self.evaluate_student_aggregates(student)
        all_discrepancies.extend(agg_discs)

        return student, all_discrepancies

    def evaluate_batch(
        self,
        students: List[StudentRecord]
    ) -> Tuple[List[StudentRecord], List[DiscrepancyReport], Dict[str, Any]]:
        """Audits an entire batch of students and produces cohort-level metrics."""
        all_discrepancies: List[DiscrepancyReport] = []
        conflicts_count = 0
        missing_resolved_count = 0

        for s in students:
            s, discs = self.audit_and_heal_student(s)
            for d in discs:
                all_discrepancies.append(d)
                if d.status == "CONFLICT":
                    conflicts_count += 1
                elif d.status == "MISSING_AUTO_RESOLVED":
                    missing_resolved_count += 1

        total_students = len(students)
        verified_count = total_students - len({d.seat_no for d in all_discrepancies if d.status == "CONFLICT"})
        pass_pct = (verified_count / max(total_students, 1)) * 100.0

        summary = {
            "total_students": total_students,
            "mathematically_verified_students": verified_count,
            "pass_percentage": round(pass_pct, 1),
            "total_conflicts": conflicts_count,
            "missing_values_resolved": missing_resolved_count
        }

        return students, all_discrepancies, summary
