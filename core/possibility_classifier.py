"""
Possibility Classifier
======================
Analyzes document pages against the pre-trained knowledge base to classify:
1. Document Archetype ('tabulation', 'gazette_classic', 'gazette_spool', 'gazette_nep')
2. Page Type ('header', 'data', 'boilerplate')
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..models import PageClassification, PageContent
except Exception:
    try:
        from data_extractor_AI.models import PageClassification, PageContent
    except Exception:
        from models import PageClassification, PageContent

KB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "knowledge_base"))
ARCHETYPES_FILE = os.path.join(KB_DIR, "archetypes.json")


def load_archetypes_kb() -> Dict[str, Any]:
    """Load archetype definitions and detection rules from knowledge base."""
    if os.path.isfile(ARCHETYPES_FILE):
        try:
            with open(ARCHETYPES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("archetypes", data)
        except Exception:
            pass
    return {}


def classify_document(pages: List[PageContent]) -> Tuple[str, float]:
    """
    Classify the overarching document archetype by scoring page 0 (and page 1 if needed).
    Returns (archetype_name, confidence).
    """
    if not pages:
        return "tabulation", 0.0

    p0_text = pages[0].text or ""
    p1_text = pages[1].text if len(pages) > 1 else ""
    sample_text = (p0_text + "\n" + p1_text[:1000]).upper()

    kb = load_archetypes_kb()
    if not kb:
        # Fallback heuristic if KB is missing
        if "TERNA" in sample_text or "TABULATION" in sample_text:
            return "tabulation", 0.9
        if "OFFICE REGISTER" in sample_text:
            return "gazette_classic", 0.9
        if "PAPER 1" in sample_text and "PAPER 7" in sample_text:
            return "gazette_spool", 0.9
        if "NEP 2020" in sample_text:
            return "gazette_nep", 0.9
        return "tabulation", 0.5

    best_arch = "tabulation"
    best_score = -1.0

    for arch_name, rules in kb.items():
        # Negative triggers: immediate disqualifier
        neg_triggers = rules.get("keywords_negative", [])
        if any(neg.upper() in sample_text for neg in neg_triggers):
            continue

        score = 0.0
        max_possible = 0.0

        # Primary keywords (weight 3.0)
        primary = rules.get("keywords_primary", [])
        for k in primary:
            max_possible += 3.0
            if k.upper() in sample_text:
                score += 3.0

        # Secondary keywords (weight 1.0)
        secondary = rules.get("keywords_secondary", [])
        for k in secondary:
            max_possible += 1.0
            if k.upper() in sample_text:
                score += 1.0

        # Seat number regex match bonus
        seat_pat = rules.get("seat_number_pattern", "")
        if seat_pat and re.search(seat_pat, sample_text):
            score += 2.0
            max_possible += 2.0

        conf = (score / max(max_possible, 1.0)) if max_possible > 0 else 0.0
        if conf > best_score:
            best_score = conf
            best_arch = arch_name

    return best_arch, max(round(best_score, 2), 0.5)


def classify_page(page_text: str, archetype: str) -> PageClassification:
    """
    Classify a single page's role ('header', 'data', or 'boilerplate').
    """
    text = (page_text or "").strip()
    classification = PageClassification(archetype=archetype, page_type="data", confidence=0.8)

    # 1. Check for pure boilerplate legend page
    boilerplate_markers = [
        "GRADE POINT", "MARKS :", "0 to 39.99",
        "Entered by :", "Read by :", "Checked by:",
        "/- FEMALE", "#- 0.229", "% Marks :"
    ]
    matches = [m for m in boilerplate_markers if m.upper() in text.upper()]
    # If text is small and mostly footnote legends
    if len(matches) >= 3 and len(text) < 600:
        classification.page_type = "boilerplate"
        classification.confidence = 0.95
        classification.matched_keywords = matches
        return classification

    # 2. Check for pure course legend/catalog header page (page 0 of NEP or classic gazette)
    seat_matches = re.findall(r"\b\d{7,10}\b", text)
    has_seat_records = len(seat_matches) >= 2

    if archetype == "gazette_nep":
        if "Course Code Course Title" in text and not has_seat_records:
            classification.page_type = "header"
            classification.confidence = 0.95
            return classification

    if archetype == "gazette_spool":
        if "OFFICE REGISTER OF THE B.E." in text and "42211-" in text and not has_seat_records:
            classification.page_type = "header"
            classification.confidence = 0.90
            return classification

    if not has_seat_records and ("EXAMINATION HELD IN" in text.upper() or "COLLEGE CODE" in text.upper()):
        classification.page_type = "header"
        classification.confidence = 0.85
        return classification

    classification.page_type = "data"
    classification.confidence = 0.90
    return classification
