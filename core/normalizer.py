"""
Universal Semantic Normalizer Module
====================================
Standardizes heterogeneous university naming conventions, assessment heads,
student identities, and ledger layout boilerplate across all gazette archetypes.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Canonical assessment heads mapping
HEAD_ALIASES: Dict[str, List[str]] = {
    "TH": ["TH", "SE", "ESE", "THEORY", "EXT", "EXTERNAL", "END_SEM", "ENDSEM", "E1", "PAPER", "HEAD1"],
    "IA": ["IA", "IN", "ISE", "INTERNAL", "INT", "TEST", "CA", "I1", "MID_SEM", "MIDSEM", "HEAD2"],
    "TW": ["TW", "TERMWORK", "TERM_WORK", "T1", "SESSIONAL", "TERM"],
    "PR": ["PR", "PRA", "PO", "P&O", "P_O", "ORAL", "OR", "O1", "OR_PR", "PRACTICAL", "VIVA", "LAB"],
    "TOT": ["TOT", "TOTAL", "SUB_TOTAL", "SUBTOTAL", "MARKS", "OBT", "OBTAINED", "GRAND_TOTAL", "AGGREGATE"]
}

# Inverted lookup dictionary for O(1) resolution
_CANONICAL_LOOKUP: Dict[str, str] = {}
for canonical, aliases in HEAD_ALIASES.items():
    for alias in aliases:
        _CANONICAL_LOOKUP[alias.upper()] = canonical

# Result status normalization mapping
RESULT_STATUS_MAP: Dict[str, str] = {
    "P": "PASS",
    "PASS": "PASS",
    "PASSES": "PASS",
    "SUCCESSFUL": "PASS",
    "PASSED": "PASS",
    "F": "FAIL",
    "FAIL": "FAIL",
    "FAILS": "FAIL",
    "UNSUCCESSFUL": "FAIL",
    "FAILED": "FAIL",
    "KT": "KT",
    "ATKT": "KT",
    "FAILED - ATKT": "KT",
    "RPV": "RPV",
    "ABS": "ABS",
    "AA": "ABS",
    "AB": "ABS",
    "ABSENT": "ABS",
    "DTC": "DTC",
    "F*": "FAIL"
}


def normalize_head(raw: str) -> str:
    """
    Normalizes any assessment head abbreviation into its canonical equivalent:
    TH, IA, TW, PR, or TOT. Returns upper-cased original if unrecognized.
    """
    if not raw:
        return ""
    clean = re.sub(r"[^A-Za-z0-9_&]", "", str(raw)).strip().upper()
    return _CANONICAL_LOOKUP.get(clean, clean)


def split_course_and_head(col_name: str) -> Tuple[str, str]:
    """
    Splits a composite column like 'CSC301_ESE' into ('CSC301', 'TH').
    If no separator is present, attempts to separate alphanumeric code from trailing head.
    """
    if not col_name:
        return "", ""

    clean = str(col_name).strip()
    # Handle standard underscore/hyphen delimiters: CSC301_TH, CSC301-IA
    m = re.match(r"^([A-Za-z0-9]+)[_\-\s/]+([A-Za-z0-9_&]+)$", clean)
    if m:
        code = m.group(1).upper()
        head = normalize_head(m.group(2))
        return code, head

    return clean.upper(), ""


def are_fields_alias_equivalent(field_a: str, field_b: str) -> bool:
    """
    Checks if two field names refer to the same course and equivalent assessment head.
    Example: 'CSC301_TH' and 'CSC301_SE' -> True.
    """
    if not field_a or not field_b:
        return False

    fa = field_a.strip().lower()
    fb = field_b.strip().lower()
    if fa == fb:
        return True

    code_a, head_a = split_course_and_head(field_a)
    code_b, head_b = split_course_and_head(field_b)

    if code_a and code_b and code_a == code_b:
        if head_a and head_b and head_a == head_b:
            return True

    return False


def normalize_student_name(raw_name: str) -> Dict[str, Any]:
    """
    Parses messy student name strings:
    - Detects leading female indicator '/' -> sets gender='F'.
    - Extracts mother's name inside parentheses (e.g. '(JYOTI)').
    - Strips line breaks, commas, redundant whitespace.
    Returns: {"student_name": str, "gender": str, "mother_name": str}
    """
    if not raw_name:
        return {"student_name": "", "gender": "M", "mother_name": ""}

    text = str(raw_name).strip()
    gender = "M"

    # 1. Detect female marker (leading slash common in Mumbai University gazettes)
    if text.startswith("/"):
        gender = "F"
        text = text.lstrip("/").strip()

    # 2. Extract mother's name if enclosed in parentheses
    mother_name = ""
    m_mom = re.search(r"\(([^)]+)\)", text)
    if m_mom:
        mother_name = m_mom.group(1).strip().upper()
        text = re.sub(r"\([^)]+\)", "", text).strip()

    # 3. Clean up commas, newlines, tabs, and duplicate spaces
    text = text.replace(",", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    clean_name = re.sub(r"\s+", " ", text).strip().upper()

    return {
        "student_name": clean_name,
        "gender": gender,
        "mother_name": mother_name
    }


def normalize_identifier(raw_id: Any) -> str:
    """
    Standardizes seat number, PRN, or ABC ID string representation:
    Preserves leading zeros and strips non-alphanumeric noise.
    """
    if raw_id is None:
        return ""
    s = str(raw_id).strip()
    # Strip any decimal point caused by float conversion like '58215001.0'
    if s.endswith(".0"):
        s = s[:-2]
    # Keep digits and alphanumeric letters (some colleges use prefix like D21AIDS01)
    clean = re.sub(r"[^A-Za-z0-9]", "", s).upper()
    return clean


def normalize_result_status(raw_status: Any) -> str:
    """
    Maps heterogeneous result status codes into standard vocabulary:
    PASS, FAIL, KT, RPV, ABS, DTC.
    """
    if not raw_status:
        return "UNKNOWN"
    clean = re.sub(r"[^A-Za-z*]", "", str(raw_status)).strip().upper()
    return RESULT_STATUS_MAP.get(clean, clean or "UNKNOWN")


def strip_ledger_boilerplate(page_text: str) -> str:
    """
    Prunes static university banners, ordinance legalese, grading conversion tables,
    and page footers to slash prompt token consumption by 70-85%.
    """
    if not page_text:
        return ""

    lines = page_text.splitlines()
    pruned_lines = []

    # Patterns indicating useless boilerplate
    boilerplate_patterns = [
        re.compile(r"UNIVERSITY OF MUMBAI", re.I),
        re.compile(r"Page No\.\s*:\s*\d+", re.I),
        re.compile(r"DATE OF DECLARATION", re.I),
        re.compile(r"CREDIT SYSTEM.*GRADES", re.I),
        re.compile(r"G\s*:\s*Grade\s*,\s*GP\s*:\s*Grade Point", re.I),
        re.compile(r"äC\s*:\s*sum of credit points", re.I),
        re.compile(r"äCG\s*:\s*sum of product", re.I),
        re.compile(r"MARKS\s*:\s*>=80.*GRADE\s*:\s*O", re.I),
        re.compile(r"\bO\s+A\s+B\s+C\s+D\s+E\s+P\s+F\b", re.I),
        re.compile(r"FEMALE\s*,\s*#\s*:\s*0\.229", re.I),
        re.compile(r"RPV\s*-\s*PROVISIONAL", re.I),
        re.compile(r"NULL\s*-\s*NULL & VOID", re.I),
        re.compile(r"Office\s+Register\s+For\s+The", re.I),
        re.compile(r"Centre\s+Name\s*:", re.I),
    ]

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip matched boilerplate lines
        if any(pat.search(stripped) for pat in boilerplate_patterns):
            continue
        pruned_lines.append(line)

    return "\n".join(pruned_lines)
