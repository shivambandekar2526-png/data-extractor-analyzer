"""
Academic Classifier & Subject-Wise Analysis Engine
==================================================
Deterministic statistical analysis for subject-level examination results.
Provides data cleaning, dynamic subject extraction, pass/fail metrics,
institutional classifications (Distinction, First Class, Second Class, Pass Class),
First Class & Distinction distribution, unified Theory + Lab subject combination,
adaptive marks distributions, Term Work analysis, Practical (PR) analysis,
and Top 5 student ranking based on total subject results.
"""

import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import numpy as np
import pandas as pd


class AcademicClassificationRules:
    """
    Configurable grading and classification thresholds conforming to
    university examination standards (e.g. Mumbai University / CBCS / NEP 2020).
    """

    DEFAULT_CLASSIFICATIONS = [
        {"name": "First Class with Distinction", "min_pct": 75.0, "max_pct": 100.0, "short": "Distinction"},
        {"name": "First Class", "min_pct": 60.0, "max_pct": 74.99, "short": "First Class"},
        {"name": "Second Class", "min_pct": 50.0, "max_pct": 59.99, "short": "Second Class"},
        {"name": "Pass Class", "min_pct": 40.0, "max_pct": 49.99, "short": "Pass Class"},
    ]

    GRADE_TO_CLASSIFICATION = {
        "O": "First Class with Distinction",
        "A+": "First Class with Distinction",
        "A": "First Class",
        "B+": "First Class",
        "B": "Second Class",
        "C": "Second Class",
        "D": "Pass Class",
        "E": "Pass Class",
        "P": "Pass Class",
        "F": "Fail",
    }

    @classmethod
    def classify_percentage(cls, pct: float, pass_threshold_pct: float = 40.0) -> str:
        """Determines academic classification based on percentage."""
        if pct is None or math.isnan(pct):
            return "Unknown"
        if pct >= 75.0:
            return "First Class with Distinction"
        elif pct >= 60.0:
            return "First Class"
        elif pct >= 50.0:
            return "Second Class"
        elif pct >= pass_threshold_pct:
            return "Pass Class"
        return "Fail"

    @classmethod
    def classify_grade(cls, grade_str: str) -> Optional[str]:
        """Maps letter grade to institutional classification."""
        if not grade_str:
            return None
        g = str(grade_str).strip().upper()
        return cls.GRADE_TO_CLASSIFICATION.get(g)


def clean_mark_entry(raw_val: Any) -> Tuple[str, Optional[float], bool]:
    """
    Validates and cleans a mark entry.
    Returns:
        status: 'VALID', 'ABSENT', 'MISSING', 'INVALID', 'NOT_APPLICABLE'
        numeric_val: float value if valid, else None
        is_fail_flag: True if mark has fail indicators (e.g. suffix 'F')
    """
    if raw_val is None or pd.isna(raw_val):
        return "MISSING", None, False

    s = str(raw_val).strip()
    if not s or s.upper() in ("--", "-", "NULL", "NONE", "NAN", ""):
        return "MISSING", None, False

    u = s.upper()
    if u in ("ABS", "AB", "A", "ABSENT"):
        return "ABSENT", None, False

    if u in ("N/A", "NA", "NOT APPLICABLE", "NE", "NOT ELIGIBLE"):
        return "NOT_APPLICABLE", None, False

    # Check for fail suffix (e.g. 18F, 22F, or standalone F)
    has_f_suffix = bool(re.search(r"[0-9]\s*F\b", u)) or (len(u) > 1 and u.endswith("F"))

    # Match numeric portion
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)", s)
    if m:
        try:
            val = float(m.group(1))
            return "VALID", val, has_f_suffix
        except ValueError:
            return "INVALID", None, False

    return "INVALID", None, False


def calculate_adaptive_intervals(max_marks: float) -> List[Tuple[float, float, str]]:
    """
    Determines adaptive intervals based on the maximum marks.
    Avoids hardcoding intervals.
    """
    max_m = float(max_marks) if max_marks and max_marks > 0 else 100.0

    if max_m <= 25:
        step = 5.0
    elif max_m <= 50:
        step = 10.0
    elif max_m <= 80:
        step = 10.0
    elif max_m <= 100:
        step = 10.0
    elif max_m <= 150:
        step = 20.0
    else:
        step = 25.0

    intervals = []
    current = 0.0
    while current < max_m:
        next_step = min(current + step, max_m)
        label = f"{int(current)}–{int(next_step)}" if (current.is_integer() and next_step.is_integer()) else f"{current:.1f}–{next_step:.1f}"
        intervals.append((current, next_step, label))
        current = next_step

    return intervals


def compute_marks_distribution(
    valid_marks: List[float],
    max_marks: float,
    appeared_count: int
) -> Dict[str, Any]:
    """Generates an adaptive marks distribution table and chart-ready structure."""
    intervals = calculate_adaptive_intervals(max_marks)
    total_app = max(1, appeared_count)

    table = []
    labels = []
    data_counts = []
    data_pcts = []

    for i, (start, end, label) in enumerate(intervals):
        if i == len(intervals) - 1:
            # Include right boundary in last interval
            count = sum(1 for m in valid_marks if start <= m <= end)
        else:
            count = sum(1 for m in valid_marks if start <= m < end)

        pct = round((count / total_app) * 100.0, 2)
        table.append({
            "range": label,
            "students": count,
            "percentage": pct
        })
        labels.append(label)
        data_counts.append(count)
        data_pcts.append(pct)

    return {
        "max_marks": max_marks,
        "table": table,
        "chart": {
            "labels": labels,
            "counts": data_counts,
            "percentages": data_pcts
        }
    }


