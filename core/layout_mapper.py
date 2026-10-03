"""
Page Layout Mapper Module
=========================
Extracts physical 2D spatial geometries and column coordinate bounds from PDF ledger pages.
Provides explicit layout mapping context to AI extraction prompts to prevent column shifting
and anchors each numerical token strictly to its physical horizontal column slot.
"""

from dataclasses import asdict, dataclass, field
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF

try:
    from ..models import Course, CourseCatalog
    from .normalizer import normalize_head
except Exception:
    try:
        from data_extractor_AI.models import Course, CourseCatalog
        from data_extractor_AI.core.normalizer import normalize_head
    except Exception:
        from models import Course, CourseCatalog
        from core.normalizer import normalize_head

logger = logging.getLogger(__name__)


@dataclass
class ColumnSlot:
    """Represents a physical horizontal column slot on an academic ledger page."""
    slot_num: int
    name: str
    code: str
    x0: float
    x1: float
    heads: List[str] = field(default_factory=list)
    max_marks: Dict[str, int] = field(default_factory=dict)
    credits: float = 0.0
    is_identity: bool = False
    is_summary: bool = False

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    def contains_x(self, x: float, margin: float = 4.0) -> bool:
        return (self.x0 - margin) <= x <= (self.x1 + margin)


@dataclass
class PageLayoutMap:
    """Encapsulates physical page geometry and column layout mapping."""
    page_num: int
    page_width: float
    page_height: float
    orientation: str
    identity_slot: Optional[ColumnSlot] = None
    course_slots: List[ColumnSlot] = field(default_factory=list)
    summary_slot: Optional[ColumnSlot] = None
    archetype: str = "generic"

    @property
    def total_columns(self) -> int:
        count = len(self.course_slots)
        if self.identity_slot:
            count += 1
        if self.summary_slot:
            count += 1
        return count

    def find_slot_by_x(self, x: float) -> Optional[ColumnSlot]:
        """Finds matching column slot for an X coordinate in points."""
        if self.identity_slot and self.identity_slot.contains_x(x):
            return self.identity_slot
        for cs in self.course_slots:
            if cs.contains_x(x):
                return cs
        if self.summary_slot and self.summary_slot.contains_x(x):
            return self.summary_slot
        # Nearest slot fallback if slightly outside margins
        all_slots = []
        if self.identity_slot:
            all_slots.append(self.identity_slot)
        all_slots.extend(self.course_slots)
        if self.summary_slot:
            all_slots.append(self.summary_slot)
        if not all_slots:
            return None
        return min(all_slots, key=lambda s: abs(s.center_x - x))

    def to_prompt_context(self) -> str:
        """
        Renders an explicit, token-efficient Physical Layout Grid for AI prompts.
        Constrains the model's spatial reasoning and eliminates column shifting.
        """
        lines = [
            "=== PHYSICAL PAGE LAYOUT & COLUMN GRID MAPPING ===",
            f"Page Geometry: {self.page_width:.1f}pt wide x {self.page_height:.1f}pt high ({self.orientation} Orientation)",
            "PHYSICAL HORIZONTAL COLUMN SLOTS (Strict Left-to-Right Physical Order):"
        ]

        if self.identity_slot:
            lines.append(
                f"- [IDENTITY BLOCK] (X: {self.identity_slot.x0:.1f}pt - {self.identity_slot.x1:.1f}pt): "
                f"Candidate Seat Number, Full Name, 16-Digit PRN, Candidate Type (Regular/Diploma)"
            )

        for s in self.course_slots:
            heads_str = ", ".join(s.heads) if s.heads else "TH, IA, TOT"
            cred_str = f" | {s.credits:g} Credits" if s.credits > 0 else ""
            lines.append(
                f"- [SLOT {s.slot_num}] (X: {s.x0:.1f}pt - {s.x1:.1f}pt): "
                f"Course {s.code} ({s.name}) -> Component Heads: [{heads_str}]{cred_str}"
            )

        if self.summary_slot:
            lines.append(
                f"- [SUMMARY BLOCK] (X: {self.summary_slot.x0:.1f}pt - {self.summary_slot.x1:.1f}pt): "
                f"Total Marks, Result Status (P/F), Total Credits Earned (EC), Total CG (ECG), SGPI (GPA)"
            )

        lines.extend([
            "",
            "CRITICAL ZERO-SHIFT SPATIAL ANCHORING DIRECTIVE:",
            "1. Every mark and grade printed between X_min and X_max belongs EXCLUSIVELY to that slot.",
            "2. Never shift, transpose, or swap numbers into adjacent columns.",
            "3. If an optional course or practical is absent or omitted (marked '--' or 'ABS'), "
            "   you MUST output that slot explicitly with null/empty values. Never slide subsequent columns to the left!",
            "=================================================="
        ])
        return "\n".join(lines)


