"""
Local Deterministic EDA Engine
===============================
Performs comprehensive academic exploratory data analysis on student result sets
using pure Python, pandas, and numpy.

Key Properties:
- 100% Mathematically Exact: No LLM approximation or calculation errors.
- 0 Token Cost: All aggregations, percentiles, histograms run in milliseconds locally.
- Schema Agnostic: Operates via SemanticMapper canonical parameters.
"""

import math
import re
from typing import Any, Dict, List, Optional, Union
import numpy as np
import pandas as pd

from .semantic_mapper import SemanticMapper


def _clean_numeric(val: Any) -> Optional[float]:
    """Clean marks/SGPI value, stripping grades, suffixes (e.g. 18F, 50E), and whitespace."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s in ("--", "-", "N/A", "null", "ABS"):
        return None
    # Strip trailing letters (e.g. 18F, 40E, 23P)
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _is_pass(status_str: Any, sgpi_val: Optional[float] = None) -> bool:
    """Determine pass status from result string or SGPI."""
    s = str(status_str).strip().upper() if status_str else ""
    if s in ("P", "PASS", "SUCCESSFUL", "PASSED"):
        return True
    if s in ("F", "FAIL", "UNSUCCESSFUL", "FAILED"):
        return False
    # Fallback to SGPI
    if sgpi_val is not None:
        return sgpi_val >= 4.0
    return False


class EDAEngine:
    """Computes comprehensive statistical and visual metrics for academic cohorts."""

    def __init__(self, semantic_mapper: Optional[SemanticMapper] = None):
        self.mapper = semantic_mapper or SemanticMapper()

    def run_analysis(
        self,
        data: Union[List[Dict[str, Any]], pd.DataFrame],
        ollama_client=None,
        cloud_client=None
    ) -> Dict[str, Any]:
        """
        Execute full EDA on student records.
        Returns a JSON-serializable dictionary with all statistics, histograms,
        subject performance breakdowns, and cohort demographics.
        """
        if isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            df = data.copy()

        if df.empty:
            return {"error": "Dataset is empty", "total_students": 0}

        # 1. Map columns dynamically via SemanticMapper
        sample_rows = df.head(3).to_dict(orient="records")
        map_res = self.mapper.map_columns(
            columns=list(df.columns),
            sample_rows=sample_rows,
            ollama_client=ollama_client,
            cloud_client=cloud_client
        )
        col_map = map_res.get("mappings", {})
        subjects_map = map_res.get("subjects", {})

        # 2. Extract Canonical Series
        seat_col = col_map.get("seat")
        name_col = col_map.get("name")
        sgpi_col = col_map.get("sgpi")
        total_col = col_map.get("total_marks")
        max_col = col_map.get("max_marks")
        result_col = col_map.get("result")
        gender_col = col_map.get("gender")
        cand_col = col_map.get("candidate_type")

        total_students = len(df)

        # Standardize SGPI
        sgpi_clean: List[Optional[float]] = []
        if sgpi_col and sgpi_col in df.columns:
            sgpi_clean = [_clean_numeric(v) for v in df[sgpi_col]]
        else:
            sgpi_clean = [None] * total_students

        # Standardize Total Marks
        marks_clean: List[Optional[float]] = []
        if total_col and total_col in df.columns:
            marks_clean = [_clean_numeric(v) for v in df[total_col]]
        else:
            marks_clean = [None] * total_students

        # Standardize Max Marks
        max_marks_val = 0.0
        if max_col and max_col in df.columns:
            valid_max = [_clean_numeric(v) for v in df[max_col] if _clean_numeric(v)]
            if valid_max:
                max_marks_val = float(max(valid_max))

        # Standardize Pass / Fail
        pass_flags: List[bool] = []
        for i in range(total_students):
            res_val = df[result_col].iloc[i] if result_col and result_col in df.columns else ""
            sgpi_v = sgpi_clean[i]
            pass_flags.append(_is_pass(res_val, sgpi_v))

        passed_count = sum(pass_flags)
        failed_count = total_students - passed_count
        pass_pct = round((passed_count / total_students) * 100, 2) if total_students > 0 else 0.0
        fail_pct = round((failed_count / total_students) * 100, 2) if total_students > 0 else 0.0

        # Numeric SGPI calculations (ignoring NaNs)
        valid_sgpi = [s for s in sgpi_clean if s is not None and not math.isnan(s)]
        if valid_sgpi:
            sgpi_arr = np.array(valid_sgpi)
            avg_sgpi = round(float(np.mean(sgpi_arr)), 2)
            median_sgpi = round(float(np.median(sgpi_arr)), 2)
            std_sgpi = round(float(np.std(sgpi_arr)), 2)
            max_sgpi = round(float(np.max(sgpi_arr)), 2)
            min_sgpi = round(float(np.min(sgpi_arr)), 2)
            p25_sgpi = round(float(np.percentile(sgpi_arr, 25)), 2)
            p75_sgpi = round(float(np.percentile(sgpi_arr, 75)), 2)
        else:
            avg_sgpi = median_sgpi = std_sgpi = max_sgpi = min_sgpi = p25_sgpi = p75_sgpi = 0.0

        # Numeric Marks calculations
        valid_marks = [m for m in marks_clean if m is not None and not math.isnan(m)]
        if valid_marks:
            marks_arr = np.array(valid_marks)
            avg_marks = round(float(np.mean(marks_arr)), 1)
            median_marks = round(float(np.median(marks_arr)), 1)
            max_marks = round(float(np.max(marks_arr)), 1)
            min_marks = round(float(np.min(marks_arr)), 1)
        else:
            avg_marks = median_marks = max_marks = min_marks = 0.0

        # Grade Classifications
        distinction = sum(1 for s in valid_sgpi if s >= 7.5)
        first_class = sum(1 for s in valid_sgpi if 6.75 <= s < 7.5)
        second_class = sum(1 for s in valid_sgpi if 6.0 <= s < 6.75)
        pass_class = sum(1 for s in valid_sgpi if 4.0 <= s < 6.0)
        atkt_fail = total_students - (distinction + first_class + second_class + pass_class)

        # SGPI Histogram Buckets for Charts
        hist_buckets = [
            {"label": "< 4.0 (Fail)", "min": 0.0, "max": 3.99, "count": 0},
            {"label": "4.0 - 4.99 (Pass)", "min": 4.0, "max": 4.99, "count": 0},
            {"label": "5.0 - 5.99 (Second Class)", "min": 5.0, "max": 5.99, "count": 0},
            {"label": "6.0 - 6.99 (Higher Second)", "min": 6.0, "max": 6.99, "count": 0},
            {"label": "7.0 - 7.99 (First Class)", "min": 7.0, "max": 7.99, "count": 0},
            {"label": "8.0 - 8.99 (Distinction)", "min": 8.0, "max": 8.99, "count": 0},
            {"label": "9.0 - 10.0 (Outstanding)", "min": 9.0, "max": 10.0, "count": 0},
        ]
        for s in valid_sgpi:
            matched = False
            for b in hist_buckets:
                if b["min"] <= s <= b["max"] or (b["label"].startswith("<") and s < 4.0):
                    b["count"] += 1
                    matched = True
                    break
            if not matched and s >= 10.0:
                hist_buckets[-1]["count"] += 1

        for b in hist_buckets:
            b["percentage"] = round((b["count"] / total_students) * 100, 1) if total_students > 0 else 0.0

        # 3. Subject-Wise Analytics
        subject_stats: List[Dict[str, Any]] = []
        for code, comps in subjects_map.items():
            tot_col = comps.get("TOT") or comps.get("TOTAL") or comps.get("TW") or comps.get("SE")
            subj_col = comps.get("SUBJECT")
            subj_name = str(df[subj_col].iloc[0]) if subj_col and subj_col in df.columns and not pd.isna(df[subj_col].iloc[0]) else code

            # Component marks
            se_col = comps.get("SE")
            ia_col = comps.get("IA")
            tw_col = comps.get("TW")
            grade_col = comps.get("GRADE")

            appeared = 0
            passed_sub = 0
            sub_marks: List[float] = []
            se_marks: List[float] = []
            ia_marks: List[float] = []
            tw_marks: List[float] = []

            for i in range(total_students):
                row_val = df[tot_col].iloc[i] if tot_col and tot_col in df.columns else None
                m_num = _clean_numeric(row_val)
                g_val = str(df[grade_col].iloc[i]).strip().upper() if grade_col and grade_col in df.columns else ""

                if se_col and se_col in df.columns:
                    se_num = _clean_numeric(df[se_col].iloc[i])
                    if se_num is not None:
                        se_marks.append(se_num)

                if ia_col and ia_col in df.columns:
                    ia_num = _clean_numeric(df[ia_col].iloc[i])
                    if ia_num is not None:
                        ia_marks.append(ia_num)

                if tw_col and tw_col in df.columns:
                    tw_num = _clean_numeric(df[tw_col].iloc[i])
                    if tw_num is not None:
                        tw_marks.append(tw_num)

                if m_num is not None or g_val:
                    appeared += 1
                    if m_num is not None:
                        sub_marks.append(m_num)

                    # Pass condition: Grade != 'F' or TOT >= 40% (default pass benchmark)
                    if g_val:
                        if g_val not in ("F", "FAIL"):
                            passed_sub += 1
                    elif m_num is not None:
                        if m_num >= 40.0:
                            passed_sub += 1

            if appeared > 0:
                sub_pass_pct = round((passed_sub / appeared) * 100, 2)
                sub_avg = round(float(np.mean(sub_marks)), 1) if sub_marks else 0.0
                sub_max = round(float(np.max(sub_marks)), 1) if sub_marks else 0.0
                sub_min = round(float(np.min(sub_marks)), 1) if sub_marks else 0.0

                subject_stats.append({
                    "code": code,
                    "name": subj_name,
                    "total_appeared": appeared,
                    "passed": passed_sub,
                    "failed": appeared - passed_sub,
                    "pass_pct": sub_pass_pct,
                    "avg_marks": sub_avg,
                    "max_marks": sub_max,
                    "min_marks": sub_min,
                    "avg_se": round(float(np.mean(se_marks)), 1) if se_marks else None,
                    "avg_ia": round(float(np.mean(ia_marks)), 1) if ia_marks else None,
                    "avg_tw": round(float(np.mean(tw_marks)), 1) if tw_marks else None,
                    "has_se": bool(se_col and se_col in df.columns),
                    "has_ia": bool(ia_col and ia_col in df.columns),
                    "has_tw": bool(tw_col and tw_col in df.columns),
                })

        # Identify bottleneck / toughest subjects (lowest pass rate)
        subject_stats.sort(key=lambda x: x["pass_pct"])
        toughest_subject = subject_stats[0] if subject_stats else None
        top_scoring_subject = max(subject_stats, key=lambda x: x["avg_marks"]) if subject_stats else None

        # 4. Demographic Segmentations
        # Regular vs Diploma
        demographics = {
            "candidate_type": {
                "regular": {"count": 0, "passed": 0, "pass_pct": 0.0, "avg_sgpi": 0.0},
                "diploma": {"count": 0, "passed": 0, "pass_pct": 0.0, "avg_sgpi": 0.0}
            },
            "gender": {
                "male": {"count": 0, "passed": 0, "pass_pct": 0.0, "avg_sgpi": 0.0},
                "female": {"count": 0, "passed": 0, "pass_pct": 0.0, "avg_sgpi": 0.0}
            }
        }

        # Segment: Candidate Type
        reg_sgpis, dip_sgpis = [], []
        if cand_col and cand_col in df.columns:
            for i in range(total_students):
                c_val = str(df[cand_col].iloc[i]).strip().lower()
                is_p = pass_flags[i]
                sg_v = sgpi_clean[i]

                if "dip" in c_val or "lateral" in c_val:
                    demographics["candidate_type"]["diploma"]["count"] += 1
                    if is_p:
                        demographics["candidate_type"]["diploma"]["passed"] += 1
                    if sg_v is not None:
                        dip_sgpis.append(sg_v)
                else:
                    demographics["candidate_type"]["regular"]["count"] += 1
                    if is_p:
                        demographics["candidate_type"]["regular"]["passed"] += 1
                    if sg_v is not None:
                        reg_sgpis.append(sg_v)

            reg_cnt = demographics["candidate_type"]["regular"]["count"]
            dip_cnt = demographics["candidate_type"]["diploma"]["count"]

            if reg_cnt > 0:
                demographics["candidate_type"]["regular"]["pass_pct"] = round(
                    (demographics["candidate_type"]["regular"]["passed"] / reg_cnt) * 100, 2
                )
                demographics["candidate_type"]["regular"]["avg_sgpi"] = round(float(np.mean(reg_sgpis)), 2) if reg_sgpis else 0.0

            if dip_cnt > 0:
                demographics["candidate_type"]["diploma"]["pass_pct"] = round(
                    (demographics["candidate_type"]["diploma"]["passed"] / dip_cnt) * 100, 2
                )
                demographics["candidate_type"]["diploma"]["avg_sgpi"] = round(float(np.mean(dip_sgpis)), 2) if dip_sgpis else 0.0
        else:
            # All regular default
            demographics["candidate_type"]["regular"] = {
                "count": total_students,
                "passed": passed_count,
                "pass_pct": pass_pct,
                "avg_sgpi": avg_sgpi
            }

        # Segment: Gender
        male_sgpis, female_sgpis = [], []
        if gender_col and gender_col in df.columns:
            for i in range(total_students):
                g_val = str(df[gender_col].iloc[i]).strip().upper()
                is_p = pass_flags[i]
                sg_v = sgpi_clean[i]

                if g_val.startswith("F") or g_val == "FEMALE":
                    demographics["gender"]["female"]["count"] += 1
                    if is_p:
                        demographics["gender"]["female"]["passed"] += 1
                    if sg_v is not None:
                        female_sgpis.append(sg_v)
                else:
                    demographics["gender"]["male"]["count"] += 1
                    if is_p:
                        demographics["gender"]["male"]["passed"] += 1
                    if sg_v is not None:
                        male_sgpis.append(sg_v)

            m_cnt = demographics["gender"]["male"]["count"]
            f_cnt = demographics["gender"]["female"]["count"]

            if m_cnt > 0:
                demographics["gender"]["male"]["pass_pct"] = round(
                    (demographics["gender"]["male"]["passed"] / m_cnt) * 100, 2
                )
                demographics["gender"]["male"]["avg_sgpi"] = round(float(np.mean(male_sgpis)), 2) if male_sgpis else 0.0

            if f_cnt > 0:
                demographics["gender"]["female"]["pass_pct"] = round(
                    (demographics["gender"]["female"]["passed"] / f_cnt) * 100, 2
                )
                demographics["gender"]["female"]["avg_sgpi"] = round(float(np.mean(female_sgpis)), 2) if female_sgpis else 0.0

        # 5. Top Performers (Ranked by SGPI then Total Marks)
        student_records = []
        for i in range(total_students):
            st = {
                "seat_no": str(df[seat_col].iloc[i]) if seat_col and seat_col in df.columns else f"#{i+1}",
                "student_name": str(df[name_col].iloc[i]) if name_col and name_col in df.columns else "Student",
                "sgpi": sgpi_clean[i] if sgpi_clean[i] is not None else 0.0,
                "total_marks": marks_clean[i] if marks_clean[i] is not None else 0.0,
                "result": "Pass" if pass_flags[i] else "Fail"
            }
            student_records.append(st)

        sorted_performers = sorted(
            student_records,
            key=lambda x: (x["sgpi"], x["total_marks"]),
            reverse=True
        )
        top_performers = []
        for rank, p in enumerate(sorted_performers[:5], 1):
            top_performers.append({
                "rank": rank,
                "seat_no": p["seat_no"],
                "student_name": p["student_name"],
                "sgpi": p["sgpi"],
                "total_marks": p["total_marks"]
            })

        # 6. Remedial Intervention Alerts (Students failing or SGPI < 4.5)
        remedial_alerts = []
        for i in range(total_students):
            if not pass_flags[i] or (sgpi_clean[i] is not None and sgpi_clean[i] < 4.5):
                remedial_alerts.append({
                    "seat_no": str(df[seat_col].iloc[i]) if seat_col and seat_col in df.columns else f"#{i+1}",
                    "student_name": str(df[name_col].iloc[i]) if name_col and name_col in df.columns else "Student",
                    "sgpi": sgpi_clean[i] if sgpi_clean[i] is not None else 0.0,
                    "reason": "Failed semester" if not pass_flags[i] else "Low SGPI (< 4.5)"
                })

        return {
            "overview": {
                "total_students": total_students,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "pass_percentage": pass_pct,
                "fail_percentage": fail_pct,
                "avg_sgpi": avg_sgpi,
                "median_sgpi": median_sgpi,
                "std_sgpi": std_sgpi,
                "max_sgpi": max_sgpi,
                "min_sgpi": min_sgpi,
                "p25_sgpi": p25_sgpi,
                "p75_sgpi": p75_sgpi,
                "avg_total_marks": avg_marks,
                "median_total_marks": median_marks,
                "max_total_marks": max_marks,
                "min_total_marks": min_marks,
                "max_possible_marks": max_marks_val,
                "distinction_count": distinction,
                "first_class_count": first_class,
                "second_class_count": second_class,
                "pass_class_count": pass_class,
                "atkt_fail_count": atkt_fail,
            },
            "sgpi_distribution": hist_buckets,
            "subject_analytics": subject_stats,
            "toughest_subject": toughest_subject,
            "top_scoring_subject": top_scoring_subject,
            "demographics": demographics,
            "top_performers": top_performers,
            "remedial_alerts": remedial_alerts[:15],
            "mapping_meta": {
                "tier_used": map_res.get("tier_used"),
                "token_usage": map_res.get("token_usage", 0),
                "mapped_columns": col_map
            }
        }
