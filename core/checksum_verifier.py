"""
Mathematical Checksum Verification Engine
=========================================
Runs deterministic zero-cost mathematical verification proofs on extracted student records:
1. Component Sum: Theory/SE + Internal/IA == Total Marks
2. Course CG: Credits * Grade Point == Credit Points (CG)
3. Semester CG: Sum(Course CG) == Semester Total CG (äCG)
4. SGPI Accuracy: Semester Total CG / Semester Total Credits == SGPI
"""

import re
from typing import Any, Dict, List, Optional

try:
    from ..models import ChecksumResult, CourseCatalog, StudentRecord
except Exception:
    try:
        from data_extractor_AI.models import ChecksumResult, CourseCatalog, StudentRecord
    except Exception:
        from models import ChecksumResult, CourseCatalog, StudentRecord


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert strings or numbers to float, stripping grace/annotation markers."""
    if val is None:
        return default
    s = str(val).strip()
    if not s or s in ("--", "AA", "AB", "ABS", "N/A", "null", "None"):
        return default
    # Handle grace mark annotations: "20*4" -> 20 + 4 = 24, "18@2" -> 18 + 2 = 20
    if "*" in s:
        parts = s.split("*")
        try:
            p0 = re.sub(r"[^\d\.]", "", parts[0])
            p1 = re.sub(r"[^\d\.]", "", parts[1])
            return float(p0) + float(p1)
        except Exception:
            pass
    if "@" in s:
        parts = s.split("@")
        try:
            p0 = re.sub(r"[^\d\.]", "", parts[0])
            p1 = re.sub(r"[^\d\.]", "", parts[1])
            return float(p0) + float(p1)
        except Exception:
            pass
    cleaned = re.sub(r"[^\d\.]", "", s)
    try:
        return float(cleaned) if cleaned else default
    except (ValueError, TypeError):
        return default


def verify_student(student: StudentRecord, catalog: Optional[CourseCatalog] = None) -> ChecksumResult:
    """Run 4 deterministic mathematical proofs on a single student record."""
    result = ChecksumResult(student_seat_no=student.seat_no)

    total_course_cg = 0.0
    total_course_credits = 0.0

    for cr in student.course_results:
        c_code = cr.course_code
        c_name = cr.course_name or c_code

        # 1. Component Sum Check: SE/TH + IA/IN == TOT
        tot_val = safe_float(cr.marks.get("TOT", cr.marks.get("Total", "")))
        th_val = safe_float(cr.marks.get("SE", cr.marks.get("TH", cr.marks.get("Head1", ""))))
        ia_val = safe_float(cr.marks.get("IA", cr.marks.get("IN", cr.marks.get("Head2", ""))))

        if tot_val > 0 and (th_val > 0 or ia_val > 0):
            if abs((th_val + ia_val) - tot_val) > 0.5:
                # Check if third head like TW attached to theory
                tw_val = safe_float(cr.marks.get("TW", ""))
                if abs((th_val + ia_val + tw_val) - tot_val) > 0.5:
                    result.component_sum_ok = False
                    result.failures.append(
                        f"Component Sum Mismatch in {c_name} ({c_code}): "
                        f"{th_val} + {ia_val} != {tot_val}"
                    )

        # 2. Course CG Check: Credits * GP == CG
        cr_val = safe_float(cr.credits)
        gp_val = safe_float(cr.grade_point)
        cg_val = safe_float(cr.credit_points)

        if cr_val > 0 and gp_val > 0 and cg_val > 0:
            expected_cg = cr_val * gp_val
            if abs(expected_cg - cg_val) > 0.5:
                result.course_cg_ok = False
                result.failures.append(
                    f"Course CG Mismatch in {c_name} ({c_code}): "
                    f"{cr_val} * {gp_val} = {expected_cg:.1f} != {cg_val}"
                )
            total_course_cg += cg_val
            total_course_credits += cr_val
        elif cg_val > 0:
            total_course_cg += cg_val
        elif cr_val > 0:
            total_course_credits += cr_val

    # 3. Semester Total CG Check: Sum(Course CG) == Sem Total CG (äCG)
    sem_cg = safe_float(student.total_cg)
    if sem_cg > 0 and total_course_cg > 0:
        if abs(total_course_cg - sem_cg) > 1.0:
            result.sem_total_cg_ok = False
            result.failures.append(
                f"Semester CG Sum Mismatch: Sum of Courses ({total_course_cg:.1f}) != Reported ({sem_cg:.1f})"
            )

    # 4. SGPI Check: Sem Total CG / Sem Total Credits == SGPI
    sem_credits = safe_float(student.credits_earned)
    sgpi = safe_float(student.sgpi)

    credits_to_use = sem_credits if sem_credits > 0 else total_course_credits
    cg_to_use = sem_cg if sem_cg > 0 else total_course_cg

    if credits_to_use > 0 and cg_to_use > 0 and sgpi > 0:
        expected_sgpi = cg_to_use / credits_to_use
        if abs(expected_sgpi - sgpi) > 0.05:
            result.sgpi_ok = False
            result.failures.append(
                f"SGPI Equation Mismatch: {cg_to_use:.1f} / {credits_to_use:.1f} = {expected_sgpi:.2f} != {sgpi:.2f}"
            )

    result.all_passed = (
        result.component_sum_ok
        and result.course_cg_ok
        and result.sem_total_cg_ok
        and result.sgpi_ok
    )
    return result


def verify_batch(students: List[StudentRecord], catalog: Optional[CourseCatalog] = None) -> Dict[str, Any]:
    """Verify all student records in an extraction batch."""
    total = len(students)
    passed = 0
    failures_summary: List[Dict[str, Any]] = []

    for s in students:
        r = verify_student(s, catalog)
        if r.all_passed:
            passed += 1
        else:
            failures_summary.append({
                "seat_no": s.seat_no,
                "student_name": s.student_name,
                "reasons": r.failures
            })

    pass_pct = (passed / total * 100.0) if total > 0 else 0.0
    return {
        "total_verified": total,
        "all_passed": passed,
        "failed_count": total - passed,
        "pass_percentage": round(pass_pct, 1),
        "failures": failures_summary[:20]
    }
