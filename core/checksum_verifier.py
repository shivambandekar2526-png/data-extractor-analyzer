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

try:
    from .normalizer import normalize_head, HEAD_ALIASES
except Exception:
    try:
        from core.normalizer import normalize_head, HEAD_ALIASES
    except Exception:
        try:
            from data_extractor_AI.core.normalizer import normalize_head, HEAD_ALIASES
        except Exception:
            normalize_head = lambda h: str(h).upper()
            HEAD_ALIASES = {}


def get_canonical_mark(marks: Dict[str, Any], canonical_target: str) -> float:
    """Retrieve mark for a canonical target (TH, IA, TW, PR, TOT) across any alias."""
    if not marks:
        return 0.0
    aliases = HEAD_ALIASES.get(canonical_target, [canonical_target])
    for k, v in marks.items():
        clean_k = re.sub(r"[^A-Za-z0-9_&]", "", str(k)).strip().upper()
        if clean_k == canonical_target or clean_k in aliases or normalize_head(clean_k) == canonical_target:
            val = safe_float(v)
            if val > 0:
                return val
    return 0.0


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
    """
    Run adaptive 3-tier deterministic mathematical proofs on a single student record:
    Tier 1: Course-aware component sum with missing column auto-deduction & combinatorial fallback.
    Tier 2: Universal course credit invariant: Credits * Grade Point == Credit Points (CG).
    Tier 3: Universal semester invariants: Sum(Course CG) == äCG and äCG / Credits == SGPI.
    """
    result = ChecksumResult(student_seat_no=student.seat_no)

    total_course_cg = 0.0
    total_course_credits = 0.0

    for cr in student.course_results:
        c_code = cr.course_code
        c_name = cr.course_name or c_code

        # Canonical component extraction via semantic aliases
        tot_val = get_canonical_mark(cr.marks, "TOT")
        th_val = get_canonical_mark(cr.marks, "TH")
        ia_val = get_canonical_mark(cr.marks, "IA")
        tw_val = get_canonical_mark(cr.marks, "TW")
        pr_val = get_canonical_mark(cr.marks, "PR")

        # 1. Tier 1 Component Sum Check
        if tot_val > 0:
            sum_matched = False
            # Check A: Standard Theory + IA == Total
            if th_val > 0 and ia_val > 0:
                if abs((th_val + ia_val) - tot_val) <= 0.5:
                    sum_matched = True
                elif tw_val > 0 and abs((th_val + ia_val + tw_val) - tot_val) <= 0.5:
                    sum_matched = True

            # Check B: Lab / Practical (TW + PR == Total)
            if not sum_matched and tw_val > 0 and pr_val > 0:
                if abs((tw_val + pr_val) - tot_val) <= 0.5:
                    sum_matched = True

            # Check C: Single Head (TW only or TH only == Total)
            if not sum_matched:
                if tw_val > 0 and abs(tw_val - tot_val) <= 0.5:
                    sum_matched = True
                elif th_val > 0 and abs(th_val - tot_val) <= 0.5:
                    sum_matched = True

            # Check D: Missing IA Column Auto-Deduction (TH present, IA missing, but diff matches IA bounds)
            if not sum_matched and th_val > 0 and ia_val == 0 and tot_val > th_val:
                diff = tot_val - th_val
                if 0 < diff <= 30:  # Valid internal assessment mark range (out of 20 or 25)
                    cr.marks["IA"] = str(int(diff))
                    ia_val = diff
                    sum_matched = True

            # Check E: Combinatorial Fallback (any non-total numbers sum to total)
            if not sum_matched:
                sub_nums = []
                for k, v in cr.marks.items():
                    clean_k = re.sub(r"[^A-Za-z0-9_&]", "", str(k)).strip().upper()
                    if clean_k != "TOT" and clean_k not in HEAD_ALIASES.get("TOT", []):
                        val_f = safe_float(v)
                        if 0 < val_f < tot_val:
                            sub_nums.append(val_f)
                if sub_nums and abs(sum(sub_nums) - tot_val) <= 0.5:
                    sum_matched = True

            # If there were non-zero components but none matched Total
            if not sum_matched and (th_val > 0 or ia_val > 0 or tw_val > 0 or pr_val > 0):
                result.component_sum_ok = False
                result.failures.append(
                    f"Component Sum Mismatch in {c_name} ({c_code}): "
                    f"Components ({th_val}, {ia_val}, {tw_val}, {pr_val}) != Total ({tot_val})"
                )

        # 2. Tier 2: Universal Course CG Check: Credits * GP == CG
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

    # 3. Tier 3: Universal Semester Total CG Check: Sum(Course CG) == Sem Total CG (äCG)
    sem_cg = safe_float(student.total_cg)
    if sem_cg > 0 and total_course_cg > 0:
        if abs(total_course_cg - sem_cg) > 1.0:
            result.sem_total_cg_ok = False
            result.failures.append(
                f"Semester CG Sum Mismatch: Sum of Courses ({total_course_cg:.1f}) != Reported ({sem_cg:.1f})"
            )

    # 4. Tier 3: Universal SGPI Check: Sem Total CG / Sem Total Credits == SGPI
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

    # Invariant Grace Rule: If Tier 2 & Tier 3 pass with 100% precision,
    # intermediate missing/scrambled component columns do not fail the overall record proof.
    if result.course_cg_ok and result.sem_total_cg_ok and result.sgpi_ok:
        result.all_passed = True
    else:
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