def _detect_course_tokens_on_page(page: fitz.Page) -> List[Dict[str, Any]]:
    """Scans PyMuPDF page blocks for course code tokens and header coordinates."""
    course_tokens = []
    # Match patterns like: CSC301, CSDLO501X, 01.CSC801, CSL501, Course- 1, etc.
    code_pattern = re.compile(
        r"\b(?:[0-9]{2}\.)?([A-Z]{2,5}\s*[0-9]{3,4}[A-Z0-9\-]*)\b|"
        r"\b(Course\s*[-–:]*\s*\d+)\b",
        re.IGNORECASE
    )

    rect = getattr(page, "rect", None)
    header_max_y = float(rect.height) * 0.38 if rect and hasattr(rect, "height") else 300.0

    try:
        words = page.get_text("words")  # (x0, y0, x1, y1, word, block_no, line_no, word_no)
    except Exception:
        words = []

    if isinstance(words, str) or not isinstance(words, (list, tuple)):
        return course_tokens

    for w in words:
        if not isinstance(w, (list, tuple)) or len(w) < 5:
            continue
        try:
            x0, y0, x1, y1, text = float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4])
        except (ValueError, TypeError, IndexError):
            continue

        if y0 <= header_max_y:
            m = code_pattern.search(text)
            if m:
                code_str = re.sub(r"\s+", "", (m.group(1) or m.group(2) or text)).upper()
                course_tokens.append({
                    "code": code_str,
                    "raw": text,
                    "x0": x0,
                    "x1": x1,
                    "y0": y0,
                    "y1": y1
                })

    return course_tokens