def normalize_token(s: str) -> str:
    """Strips all whitespace and non-alphanumeric symbols and converts to uppercase."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


NON_COURSE_CODES: Set[str] = {
    "SEAT", "SEATNO", "SEAT_NO", "SEAT NO", "PRN", "PRN_NO", "PRN NO",
    "SGPI", "CGPI", "RESULT", "TOTAL", "ABC", "ABC_ID", "ABC ID",
    "STUDENT", "STUDENT_NAME", "STUDENT NAME", "NAME", "NAME OF STUDENT",
    "CANDIDATE", "CANDIDATE_TYPE", "CANDIDATE TYPE", "MOTHER", "MOTHER_NAME", "MOTHER NAME",
    "GENDER", "CREDITS", "CREDIT", "CREDITS_EARNED", "TOTAL_CG", "TOTAL MARKS", "TOTAL_MARKS",
    "REMARK", "REMARKS", "SR", "NO", "SR_NO", "SR. NO.", "SR. NO", "SR NO",
    "SEM", "SEMESTER", "MAX", "MAX_MARKS", "COLLEGE", "EXAM", "KT", "KT_COUNT", "KT COUNT",
    "CUSTOM", "CUSTOM_REMARKS", "STATUS", "RESULT_STATUS", "DATE", "COMPONENT", "ID",
    "TYPE", "TITLE", "ROLL", "ROLL_NO", "ROLL NO"
}


HEAD_INDICATORS = {
    "TH": ["TH", "THEORY", "SE", "ESE", "SEM", "ENDSEM", "TH_OR", "TH_ESE", "TH-ESE", "THEORY_EXAM"],
    "PR": ["PR", "PRACTICAL", "PRA", "PRACT", "LAB", "PRAC"],
    "OR": ["OR", "ORAL", "VIVA", "VO"],
    "ORPR": ["ORPR", "OR_PR", "PR_OR", "PO", "ORAL_PRACTICAL", "PR_AND_OR"],
    "TW": ["TW", "TERMWORK", "TERM_WORK", "IA_TW"],
    "IA": ["IA", "INTERNAL", "TEST", "IN", "IAT", "CA", "IA_TW"],
    "TOT": ["TOT", "TOTAL", "TOTAL_MARKS", "TOT_MARKS"]
}


def find_column_for_head(
    columns: List[str],
    subject_code: str,
    head_type: str,
    df: Optional[pd.DataFrame] = None,
    subject_name: Optional[str] = None
) -> Optional[str]:
    """
    Robustly finds the exact column corresponding to subject code/title and head type.
    Distinguishes strictly between Theory (TH), Practical (PR), Oral (OR), and Term Work (TW).
    Supports underscores, hyphens, spaces, parentheses, and handles naming anomalies.
    If multiple candidate columns match and df is supplied, picks the column with valid data.
    """
    code_raw = str(subject_code or "").strip()
    code_norm = normalize_token(code_raw)
    head_upper = str(head_type or "").upper().strip()

    if not code_norm or not head_upper:
        return None

    # Determine allowed tokens for this head
    if head_upper == "TH":
        target_tokens = ["TH", "THEORY", "SE", "ESE", "SEM", "ENDSEM", "TH_OR", "TH_ESE", "THESE"]
    elif head_upper == "PR":
        target_tokens = ["PR", "PRACTICAL", "PRA", "PRACT", "LAB", "PRAC", "ORPR", "OR_PR", "PR_OR", "PO"]
    elif head_upper == "OR":
        target_tokens = ["OR", "ORAL", "VIVA", "VO", "ORPR", "OR_PR", "PR_OR", "PO"]
    elif head_upper in ("ORPR", "PO", "OR_PR"):
        target_tokens = ["ORPR", "OR_PR", "PR_OR", "PO", "ORAL_PRACTICAL"]
    elif head_upper == "TW":
        target_tokens = ["TW", "TERMWORK", "TERM_WORK", "IA_TW", "IATW"]
    elif head_upper == "IA":
        target_tokens = ["IA", "INTERNAL", "TEST", "IN", "IAT", "CA", "IA_TW"]
    elif head_upper == "TOT":
        target_tokens = ["TOT", "TOTAL", "TOTALMARKS", "TOT_MARKS"]
    else:
        target_tokens = [head_upper]

    candidates: List[Tuple[int, str]] = []

    name_norm = normalize_token(subject_name) if subject_name else ""

    for c in columns:
        c_str = str(c).strip()
        c_upper = c_str.upper()
        c_norm = normalize_token(c_str)

        parts = re.split(r"[\s_\-\(\)\/]+", c_upper)
        parts = [p for p in parts if p]
        if not parts:
            continue

        prefix_norm = ""
        head_token = ""

        # Scan for matching head tokens from the end backwards
        for i in range(len(parts) - 1, -1, -1):
            cand_head = parts[i]
            if cand_head in target_tokens or (cand_head.startswith("TH") and head_upper == "TH"):
                head_token = cand_head
                prefix_parts = parts[:i]
                prefix_norm = normalize_token("".join(prefix_parts))
                break

        # Fallback: composite head like "TH_OR", "IA_TW"
        if not head_token and len(parts) >= 2:
            composite = f"{parts[-2]}_{parts[-1]}"
            if composite in target_tokens:
                head_token = composite
                prefix_norm = normalize_token("".join(parts[:-2]))

        code_matched = False
        if prefix_norm and (prefix_norm == code_norm or prefix_norm.startswith(code_norm) or code_norm.startswith(prefix_norm)):
            code_matched = True
        elif name_norm and prefix_norm and (prefix_norm == name_norm or name_norm in prefix_norm or prefix_norm in name_norm):
            code_matched = True

        if code_matched and head_token:
            priority = 10
            if head_token == head_upper or f"_{head_upper}" in c_upper:
                priority += 20
            if c_upper.startswith(code_raw.upper()):
                priority += 10
            if head_upper == "PR" and "OR" in head_token and "PR" not in head_token:
                continue
            if head_upper == "OR" and "PR" in head_token and "OR" not in head_token:
                continue
            candidates.append((priority, c_str))
            continue

        # Alternative direct pattern check
        for tok in target_tokens:
            pat_exact = f"{code_norm}_{tok}"
            pat_spaced = f"{code_raw}_{tok}".upper()
            if c_norm == normalize_token(pat_exact) or c_norm == normalize_token(pat_spaced):
                candidates.append((30, c_str))
                break
            if c_norm.startswith(code_norm) and c_norm.endswith(tok):
                if head_upper == "PR" and tok.startswith("OR") and "PR" not in tok:
                    continue
                if head_upper == "OR" and tok.startswith("PR") and "OR" not in tok:
                    continue
                candidates.append((25, c_str))
                break

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    if df is not None and not df.empty:
        best_col = None
        best_valid_count = -1
        top_score = candidates[0][0]
        top_candidates = [c[1] for c in candidates if c[0] >= top_score - 10]

        for cand_col in top_candidates:
            if cand_col in df.columns:
                valid_cnt = 0
                for v in df[cand_col].dropna():
                    st, _, _ = clean_mark_entry(v)
                    if st == "VALID":
                        valid_cnt += 1
                if valid_cnt > best_valid_count:
                    best_valid_count = valid_cnt
                    best_col = cand_col

        if best_col and best_valid_count > 0:
            return best_col

    return candidates[0][1]


def clean_title_for_pairing(title: str) -> str:
    """Normalizes subject and lab titles to establish pairing correspondence."""
    s = re.sub(r"[\s_\-\(\)\/]+", " ", str(title or "").lower()).strip()
    s = re.sub(r"\b(lab|laboratory|term\s*work|tw|practical|pr|or|tworl|tworpr)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def extract_subject_definitions(
    df: pd.DataFrame,
    catalog: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Dynamically extracts and consolidates valid subject definitions.
    Automatically combines corresponding theory subjects and their labs
    (e.g., 'Reinforcement Learning' and 'Reinforcement Learning Lab').
    Prunes empty ghost columns and unparented duplicates.
    """
    subjects_dict: Dict[str, Dict[str, Any]] = {}
    catalog_courses = (catalog or {}).get("courses", [])

    cat_by_code: Dict[str, Dict[str, Any]] = {}
    for c in catalog_courses:
        c_raw = str(c.get("code") or c.get("course_code") or "").strip()
        c_norm = normalize_token(c_raw)
        if c_norm:
            cat_by_code[c_norm] = c
            cat_by_code[c_raw.upper()] = c

    cols = list(df.columns)

    def clean_subject_code(raw_code: str) -> str:
        """Cleans course code and strips accidental trailing heads like TW, TH, PR, OR."""
        norm = normalize_token(raw_code)
        for head_tag in ("TW", "TH", "PR", "OR", "IA", "TOT"):
            if norm.endswith(head_tag) and len(norm) > len(head_tag) + 2:
                prefix_cand = norm[:-len(head_tag)]
                if any(ch.isalpha() for ch in prefix_cand) and any(ch.isdigit() for ch in prefix_cand):
                    return prefix_cand
        return norm

    # 1. PRIMARY: Discover subjects from *_Subject, * Subject, *_TITLE columns
    subject_cols = []
    for c in cols:
        c_lower = str(c).strip().lower()
        if "_subject" in c_lower or " subject" in c_lower or "-subject" in c_lower or "_title" in c_lower:
            subject_cols.append(c)

    for sc in subject_cols:
        sc_str = str(sc).strip()
        m = re.search(r"(_|\s|-)?(subject|title)\b", sc_str, re.IGNORECASE)
        raw_code = sc_str[:m.start()].strip() if m else sc_str
        clean_code = clean_subject_code(raw_code)

        if not clean_code or clean_code in NON_COURSE_CODES:
            continue

        valid_vals = df[sc].dropna().astype(str).str.strip()
        valid_vals = [v for v in valid_vals if v and v.lower() not in ("--", "-", "none", "nan", "null", "empty")]
        
        extracted_name = ""
        if valid_vals:
            extracted_name = max(set(valid_vals), key=valid_vals.count)

        cat_info = cat_by_code.get(clean_code, {})
        official_name = cat_info.get("name") or cat_info.get("official_course_name") or ""

        name = official_name or extracted_name or clean_code
        max_m = dict(cat_info.get("max_marks", {}))
        min_m = dict(cat_info.get("min_marks", {}))

        if clean_code in subjects_dict:
            curr = subjects_dict[clean_code]
            if (curr["name"] == clean_code or not curr["name"]) and (name and name != clean_code):
                curr["name"] = name
                curr["display_name"] = f"{clean_code} - {name}"
            for k, v in max_m.items():
                if k not in curr["max_marks"]:
                    curr["max_marks"][k] = v
            for k, v in min_m.items():
                if k not in curr["min_marks"]:
                    curr["min_marks"][k] = v
            continue

        subjects_dict[clean_code] = {
            "code": clean_code,
            "raw_code": raw_code,
            "name": name,
            "display_name": f"{clean_code} - {name}" if name and name.upper() != clean_code else clean_code,
            "heads": set(),
            "columns": {},
            "max_marks": max_m,
            "min_marks": min_m,
            "credits": float(cat_info.get("credits") or 0.0)
        }

    # 2. Inspect all columns to discover course codes and associate marks heads
    for col in cols:
        col_str = str(col).strip()
        parts = re.split(r"[\s_\-\(\)\/]+", col_str.upper())
        parts = [p for p in parts if p]
        if not parts:
            continue

        raw_prefix = parts[0]
        clean_prefix = clean_subject_code(raw_prefix)

        if not clean_prefix or clean_prefix in NON_COURSE_CODES:
            continue

        head = parts[-1] if len(parts) > 1 else ""
        composite_head = "_".join(parts[1:]) if len(parts) > 1 else ""

        is_known_head = False
        for head_category, tokens in HEAD_INDICATORS.items():
            if head in tokens or composite_head in tokens:
                is_known_head = True
                break

        if clean_prefix not in subjects_dict and is_known_head:
            cat_info = cat_by_code.get(clean_prefix, {})
            name = cat_info.get("name") or cat_info.get("official_course_name") or clean_prefix
            max_m = dict(cat_info.get("max_marks", {}))
            min_m = dict(cat_info.get("min_marks", {}))

            subjects_dict[clean_prefix] = {
                "code": clean_prefix,
                "raw_code": raw_prefix,
                "name": name,
                "display_name": f"{clean_prefix} - {name}" if name and name.upper() != clean_prefix else clean_prefix,
                "heads": set(),
                "columns": {},
                "max_marks": max_m,
                "min_marks": min_m,
                "credits": float(cat_info.get("credits") or 0.0)
            }

        if clean_prefix in subjects_dict and head:
            subjects_dict[clean_prefix]["heads"].add(head)
            if composite_head:
                subjects_dict[clean_prefix]["heads"].add(composite_head)
            subjects_dict[clean_prefix]["columns"][head] = col_str

    # 3. Add any courses in catalog that might not have triggered above
    for code_k, c in cat_by_code.items():
        clean_k = clean_subject_code(code_k)
        if clean_k not in subjects_dict and clean_k not in NON_COURSE_CODES:
            name = c.get("name") or c.get("official_course_name") or clean_k
            subjects_dict[clean_k] = {
                "code": clean_k,
                "raw_code": code_k,
                "name": name,
                "display_name": f"{clean_k} - {name}" if name and name != clean_k else clean_k,
                "heads": set(c.get("heads", [])),
                "columns": {},
                "max_marks": dict(c.get("max_marks", {})),
                "min_marks": dict(c.get("min_marks", {})),
                "credits": float(c.get("credits") or 0.0)
            }
        elif clean_k in subjects_dict:
            for k, v in c.get("max_marks", {}).items():
                if k not in subjects_dict[clean_k]["max_marks"]:
                    subjects_dict[clean_k]["max_marks"][k] = v
            for k, v in c.get("min_marks", {}).items():
                if k not in subjects_dict[clean_k]["min_marks"]:
                    subjects_dict[clean_k]["min_marks"][k] = v

    # 4. Finalize raw subjects list and prune ghost empty entries
    raw_subjects = []
    cols_list = list(df.columns)

    for code, s in list(subjects_dict.items()):
        th_col = find_column_for_head(cols_list, code, "TH", df=df, subject_name=s["name"])
        tw_col = find_column_for_head(cols_list, code, "TW", df=df, subject_name=s["name"])
        pr_col = find_column_for_head(cols_list, code, "PR", df=df, subject_name=s["name"])
        or_col = find_column_for_head(cols_list, code, "OR", df=df, subject_name=s["name"])

        has_th = bool(th_col)
        has_tw = bool(tw_col)
        has_pr = bool(pr_col)
        has_or = bool(or_col)
        has_pr_or = has_pr or has_or

        total_valid_entries = 0
        for col_cand in [th_col, tw_col, pr_col, or_col]:
            if col_cand and col_cand in df.columns:
                for v in df[col_cand].dropna():
                    st, _, _ = clean_mark_entry(v)
                    if st == "VALID":
                        total_valid_entries += 1

        is_in_catalog = normalize_token(code) in cat_by_code
        if total_valid_entries == 0 and not is_in_catalog and s["name"] == code:
            continue

        s["heads"] = sorted(list(s["heads"]))
        s["has_theory"] = has_th
        s["has_term_work"] = has_tw
        s["has_practical"] = has_pr
        s["has_oral"] = has_or
        s["has_practical_oral"] = has_pr_or

        if has_th and "TH" not in s["max_marks"]:
            s["max_marks"]["TH"] = 80.0
        if has_th and "TH" not in s["min_marks"]:
            s["min_marks"]["TH"] = 32.0
        if has_tw and "TW" not in s["max_marks"]:
            s["max_marks"]["TW"] = 25.0
        if has_tw and "TW" not in s["min_marks"]:
            s["min_marks"]["TW"] = 10.0
        if has_pr and "PR" not in s["max_marks"]:
            s["max_marks"]["PR"] = 25.0
        if has_pr and "PR" not in s["min_marks"]:
            s["min_marks"]["PR"] = 10.0
        if has_or and "OR" not in s["max_marks"]:
            s["max_marks"]["OR"] = 25.0
        if has_or and "OR" not in s["min_marks"]:
            s["min_marks"]["OR"] = 10.0

        if "TOT" not in s["max_marks"]:
            tot_max = (
                (s["max_marks"].get("TH", 0) if has_th else 0)
                + s["max_marks"].get("IA", 20 if has_th else 0)
                + (s["max_marks"].get("TW", 0) if has_tw else 0)
                + (s["max_marks"].get("PR", 0) if has_pr else 0)
                + (s["max_marks"].get("OR", 0) if has_or else 0)
            )
            s["max_marks"]["TOT"] = tot_max if tot_max > 0 else 100.0

        raw_subjects.append(s)

    # 5. Automatically Pair Corresponding Labs to Theory Subjects
    # (e.g. 'Reinforcement Learning' and 'Reinforcement Learning Lab')
    theory_subjects = [s for s in raw_subjects if s["has_theory"]]
    lab_candidates = [s for s in raw_subjects if not s["has_theory"] and (s["has_term_work"] or s["has_practical"] or s["has_oral"])]

    paired_lab_codes = set()

    for th_s in theory_subjects:
        th_t_clean = clean_title_for_pairing(th_s["name"])
        matched_lab = None

        # Priority 1: Exact cleaned title match
        if th_t_clean:
            for cand in lab_candidates:
                cand_clean = clean_title_for_pairing(cand["name"])
                if cand_clean and (cand_clean == th_t_clean):
                    matched_lab = cand
                    break

        # Priority 2: Title containment
        if not matched_lab and th_t_clean:
            for cand in lab_candidates:
                cand_clean = clean_title_for_pairing(cand["name"])
                if cand_clean and (cand_clean in th_t_clean or th_t_clean in cand_clean):
                    matched_lab = cand
                    break

        # Priority 3: Numerical code match (e.g. CSDO8013 and AIDSCSDOL8013, CSC801 and CSL801)
        if not matched_lab:
            th_num = re.sub(r"[^0-9]", "", th_s["code"])
            if th_num and len(th_num) >= 3:
                for cand in lab_candidates:
                    cand_num = re.sub(r"[^0-9]", "", cand["code"])
                    if cand_num == th_num:
                        matched_lab = cand
                        break

        if matched_lab:
            paired_lab_codes.add(matched_lab["code"])
            th_s["lab_code"] = matched_lab["code"]
            th_s["lab_name"] = matched_lab["name"]
            th_s["is_combined"] = True
            th_s["display_name"] = f"{th_s['code']} - {th_s['name']} (incl. Lab)"
            th_s["has_term_work"] = th_s["has_term_work"] or matched_lab["has_term_work"]
            th_s["has_practical"] = th_s["has_practical"] or matched_lab["has_practical"]
            th_s["has_oral"] = th_s["has_oral"] or matched_lab["has_oral"]
            th_s["has_practical_oral"] = th_s["has_practical_oral"] or matched_lab["has_practical_oral"]

            for h, c_name in matched_lab.get("columns", {}).items():
                if h not in th_s["columns"]:
                    th_s["columns"][h] = c_name

            th_tot_max = float(th_s["max_marks"].get("TOT") or (th_s["max_marks"].get("TH", 80.0) + th_s["max_marks"].get("IA", 20.0)))
            lab_tot_max = float(matched_lab["max_marks"].get("TOT") or (matched_lab["max_marks"].get("TW", 25.0) + matched_lab["max_marks"].get("PR", 25.0)))
            th_s["max_marks"]["TOT"] = th_tot_max + lab_tot_max
            th_s["theory_max_marks"] = th_tot_max
            th_s["lab_max_marks"] = lab_tot_max

            for k in ("TW", "PR", "OR"):
                if k in matched_lab.get("max_marks", {}):
                    th_s["max_marks"][k] = matched_lab["max_marks"][k]
                if k in matched_lab.get("min_marks", {}):
                    th_s["min_marks"][k] = matched_lab["min_marks"][k]

    # Return unified list: theory subjects (with paired lab) + unparented standalone labs
    final_list = []
    for s in raw_subjects:
        if s["code"] not in paired_lab_codes:
            final_list.append(s)

    final_list.sort(key=lambda x: x["code"])
    return final_list