def extract_page_layout_map(
    pdf_path: str,
    page_index: int = 0,
    catalog: Optional[CourseCatalog] = None,
    archetype: str = "generic"
) -> PageLayoutMap:
    """
    Extracts physical page geometry and column layout coordinates.
    Computes exact horizontal column boundaries across course slots,
    identity block, and summary block.
    """
    try:
        doc = fitz.open(pdf_path)
        if page_index >= len(doc):
            try:
                doc.close()
            except Exception:
                pass
            return PageLayoutMap(
                page_num=page_index,
                page_width=1190.5,
                page_height=841.9,
                orientation="Landscape",
                archetype=archetype
            )

        page = doc[page_index]
        rect = getattr(page, "rect", None)
        width = float(rect.width) if rect and hasattr(rect, "width") else 1190.5
        height = float(rect.height) if rect and hasattr(rect, "height") else 841.9
        orientation = "Landscape" if width > height else "Portrait"
    except Exception:
        return PageLayoutMap(
            page_num=page_index,
            page_width=1190.5,
            page_height=841.9,
            orientation="Landscape",
            archetype=archetype
        )

    # Known catalog courses if provided
    courses = catalog.courses if catalog and catalog.courses else []

    # 1. Inspect words for column anchors
    course_tokens = _detect_course_tokens_on_page(page)

    course_slots: List[ColumnSlot] = []
    identity_slot: Optional[ColumnSlot] = None
    summary_slot: Optional[ColumnSlot] = None

    if courses:
        # Determine candidate identity and summary boundaries based on document dimensions
        if orientation == "Landscape":
            # Tabulation or wide ledger landscape layout
            id_x0 = 0.0
            id_x1 = max(180.0, width * 0.18)
            sum_x0 = min(width - 120.0, width * 0.88)
            sum_x1 = width
            avail_w = sum_x0 - id_x1
            slot_w = avail_w / max(1, len(courses))

            identity_slot = ColumnSlot(
                slot_num=0,
                name="CANDIDATE_IDENTITY",
                code="ID",
                x0=id_x0,
                x1=id_x1,
                is_identity=True
            )

            summary_slot = ColumnSlot(
                slot_num=len(courses) + 1,
                name="SUMMARY_METRICS",
                code="SUMMARY",
                x0=sum_x0,
                x1=sum_x1,
                is_summary=True
            )

            # Assign horizontal intervals for each catalog course
            curr_x = id_x1
            for idx, c in enumerate(courses, 1):
                c_x0 = curr_x
                c_x1 = curr_x + slot_w
                # Check if we have exact detected token position for this course code
                match_tok = next((t for t in course_tokens if c.code.upper() in t["code"] or t["code"] in c.code.upper()), None)
                if match_tok:
                    c_x0 = max(id_x1, match_tok["x0"] - 15.0)
                    c_x1 = min(sum_x0, match_tok["x1"] + (slot_w * 0.6))

                course_slots.append(ColumnSlot(
                    slot_num=idx,
                    name=c.name or c.code,
                    code=c.code,
                    x0=c_x0,
                    x1=c_x1,
                    heads=c.heads or ["TH", "IA", "TOT"],
                    credits=getattr(c, "credits", 0.0)
                ))
                curr_x += slot_w

        else:
            # Portrait ledger layout
            id_x0 = 0.0
            id_x1 = max(120.0, width * 0.22)
            sum_x0 = min(width - 80.0, width * 0.85)
            sum_x1 = width
            avail_w = sum_x0 - id_x1
            slot_w = avail_w / max(1, len(courses))

            identity_slot = ColumnSlot(slot_num=0, name="CANDIDATE_IDENTITY", code="ID", x0=id_x0, x1=id_x1, is_identity=True)
            summary_slot = ColumnSlot(slot_num=len(courses) + 1, name="SUMMARY_METRICS", code="SUMMARY", x0=sum_x0, x1=sum_x1, is_summary=True)

            curr_x = id_x1
            for idx, c in enumerate(courses, 1):
                course_slots.append(ColumnSlot(
                    slot_num=idx,
                    name=c.name or c.code,
                    code=c.code,
                    x0=curr_x,
                    x1=curr_x + slot_w,
                    heads=c.heads or ["TH", "IA", "TOT"],
                    credits=getattr(c, "credits", 0.0)
                ))
                curr_x += slot_w

    else:
        # No catalog yet: infer from physical page tokens
        if course_tokens:
            # Cluster tokens by X coordinates
            sorted_tokens = sorted(course_tokens, key=lambda t: t["x0"])
            id_x1 = sorted_tokens[0]["x0"] - 10.0 if sorted_tokens else width * 0.2
            identity_slot = ColumnSlot(slot_num=0, name="CANDIDATE_IDENTITY", code="ID", x0=0.0, x1=max(80.0, id_x1), is_identity=True)

            last_x = id_x1
            for idx, t in enumerate(sorted_tokens, 1):
                c_x0 = max(0.0, t["x0"] - 10.0)
                c_x1 = t["x1"] + 50.0
                course_slots.append(ColumnSlot(
                    slot_num=idx,
                    name=t["raw"],
                    code=t["code"],
                    x0=c_x0,
                    x1=c_x1,
                    heads=["TH", "IA", "TOT"]
                ))
                last_x = c_x1

            sum_x0 = max(last_x, width * 0.85)
            summary_slot = ColumnSlot(slot_num=len(course_slots) + 1, name="SUMMARY_METRICS", code="SUMMARY", x0=sum_x0, x1=width, is_summary=True)
        else:
            # Default proportional partition
            id_x1 = width * 0.22
            sum_x0 = width * 0.85
            identity_slot = ColumnSlot(slot_num=0, name="CANDIDATE_IDENTITY", code="ID", x0=0.0, x1=id_x1, is_identity=True)
            summary_slot = ColumnSlot(slot_num=2, name="SUMMARY_METRICS", code="SUMMARY", x0=sum_x0, x1=width, is_summary=True)
            course_slots.append(ColumnSlot(slot_num=1, name="COURSES", code="COURSES", x0=id_x1, x1=sum_x0, heads=["TH", "IA", "TOT"]))

    try:
        doc.close()
    except Exception:
        pass

    return PageLayoutMap(
        page_num=page_index,
        page_width=width,
        page_height=height,
        orientation=orientation,
        identity_slot=identity_slot,
        course_slots=course_slots,
        summary_slot=summary_slot,
        archetype=archetype
    )


def format_page_text_with_layout(pdf_path: str, page_index: int, layout_map: PageLayoutMap) -> str:
    """
    Extracts text tokens from the page and groups them with explicit column slot anchors.
    Ensures the text passed to AI is physically anchored, eliminating column confusion.
    """
    try:
        doc = fitz.open(pdf_path)
        if page_index >= len(doc):
            doc.close()
            return ""

        page = doc[page_index]
        try:
            words = page.get_text("words")  # (x0, y0, x1, y1, word)
        except Exception:
            words = []
        doc.close()

        if isinstance(words, str) or not isinstance(words, (list, tuple)) or not words:
            return ""

        valid_words = []
        for w in words:
            if isinstance(w, (list, tuple)) and len(w) >= 5:
                try:
                    valid_words.append((float(w[0]), float(w[1]), str(w[4])))
                except (ValueError, TypeError, IndexError):
                    pass

        if not valid_words:
            return ""

        # Group words by approximate vertical line (y0 tolerance 6pt)
        lines: List[List[Tuple[float, float, str]]] = []
        curr_line: List[Tuple[float, float, str]] = []
        curr_y = None

        for x0, y0, text in sorted(valid_words, key=lambda item: (item[1], item[0])):
            if curr_y is None:
                curr_y = y0
                curr_line.append((x0, y0, text))
            elif abs(y0 - curr_y) <= 6.0:
                curr_line.append((x0, y0, text))
            else:
                curr_line.sort(key=lambda item: item[0])
                lines.append(curr_line)
                curr_line = [(x0, y0, text)]
                curr_y = y0

        if curr_line:
            curr_line.sort(key=lambda item: item[0])
            lines.append(curr_line)

        # Format lines with column annotations
        formatted_lines = []
        for line_words in lines:
            if not line_words:
                continue
            # Group tokens into slots
            slot_groups: Dict[int, List[str]] = {}
            for x, y, text in line_words:
                slot = layout_map.find_slot_by_x(x)
                slot_id = slot.slot_num if slot else 0
                if slot_id not in slot_groups:
                    slot_groups[slot_id] = []
                slot_groups[slot_id].append(text)

            line_parts = []
            for s_idx in sorted(slot_groups.keys()):
                tokens_str = " ".join(slot_groups[s_idx])
                if s_idx == 0:
                    line_parts.append(f"[CANDIDATE: {tokens_str}]")
                elif s_idx == layout_map.total_columns - 1 and layout_map.summary_slot:
                    line_parts.append(f"[SUMMARY: {tokens_str}]")
                else:
                    matched_course = next((cs for cs in layout_map.course_slots if cs.slot_num == s_idx), None)
                    tag = f"SLOT_{s_idx}" + (f":{matched_course.code}" if matched_course else "")
                    line_parts.append(f"[{tag}: {tokens_str}]")

            return "\n".join(formatted_lines)
    except Exception:
        return ""