def _build_component_analysis(
    df_clean: pd.DataFrame,
    col_name: Optional[str],
    component_name: str,
    max_marks: float,
    min_marks: float,
    grade_col: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes deterministic statistical metrics for a single academic component
    (Theory, Term Work, Practical, or Oral) and calculates First Class & Distinction distribution.
    """
    analysis: Dict[str, Any] = {"available": False}
    if not col_name or col_name not in df_clean.columns:
        analysis["message"] = f"{component_name} data unavailable in selected dataset."
        return analysis

    valid_marks: List[float] = []
    passed_marks: List[float] = []
    appeared = 0
    passed = 0
    failed = 0
    absent = 0
    missing = 0
    invalid = 0

    for _, row in df_clean.iterrows():
        raw_val = row.get(col_name)
        status, val, fail_flag = clean_mark_entry(raw_val)
        g_val = str(row.get(grade_col) or "").strip().upper() if grade_col else ""

        if status == "ABSENT":
            absent += 1
        elif status == "MISSING":
            missing += 1
        elif status == "INVALID":
            invalid += 1
        elif status == "VALID":
            appeared += 1
            valid_marks.append(val)

            is_pass = (val >= min_marks) and not fail_flag and (g_val not in ("F", "FAIL"))
            if is_pass:
                passed += 1
                passed_marks.append(val)
            else:
                failed += 1

    if appeared == 0:
        analysis["message"] = f"{component_name} data unavailable or no valid student records found."
        return analysis

    if valid_marks:
        observed_max = max(valid_marks)
        if observed_max > max_marks:
            if observed_max <= 50.0:
                max_marks = 50.0
                min_marks = max(min_marks, 20.0)
            elif observed_max <= 75.0:
                max_marks = 75.0
                min_marks = max(min_marks, 30.0)
            elif observed_max <= 100.0:
                max_marks = 100.0
                min_marks = max(min_marks, 40.0)

    pass_rate = round((passed / max(1, appeared)) * 100.0, 2)
    fail_rate = round((failed / max(1, appeared)) * 100.0, 2)

    # Institutional Classifications
    class_counts = {
        "First Class with Distinction": 0,
        "First Class": 0,
        "Second Class": 0,
        "Pass Class": 0
    }
    for m in passed_marks:
        pct = (m / max(1.0, max_marks)) * 100.0
        cat = AcademicClassificationRules.classify_percentage(pct, pass_threshold_pct=(min_marks / max_marks) * 100.0)
        if cat in class_counts:
            class_counts[cat] += 1
        else:
            class_counts["Pass Class"] += 1

    distinction_cnt = class_counts["First Class with Distinction"]
    first_class_cnt = class_counts["First Class"]
    first_class_and_dist_cnt = distinction_cnt + first_class_cnt

    first_class_and_dist_pct = round((first_class_and_dist_cnt / max(1, appeared)) * 100.0, 2)
    distinction_pct = round((distinction_cnt / max(1, appeared)) * 100.0, 2)
    first_class_pct = round((first_class_cnt / max(1, appeared)) * 100.0, 2)

    tot_p = max(1, passed)
    class_table = [
        {"classification": "First Class with Distinction (Distinction)", "short": "Distinction", "students": distinction_cnt, "percentage": round((distinction_cnt / tot_p) * 100.0, 2)},
        {"classification": "First Class", "short": "First Class", "students": first_class_cnt, "percentage": round((first_class_cnt / tot_p) * 100.0, 2)},
        {"classification": "Second Class", "short": "Second Class", "students": class_counts["Second Class"], "percentage": round((class_counts["Second Class"] / tot_p) * 100.0, 2)},
        {"classification": "Pass Class", "short": "Pass Class", "students": class_counts["Pass Class"], "percentage": round((class_counts["Pass Class"] / tot_p) * 100.0, 2)},
    ]

    marks_dist = compute_marks_distribution(valid_marks, max_marks, appeared)

    # Pass/Fail table enriched with First Class & Distinction counts and distribution
    pass_fail_table = [
        {"status": "Passed", "students": passed, "percentage": pass_rate},
        {"status": "Failed", "students": failed, "percentage": fail_rate},
        {"status": "First Class & Distinction (≥60%)", "students": first_class_and_dist_cnt, "percentage": first_class_and_dist_pct},
        {"status": "First Class with Distinction (≥75%)", "students": distinction_cnt, "percentage": distinction_pct},
        {"status": "First Class (60–74%)", "students": first_class_cnt, "percentage": first_class_pct}
    ]

    analysis.update({
        "available": True,
        "component_name": component_name,
        "column_used": col_name,
        "max_marks": max_marks,
        "min_marks": min_marks,
        "appeared": appeared,
        "passed": passed,
        "failed": failed,
        "absent": absent,
        "missing": missing,
        "invalid": invalid,
        "pass_rate": pass_rate,
        "fail_rate": fail_rate,
        "first_class_and_dist_count": first_class_and_dist_cnt,
        "first_class_and_dist_rate": first_class_and_dist_pct,
        "first_class_and_dist_pct": first_class_and_dist_pct,
        "distinction_count": distinction_cnt,
        "distinction_rate": distinction_pct,
        "first_class_count": first_class_cnt,
        "first_class_rate": first_class_pct,
        "pass_fail_table": pass_fail_table,
        "pass_fail_chart": {
            "labels": ["Passed", "Failed", "First Class & Distinction"],
            "counts": [passed, failed, first_class_and_dist_cnt],
            "percentages": [pass_rate, fail_rate, first_class_and_dist_pct]
        },
        "classification": {
            "table": class_table,
            "chart": {
                "labels": [c["short"] for c in class_table],
                "counts": [c["students"] for c in class_table],
                "percentages": [c["percentage"] for c in class_table]
            }
        },
        "marks_distribution": marks_dist
    })
    return analysis


def analyze_subject_performance(
    records: List[Dict[str, Any]],
    catalog: Optional[Dict[str, Any]],
    subject_code: str,
    dataset_meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Computes complete deterministic academic statistics for a chosen subject.
    Combines Theory and corresponding Lab (e.g., Reinforcement Learning + Reinforcement Learning Lab),
    and counts toppers strictly based on the total combined result of the subject.
    """
    df = pd.DataFrame(records)
    if df.empty:
        return {
            "success": False,
            "error": "Dataset records are empty."
        }

    subjects = extract_subject_definitions(df, catalog)
    clean_target = normalize_token(subject_code)

    # 1. Match subject or resolve paired lab to its parent theory subject
    subject_info = next((s for s in subjects if normalize_token(s["code"]) == clean_target), None)
    if not subject_info:
        subject_info = next((s for s in subjects if normalize_token(s.get("lab_code", "")) == clean_target), None)

    if not subject_info:
        subject_info = {
            "code": subject_code,
            "name": subject_code,
            "display_name": subject_code,
            "max_marks": {"TH": 80.0, "TOT": 100.0, "TW": 25.0, "PR": 25.0, "OR": 25.0},
            "min_marks": {"TH": 32.0, "TOT": 40.0, "TW": 10.0, "PR": 10.0, "OR": 10.0},
            "has_theory": True,
            "has_term_work": False,
            "has_practical": False,
            "has_oral": False,
            "has_practical_oral": False,
        }

    cols = list(df.columns)

    # Candidate student identifiers
    seat_col = next((c for c in cols if normalize_token(c) in ("SEATNO", "SEAT_NO", "SEAT", "ROLLNO", "ROLL_NO")), None)
    name_col = next((c for c in cols if normalize_token(c) in ("STUDENTNAME", "STUDENT_NAME", "NAME", "NAMEOFSTUDENT")), None)
    prn_col = next((c for c in cols if normalize_token(c) in ("PRN", "PRNNO", "PRN_NO")), None)

    # ==========================================
    # DATA VALIDATION & CLEANSING AUDIT
    # ==========================================
    total_records = len(df)
    seen_ids = set()
    duplicate_indices = set()

    for idx, row in df.iterrows():
        raw_prn = row.get(prn_col) if prn_col else None
        raw_seat = row.get(seat_col) if seat_col else None
        
        prn_str = str(raw_prn).strip() if (raw_prn is not None and not pd.isna(raw_prn)) else ""
        if prn_str.lower() in ("nan", "none", "null", ""):
            prn_str = ""

        seat_str = str(raw_seat).strip() if (raw_seat is not None and not pd.isna(raw_seat)) else ""
        if seat_str.lower() in ("nan", "none", "null", ""):
            seat_str = ""

        cand_id = prn_str or seat_str
        if cand_id and cand_id in seen_ids:
            duplicate_indices.add(idx)
        elif cand_id:
            seen_ids.add(cand_id)

    if duplicate_indices:
        df_clean = df.drop(index=list(duplicate_indices)).reset_index(drop=True)
    else:
        df_clean = df.copy()

    theory_code = subject_info["code"]
    lab_code = subject_info.get("lab_code")
    theory_name = subject_info.get("name") or theory_code
    lab_name = subject_info.get("lab_name") or (f"{theory_name} Lab" if lab_code else "")

    # Locate Theory columns
    th_col = find_column_for_head(cols, theory_code, "TH", df=df_clean, subject_name=theory_name)
    ia_col = find_column_for_head(cols, theory_code, "IA", df=df_clean, subject_name=theory_name)
    th_tot_col = find_column_for_head(cols, theory_code, "TOT", df=df_clean, subject_name=theory_name)
    grade_col = next((c for c in cols if normalize_token(c) == f"{normalize_token(theory_code)}GRADE"), None)

    # Locate Lab columns (checks lab_code first, then theory_code if standalone)
    tw_col = (
        (find_column_for_head(cols, lab_code, "TW", df=df_clean, subject_name=lab_name) if lab_code else None)
        or find_column_for_head(cols, theory_code, "TW", df=df_clean, subject_name=theory_name)
    )
    pr_col = (
        (find_column_for_head(cols, lab_code, "PR", df=df_clean, subject_name=lab_name) if lab_code else None)
        or find_column_for_head(cols, theory_code, "PR", df=df_clean, subject_name=theory_name)
    )
    lab_tot_col = (
        (find_column_for_head(cols, lab_code, "TOT", df=df_clean, subject_name=lab_name) if lab_code else None)
        or (find_column_for_head(cols, theory_code, "TOT", df=df_clean, subject_name=theory_name) if not th_col else None)
    )

    # Determine max/min marks
    th_max = float(subject_info.get("max_marks", {}).get("TH") or 80.0)
    th_min = float(subject_info.get("min_marks", {}).get("TH") or 32.0)
    tw_max = float(subject_info.get("max_marks", {}).get("TW") or 25.0)
    tw_min = float(subject_info.get("min_marks", {}).get("TW") or 10.0)
    pr_max = float(subject_info.get("max_marks", {}).get("PR") or 25.0)
    pr_min = float(subject_info.get("min_marks", {}).get("PR") or 10.0)

    # Combined Total Max Marks calculation
    th_tot_max = float(subject_info.get("theory_max_marks") or (th_max + 20.0 if th_col else 0.0))
    lab_tot_max = float(subject_info.get("lab_max_marks") or ((tw_max if tw_col else 0.0) + (pr_max if pr_col else 0.0)))
    combined_tot_max = float(subject_info.get("max_marks", {}).get("TOT") or (th_tot_max + lab_tot_max))
    if combined_tot_max <= 0:
        combined_tot_max = 100.0

    # ==========================================
    # 1. THEORY (TH) ANALYSIS
    # ==========================================
    theory_analysis = _build_component_analysis(
        df_clean=df_clean,
        col_name=th_col,
        component_name="Theory",
        max_marks=th_max,
        min_marks=th_min,
        grade_col=grade_col
    )
    if not theory_analysis.get("available"):
        theory_analysis["message"] = "Theory examination not applicable or unavailable for this course."

    # ==========================================
    # 2. TERM WORK (TW) ANALYSIS
    # ==========================================
    term_work_analysis = _build_component_analysis(
        df_clean=df_clean,
        col_name=tw_col,
        component_name="Term work",
        max_marks=tw_max,
        min_marks=tw_min
    )
    if not term_work_analysis.get("available"):
        term_work_analysis["message"] = "Term work data unavailable in selected dataset."

    # ==========================================
    # 3. PRACTICAL (PR) ANALYSIS
    # ==========================================
    practical_analysis = _build_component_analysis(
        df_clean=df_clean,
        col_name=pr_col,
        component_name="Practical",
        max_marks=pr_max,
        min_marks=pr_min
    )
    if not practical_analysis.get("available"):
        practical_analysis["message"] = "Practical data unavailable in selected dataset."

    # Backward compatibility
    practical_oral_analysis = dict(practical_analysis) if practical_analysis.get("available") else {
        "available": False,
        "message": "Practical/Oral data unavailable in selected dataset."
    }

    # ==========================================
    # 4. TOP 5 STUDENTS (COMBINED SUBJECT TOTAL)
    # ==========================================
    # Toppers are counted strictly based on the total result of that subject
    # (Combined Theory Total + Lab Total, e.g. 100 + 50 = 150)
    ranked_candidates = []
    for _, row in df_clean.iterrows():
        raw_name = row.get(name_col) if name_col else None
        raw_seat = row.get(seat_col) if seat_col else None
        raw_prn = row.get(prn_col) if prn_col else None

        s_name = str(raw_name).strip() if (raw_name is not None and not pd.isna(raw_name)) else ""
        s_seat = str(raw_seat).strip() if (raw_seat is not None and not pd.isna(raw_seat)) else ""
        s_prn = str(raw_prn).strip() if (raw_prn is not None and not pd.isna(raw_prn)) else ""

        if s_seat.lower() in ("nan", "none", "null", ""):
            s_seat = ""
        if s_prn.lower() in ("nan", "none", "null", ""):
            s_prn = ""

        # Extract Theory Score
        th_val: Optional[float] = None
        if th_tot_col and th_tot_col in row and pd.notna(row.get(th_tot_col)):
            st, val, _ = clean_mark_entry(row.get(th_tot_col))
            if st == "VALID":
                th_val = val
        if th_val is None and (th_col or ia_col):
            comp = []
            for c in [th_col, ia_col]:
                if c and c in row and pd.notna(row.get(c)):
                    st, val, _ = clean_mark_entry(row.get(c))
                    if st == "VALID":
                        comp.append(val)
            if comp:
                th_val = sum(comp)

        # Extract Lab Score
        lab_val: Optional[float] = None
        if lab_tot_col and lab_tot_col in row and pd.notna(row.get(lab_tot_col)):
            st, val, _ = clean_mark_entry(row.get(lab_tot_col))
            if st == "VALID":
                lab_val = val
        if lab_val is None and (tw_col or pr_col):
            comp = []
            for c in [tw_col, pr_col]:
                if c and c in row and pd.notna(row.get(c)):
                    st, val, _ = clean_mark_entry(row.get(c))
                    if st == "VALID":
                        comp.append(val)
            if comp:
                lab_val = sum(comp)

        # Compute Subject Total
        if th_val is not None and lab_val is not None:
            subject_score = th_val + lab_val
            target_max = combined_tot_max
        elif th_val is not None:
            subject_score = th_val
            target_max = th_tot_max if th_tot_max > 0 else combined_tot_max
        elif lab_val is not None:
            subject_score = lab_val
            target_max = lab_tot_max if lab_tot_max > 0 else combined_tot_max
        else:
            subject_score = None
            target_max = combined_tot_max

        if subject_score is not None:
            pct = round((subject_score / max(1.0, target_max)) * 100.0, 2)
            ranked_candidates.append({
                "student_name": s_name or f"Candidate {s_seat or s_prn or 'ID'}",
                "seat_no": s_seat,
                "prn": s_prn,
                "seat_or_prn": s_seat or s_prn or "N/A",
                "marks": subject_score,
                "max_marks": target_max,
                "percentage": pct,
                "theory_score": th_val,
                "lab_score": lab_val
            })

    ranked_candidates.sort(key=lambda x: x["marks"], reverse=True)

    top_5 = []
    current_rank = 1
    for i, c in enumerate(ranked_candidates[:5]):
        if i > 0 and c["marks"] < ranked_candidates[i - 1]["marks"]:
            current_rank = i + 1
        top_5.append({
            "rank": current_rank,
            **c
        })

    # Prepare Header Metadata
    d_meta = dataset_meta or {}
    branch_val = d_meta.get("branch") or d_meta.get("department") or "Engineering"
    acad_year = d_meta.get("academic_year") or d_meta.get("batch") or "Current Cohort"
    sem_val = d_meta.get("semester") or (catalog or {}).get("semester") or "N/A"
    dataset_name = d_meta.get("dataset_name") or d_meta.get("folder_name") or "Selected Gazette"

    header_meta = {
        "subject_name": subject_info.get("name") or subject_code,
        "subject_code": f"{theory_code} / {lab_code}" if lab_code else theory_code,
        "theory_code": theory_code,
        "lab_code": lab_code,
        "is_combined": bool(lab_code),
        "academic_year": acad_year,
        "department": (d_meta.get("department") or branch_val).replace("_", " "),
        "batch": d_meta.get("batch") or acad_year,
        "branch": branch_val.replace("_", " "),
        "semester": str(sem_val).replace("Semester_", "Sem ").upper(),
        "dataset_name": dataset_name,
        "credits": subject_info.get("credits", 0.0),
        "total_max_marks": combined_tot_max,
        "theory_max_marks": th_tot_max,
        "lab_max_marks": lab_tot_max,
        "columns_mapped": {
            "theory": th_col,
            "internal_assessment": ia_col,
            "theory_total": th_tot_col,
            "term_work": tw_col,
            "practical": pr_col,
            "lab_total": lab_tot_col
        }
    }

    validation_meta = {
        "total_records": total_records,
        "valid_records": len(df_clean),
        "duplicate_records": len(duplicate_indices),
        "theory_appeared": theory_analysis.get("appeared", 0),
        "theory_absent": theory_analysis.get("absent", 0),
        "theory_missing": theory_analysis.get("missing", 0),
        "theory_invalid": theory_analysis.get("invalid", 0)
    }

    return {
        "success": True,
        "header": header_meta,
        "validation": validation_meta,
        "theory": theory_analysis,
        "term_work": term_work_analysis,
        "practical": practical_analysis,
        "practical_oral": practical_oral_analysis,
        "top_students": top_5
    }
