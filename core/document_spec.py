"""
Document Execution Specification & Dynamic Relationship Compiler
=================================================================
Discovers the dynamic blueprint, candidate header structure, course slots,
grade scale, and inter-column algebraic relationships directly from document
Page 1 and sample candidate records using Cloud/Local AI.
"""

from dataclasses import asdict, dataclass, field
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AuditCallback = Optional[Callable[[Dict[str, Any]], None]]


@dataclass
class AlgebraicRelation:
    """Defines a mathematical relationship between columns."""
    target_field: str                          # e.g., "TOT", "CG", "total_marks", "total_cg", "sgpi"
    formula: str                               # e.g., "TH + IA", "credits * grade_point", "sum(TOT)"
    scope: str = "per_course"                  # "per_course" or "student_aggregate"
    tolerance: float = 0.0                     # Allowable numerical difference (e.g., 0.02 for SGPI)
    rounding: Optional[int] = None             # Decimal places for rounding (e.g., 2 for SGPI)
    condition: Optional[str] = None            # Optional condition (e.g., "result_status != 'FAILED'")
    source_components: List[str] = field(default_factory=list)  # e.g., ["TH", "IA"]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AlgebraicRelation":
        return cls(
            target_field=str(d.get("target_field", "")),
            formula=str(d.get("formula", "")),
            scope=str(d.get("scope", "per_course")),
            tolerance=float(d.get("tolerance", 0.0)),
            rounding=int(d["rounding"]) if d.get("rounding") is not None else None,
            condition=str(d["condition"]) if d.get("condition") else None,
            source_components=list(d.get("source_components", []))
        )


@dataclass
class CourseSlot:
    """Represents a physical course column slot discovered on the ledger."""
    slot: int
    code: str
    name: str = ""
    credits: float = 0.0
    course_type: str = "Theory"                 # "Theory", "Lab", "Project", "Audit"
    category: str = "THEORY"                    # "THEORY", "LAB", "PROJECT", "ELECTIVE", "AUDIT"
    components: List[str] = field(default_factory=lambda: ["TH", "IA", "TOT"])
    max_marks: Dict[str, float] = field(default_factory=dict)
    min_marks: Dict[str, float] = field(default_factory=dict)

    def is_project(self) -> bool:
        c_type = (self.course_type or "").lower()
        cat = (self.category or "").upper()
        name_l = (self.name or "").lower()
        return cat == "PROJECT" or "proj" in c_type or "major" in name_l or "mini" in name_l or "project" in name_l

    def is_lab(self) -> bool:
        if self.is_project():
            return False
        c_type = (self.course_type or "").lower()
        cat = (self.category or "").upper()
        return cat == "LAB" or "lab" in c_type or "pr" in c_type or any(h in ("TW", "PR", "OR_PR") for h in self.components)

    def is_theory(self) -> bool:
        c_type = (self.course_type or "").lower()
        cat = (self.category or "").upper()
        return (cat in ("THEORY", "ELECTIVE") or "th" in c_type) and not self.is_lab() and not self.is_project()

    def get_max_tot(self) -> float:
        if self.max_marks and "TOT" in self.max_marks:
            return float(self.max_marks["TOT"])
        if self.is_project():
            return 150.0 if self.credits >= 4.0 else 100.0
        return 50.0 if self.is_lab() else 100.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CourseSlot":
        c_type = str(d.get("course_type", "Theory"))
        cat = str(d.get("category", "")).upper()
        name_str = str(d.get("name", "")).strip()
        name_l = name_str.lower()
        if not cat:
            low_type = c_type.lower()
            if "proj" in low_type or "mini" in low_type or "major" in name_l or "project" in name_l:
                cat = "PROJECT"
                c_type = "Project"
            elif "lab" in low_type or "pr" in low_type or "tw" in low_type:
                cat = "LAB"
            elif "elec" in low_type or "opt" in low_type:
                cat = "ELECTIVE"
            else:
                cat = "THEORY"

        return cls(
            slot=int(d.get("slot", 1)),
            code=str(d.get("code", "")).strip().upper(),
            name=str(d.get("name", "")).strip(),
            credits=float(d.get("credits", 0.0) or 0.0),
            course_type=c_type,
            category=cat,
            components=[str(c).upper() for c in d.get("components", ["TH", "IA", "TOT"]) if c],
            max_marks={str(k): float(v) for k, v in d.get("max_marks", {}).items() if v},
            min_marks={str(k): float(v) for k, v in d.get("min_marks", {}).items() if v}
        )


@dataclass
class CandidateHeaderSpec:
    """Rules and patterns for identifying and extracting candidate metadata."""
    seat_pattern: str = r"^\s*([0-9]{6,10})\b"
    prn_pattern: str = r"\b([0-9]{16})\b"
    abc_id_pattern: str = r"\b([0-9]{12})\b"
    gender_rule: str = "leading_slash_indicates_female"  # "leading_slash_indicates_female", "explicit_column", "none"
    candidate_type_rule: str = "diploma_marker"         # "diploma_marker", "sem_history", "explicit_column"
    remark_codes: List[str] = field(default_factory=lambda: ["PASS", "FAIL", "RLE", "RRA", "RPV", "ADC", "RCC", "ATKT"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandidateHeaderSpec":
        return cls(
            seat_pattern=str(d.get("seat_pattern", r"^\s*([0-9]{6,10})\b")),
            prn_pattern=str(d.get("prn_pattern", r"\b([0-9]{16})\b")),
            abc_id_pattern=str(d.get("abc_id_pattern", r"\b([0-9]{12})\b")),
            gender_rule=str(d.get("gender_rule", "leading_slash_indicates_female")),
            candidate_type_rule=str(d.get("candidate_type_rule", "diploma_marker")),
            remark_codes=list(d.get("remark_codes", ["PASS", "FAIL", "RLE", "RRA", "RPV", "ADC", "RCC", "ATKT"]))
        )


@dataclass
class GradingContract:
    """Grading scale and classification standards for the examination."""
    scale_type: str = "10-point"                # "10-point", "4-point", "percentage"
    scale: Dict[str, float] = field(default_factory=lambda: {
        "O": 10.0, "A+": 9.0, "A": 8.0, "B+": 7.0, "B": 6.0, "C": 5.0, "D": 4.0, "P": 4.0, "F": 0.0
    })
    valid_grades: List[str] = field(default_factory=lambda: ["O", "A+", "A", "B+", "B", "C", "D", "P", "F"])
    pass_grades: List[str] = field(default_factory=lambda: ["O", "A+", "A", "B+", "B", "C", "D", "P"])
    fail_grades: List[str] = field(default_factory=lambda: ["F", "AB", "ABS"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GradingContract":
        raw_scale = d.get("scale", {})
        scale = {str(k).upper(): float(v) for k, v in raw_scale.items()} if raw_scale else {
            "O": 10.0, "A+": 9.0, "A": 8.0, "B+": 7.0, "B": 6.0, "C": 5.0, "D": 4.0, "P": 4.0, "F": 0.0
        }
        return cls(
            scale_type=str(d.get("scale_type", "10-point")),
            scale=scale,
            valid_grades=[str(g).upper() for g in d.get("valid_grades", list(scale.keys()))],
            pass_grades=[str(g).upper() for g in d.get("pass_grades", [k for k, v in scale.items() if v > 0])],
            fail_grades=[str(g).upper() for g in d.get("fail_grades", ["F", "AB", "ABS"])]
        )


@dataclass
class DocumentExecutionSpec:
    """Master Dynamic Execution Plan synthesized by AI for a specific document."""
    exam_title: str = "University Examination"
    semester: str = "Current"
    college_name: str = ""
    scheme: str = "CBCGS"
    candidate_header: CandidateHeaderSpec = field(default_factory=CandidateHeaderSpec)
    slots: List[CourseSlot] = field(default_factory=list)
    algebraic_relations: List[AlgebraicRelation] = field(default_factory=list)
    grading_contract: GradingContract = field(default_factory=GradingContract)
    exception_tokens: Dict[str, List[str]] = field(default_factory=lambda: {
        "absent": ["AB", "ABS", "--", "-"],
        "grace_markers": ["*", "#", "@", "!"],
        "null_indicators": ["--", "-", "null", "none"]
    })
    column_contract: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exam_title": self.exam_title,
            "semester": self.semester,
            "college_name": self.college_name,
            "scheme": self.scheme,
            "candidate_header": self.candidate_header.to_dict(),
            "slots": [s.to_dict() for s in self.slots],
            "algebraic_relations": [r.to_dict() for r in self.algebraic_relations],
            "grading_contract": self.grading_contract.to_dict(),
            "exception_tokens": self.exception_tokens,
            "column_contract": self.column_contract
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DocumentExecutionSpec":
        header_spec = CandidateHeaderSpec.from_dict(d.get("candidate_header", {}))
        slots = [CourseSlot.from_dict(s) for s in d.get("slots", []) if isinstance(s, dict)]
        relations = [AlgebraicRelation.from_dict(r) for r in d.get("algebraic_relations", []) if isinstance(r, dict)]
        grading = GradingContract.from_dict(d.get("grading_contract", {}))
        
        return cls(
            exam_title=str(d.get("exam_title", "University Examination")),
            semester=str(d.get("semester", "Current")),
            college_name=str(d.get("college_name", "")),
            scheme=str(d.get("scheme", "CBCGS")),
            candidate_header=header_spec,
            slots=slots,
            algebraic_relations=relations,
            grading_contract=grading,
            exception_tokens=d.get("exception_tokens", {
                "absent": ["AB", "ABS", "--", "-"],
                "grace_markers": ["*", "#", "@", "!"],
                "null_indicators": ["--", "-", "null", "none"]
            }),
            column_contract=list(d.get("column_contract", []))
        )

    def get_slot_by_code(self, code: str) -> Optional[CourseSlot]:
        clean = re.sub(r"^\d{1,2}\.\s*", "", str(code or "")).strip().upper()
        for s in self.slots:
            s_clean = re.sub(r"^\d{1,2}\.\s*", "", s.code).strip().upper()
            if s.code == code or s_clean == clean:
                return s
        return None

    def get_slot_by_number(self, num: int) -> Optional[CourseSlot]:
        for s in self.slots:
            if s.slot == num:
                return s
        return None


def get_default_relations() -> List[AlgebraicRelation]:
    """Default fallback algebraic relations common to semester credit systems."""
    return [
        AlgebraicRelation(
            target_field="TOT",
            formula="TH + IA",
            scope="per_course",
            source_components=["TH", "IA"]
        ),
        AlgebraicRelation(
            target_field="TOT",
            formula="TW + PR",
            scope="per_course",
            source_components=["TW", "PR"]
        ),
        AlgebraicRelation(
            target_field="credit_points",
            formula="credits * grade_point",
            scope="per_course",
            source_components=["credits", "grade_point"]
        ),
        AlgebraicRelation(
            target_field="total_marks",
            formula="sum(course_results.*.marks.TOT)",
            scope="student_aggregate"
        ),
        AlgebraicRelation(
            target_field="total_cg",
            formula="sum(course_results.*.credit_points)",
            scope="student_aggregate",
            tolerance=0.1
        ),
        AlgebraicRelation(
            target_field="credits_earned",
            formula="sum(course_results.*.credits where grade != 'F')",
            scope="student_aggregate"
        ),
        AlgebraicRelation(
            target_field="sgpi",
            formula="total_cg / credits_earned",
            scope="student_aggregate",
            tolerance=0.03,
            rounding=2,
            condition="result_status != 'FAILED'"
        )
    ]


def compile_document_spec(
    page1_text: str,
    page1_image: Optional[bytes] = None,
    client: Any = None,
    audit_cb: Optional[AuditCallback] = None
) -> DocumentExecutionSpec:
    """
    Phase 0: Prompt AI to discover the full Document Execution Specification,
    inter-column relationships, grading contract, and slot layout.
    """
    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "spec_compilation_start",
                "badge": "AI SPEC COMPILER",
                "title": "Deducing Dynamic Document Execution Spec & Algebraic Relations from Page 1...",
                "details": {"has_image": bool(page1_image), "text_chars": len(page1_text)}
            })
        except Exception:
            pass

    system_instruction = (
        "You are an academic database schema architect and mathematical auditor.\n"
        "Analyze this academic examination ledger page (Page 1) and determine the exact operational blueprint, "
        "including physical column slots, metadata patterns, grading contract, and algebraic relations between columns.\n"
        "DO NOT assume hardcoded university rules; deduce all rules directly from the document headers, legend, and sample candidate rows.\n"
        "Strictly output valid JSON matching the DocumentExecutionSpec format."
    )

    user_prompt = (
        "Analyze this examination ledger (Page 1 image/text) and establish the dynamic DocumentExecutionSpec.\n\n"
        "CRITICAL DISCOVERY INSTRUCTIONS:\n"
        "1. PHYSICAL COLUMN SLOTS & STRUCTURAL PROFILE:\n"
        "   - Map the horizontal subject table columns (Slot 1..N) strictly left to right.\n"
        "   - For each slot, determine: slot number, course code, name, credits, course_type ('Theory', 'Lab', 'Project', or 'Elective'), components (e.g. ['TH', 'IA', 'TOT'] or ['TW', 'PR', 'TOT']), and max_marks (e.g. {'TH': 80, 'IA': 20, 'TOT': 100} or {'TW': 25, 'PR': 25, 'TOT': 50}).\n"
        "   - CATEGORY BOUNDARIES: Clearly identify where Theory courses end and where Practical/Lab courses begin (e.g. Slots 1-5 Theory, Slots 6-10 Lab, Slot 11 Project) to prevent column shifting.\n"
        "2. CANDIDATE HEADER PATTERN:\n"
        "   - How are students identified? Determine seat number format, PRN/Registration digits, and how gender is designated (e.g. leading slash '/' before name for female, or explicit column).\n"
        "3. GRADING CONTRACT:\n"
        "   - Discover the allowed letter grades and their grade points (GP) from the legend or grade table (e.g. O=10, A+=9, etc.).\n"
        "4. ALGEBRAIC RELATIONS (HOW COLUMNS RELATE & CALCULATE):\n"
        "   - Define how component marks sum into total (e.g. TH + IA = TOT, or TW + PR = TOT).\n"
        "   - Define course credit point calculation (Credits * GP = CG).\n"
        "   - Define semester aggregates (Total Marks = sum(TOT), Total CG = sum(CG), SGPI = Total CG / Credits).\n\n"
        "Return pure valid JSON with this exact schema:\n"
        "{\n"
        '  "exam_title": "Full name of exam",\n'
        '  "semester": "Semester numeral (e.g. III, IV, VIII)",\n'
        '  "college_name": "College name or code",\n'
        '  "scheme": "CBCGS / NEP-2020",\n'
        '  "candidate_header": {\n'
        '    "seat_pattern": "^[0-9]{7,8}$",\n'
        '    "prn_pattern": "\\\\b[0-9]{16}\\\\b",\n'
        '    "abc_id_pattern": "\\\\b[0-9]{12}\\\\b",\n'
        '    "gender_rule": "leading_slash_indicates_female",\n'
        '    "remark_codes": ["PASS", "FAIL", "RLE", "RRA", "RPV", "ADC", "RCC", "ATKT"]\n'
        "  },\n"
        '  "slots": [\n'
        "    {\n"
        '      "slot": 1,\n'
        '      "code": "COURSE_CODE_1",\n'
        '      "name": "Course Title",\n'
        '      "credits": 3.0,\n'
        '      "course_type": "Theory",\n'
        '      "components": ["TH", "IA", "TOT"],\n'
        '      "max_marks": {"TH": 80, "IA": 20, "TOT": 100},\n'
        '      "min_marks": {"TH": 32, "IA": 8, "TOT": 40}\n'
        "    }\n"
        "  ],\n"
        '  "grading_contract": {\n'
        '    "scale_type": "10-point",\n'
        '    "scale": {"O": 10.0, "A": 9.0, "B": 8.0, "C": 7.0, "D": 6.0, "P": 4.0, "F": 0.0},\n'
        '    "valid_grades": ["O", "A", "B", "C", "D", "P", "F"]\n'
        "  },\n"
        '  "algebraic_relations": [\n'
        '    {"target_field": "TOT", "formula": "TH + IA", "scope": "per_course", "source_components": ["TH", "IA"]},\n'
        '    {"target_field": "credit_points", "formula": "credits * grade_point", "scope": "per_course", "source_components": ["credits", "grade_point"]},\n'
        '    {"target_field": "total_marks", "formula": "sum(course_results.*.marks.TOT)", "scope": "student_aggregate"},\n'
        '    {"target_field": "total_cg", "formula": "sum(course_results.*.credit_points)", "scope": "student_aggregate"},\n'
        '    {"target_field": "sgpi", "formula": "total_cg / credits_earned", "scope": "student_aggregate", "tolerance": 0.03, "rounding": 2}\n'
        "  ],\n"
        '  "column_contract": ["Seat_No", "Student_Name", "Gender", "Candidate_Type", "PRN", "Result_Status", "Total_Marks", "Credits_Earned", "Total_CG", "SGPI"]\n'
        "}\n\n"
        f"PAGE 1 RAW TEXT CONTEXT:\n{page1_text[:6000]}\n"
    )

    raw_json = ""
    if client and client.is_configured():
        try:
            t0 = time.time()
            raw_json = client.generate_direct(
                user_prompt=user_prompt,
                system_instruction=system_instruction,
                format_json=True,
                timeout=75,
                temperature=0.0,
                audit_cb=audit_cb,
                image_bytes=page1_image,
                image_mime="image/jpeg" if page1_image else None
            )
            dur = time.time() - t0
            logger.info("Compiled document spec in %.2fs", dur)
        except Exception as e:
            logger.warning("Failed to compile document spec via AI client: %s", e)

    spec_dict = {}
    if raw_json:
        try:
            spec_dict = json.loads(raw_json)
        except Exception as e:
            logger.warning("Failed to parse document spec JSON: %s. Raw: %s", e, raw_json[:200])

    spec = DocumentExecutionSpec.from_dict(spec_dict)

    # Ensure essential relations exist
    if not spec.algebraic_relations:
        spec.algebraic_relations = get_default_relations()

    # Build canonical column contract if AI omitted it
    if not spec.column_contract and spec.slots:
        cols = ["Seat_No", "Student_Name", "Gender", "Candidate_Type", "PRN", "ABC_ID", "Result_Status", "Remark"]
        for s in spec.slots:
            for comp in (s.components or ["TOT"]):
                cols.append(f"{s.code}_{comp.upper()}")
            cols.extend([f"{s.code}_Grade", f"{s.code}_GP", f"{s.code}_Credits", f"{s.code}_CG"])
        cols.extend(["Total_Marks", "Credits_Earned", "Total_CG", "SGPI"])
        spec.column_contract = cols

    if audit_cb:
        try:
            audit_cb({
                "timestamp": time.strftime("%H:%M:%S"),
                "type": "spec_compilation_success",
                "badge": "SPEC COMPILED",
                "title": f"Deduce Spec: {len(spec.slots)} slots, {len(spec.algebraic_relations)} algebraic relations, {len(spec.grading_contract.valid_grades)} grades locked",
                "details": {
                    "exam_title": spec.exam_title,
                    "semester": spec.semester,
                    "slots_count": len(spec.slots),
                    "relations": [f"{r.target_field} = {r.formula}" for r in spec.algebraic_relations],
                    "valid_grades": spec.grading_contract.valid_grades
                }
            })
        except Exception:
            pass

    return spec


@dataclass
class DynamicRulesRegistry:
    """
    Cumulative In-Flight Knowledge Base & Rule Accumulator.
    Establishes, locks, and feeds forward discovered rules across candidates and pages:
    - Physical slot mappings & all discovered elective course variants
    - Dynamic credit structures & component max marks (e.g. 150 for Major Project II)
    - Valid grading scales and Grade-to-GP points
    - Program total credits invariant
    - In-flight candidate markers (leading slash for female, diploma exempt markers)
    """
    exam_title: str = "University Examination"
    semester: str = "Current"
    college_name: str = ""
    scheme: str = "CBCGS"
    total_program_credits: float = 0.0
    valid_grades: List[str] = field(default_factory=list)
    grade_scale: Dict[str, float] = field(default_factory=dict)
    slot_electives: Dict[int, List[Dict[str, str]]] = field(default_factory=dict)
    slot_profiles: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    invariants: Dict[str, str] = field(default_factory=lambda: {
        "gender_marker": "Leading slash '/' before student name indicates Female; absence indicates Male.",
        "candidate_types": "Regular candidates have continuous SGPI history; Direct Second Year (Diploma) have Sem I/II marked '--' or 'DIPLOMA'.",
        "sgpi_formula": "SGPI = Total CG / Credits Earned (rounded to 2 decimal places)."
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exam_title": self.exam_title,
            "semester": self.semester,
            "college_name": self.college_name,
            "scheme": self.scheme,
            "total_program_credits": self.total_program_credits,
            "valid_grades": self.valid_grades,
            "grade_scale": self.grade_scale,
            "slot_electives": self.slot_electives,
            "slot_profiles": self.slot_profiles,
            "invariants": self.invariants
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DynamicRulesRegistry":
        return cls(
            exam_title=str(d.get("exam_title", "")),
            semester=str(d.get("semester", "")),
            college_name=str(d.get("college_name", "")),
            scheme=str(d.get("scheme", "CBCGS")),
            total_program_credits=float(d.get("total_program_credits", 0.0) or 0.0),
            valid_grades=list(d.get("valid_grades", [])),
            grade_scale={str(k): float(v) for k, v in d.get("grade_scale", {}).items()},
            slot_electives={int(k): list(v) for k, v in d.get("slot_electives", {}).items()},
            slot_profiles={int(k): dict(v) for k, v in d.get("slot_profiles", {}).items()},
            invariants=dict(d.get("invariants", {}))
        )

    @classmethod
    def from_blueprint_and_spec(
        cls,
        blueprint: Dict[str, Any],
        spec: Optional[DocumentExecutionSpec] = None,
        catalog: Optional[Any] = None
    ) -> "DynamicRulesRegistry":
        registry = cls(
            exam_title=blueprint.get("exam_title") or (spec.exam_title if spec else "University Examination"),
            semester=blueprint.get("semester") or (spec.semester if spec else "Current"),
            college_name=blueprint.get("college_name") or (spec.college_name if spec else ""),
            scheme=blueprint.get("grading_system") or (spec.scheme if spec else "CBCGS"),
            valid_grades=list(blueprint.get("valid_grades") or (spec.grading_contract.valid_grades if spec else []))
        )
        if spec and spec.grading_contract and spec.grading_contract.scale:
            registry.grade_scale = dict(spec.grading_contract.scale)

        slots_src = (spec.slots if spec and spec.slots else blueprint.get("slots", []))
        for idx, s in enumerate(slots_src):
            s_num = getattr(s, "slot", None) or (s.get("slot") if isinstance(s, dict) else None) or (idx + 1)
            code = getattr(s, "code", None) or (s.get("code", "") if isinstance(s, dict) else "")
            name = getattr(s, "name", None) or (s.get("name", "") if isinstance(s, dict) else "")
            credits_val = getattr(s, "credits", None) or (s.get("credits", 0.0) if isinstance(s, dict) else 0.0)
            c_type = getattr(s, "course_type", None) or (s.get("course_type") or s.get("type", "Theory") if isinstance(s, dict) else "Theory")
            slot_obj = CourseSlot.from_dict(s) if isinstance(s, dict) else s
            max_tot = slot_obj.get_max_tot() if hasattr(slot_obj, "get_max_tot") else 100.0
            heads = getattr(slot_obj, "components", ["TH", "IA", "TOT"])

            registry.slot_profiles[int(s_num)] = {
                "slot": int(s_num),
                "code": code,
                "name": name,
                "credits": float(credits_val or 0.0),
                "course_type": c_type,
                "heads": list(heads),
                "max_tot": float(max_tot)
            }
            if code:
                registry.slot_electives.setdefault(int(s_num), [])
                if not any(item.get("code") == code for item in registry.slot_electives[int(s_num)]):
                    registry.slot_electives[int(s_num)].append({"code": code, "name": name})

        return registry

    def record_student(self, student: Any) -> None:
        """Accumulate newly observed course codes, electives, and credit totals from a parsed student record."""
        cr_earned = getattr(student, "credits_earned", None)
        if cr_earned:
            try:
                val = float(str(cr_earned).strip())
                if val > self.total_program_credits:
                    self.total_program_credits = val
            except Exception:
                pass

        c_results = getattr(student, "course_results", [])
        for c_idx, cr in enumerate(c_results):
            code = getattr(cr, "course_code", "").strip().upper()
            name = getattr(cr, "course_name", "").strip()
            if not code:
                continue
            slot_matched = None
            for s_num, p in self.slot_profiles.items():
                if p.get("code") == code:
                    slot_matched = s_num
                    break
            if slot_matched is None and (c_idx + 1) in self.slot_profiles:
                slot_matched = c_idx + 1

            if slot_matched:
                self.slot_electives.setdefault(slot_matched, [])
                if not any(item.get("code") == code for item in self.slot_electives[slot_matched]):
                    self.slot_electives[slot_matched].append({"code": code, "name": name})

    def record_newly_discovered_rules(self, rules_dict: Dict[str, Any]) -> None:
        """Incorporate explicit new rules or electives returned by the AI decoder."""
        if not isinstance(rules_dict, dict):
            return
        new_elecs = rules_dict.get("new_electives", [])
        if isinstance(new_elecs, list):
            for ne in new_elecs:
                if isinstance(ne, dict):
                    s_num = int(ne.get("slot", 0) or 0)
                    code = str(ne.get("code", "")).strip().upper()
                    name = str(ne.get("name", "")).strip()
                    if s_num and code:
                        self.slot_electives.setdefault(s_num, [])
                        if not any(item.get("code") == code for item in self.slot_electives[s_num]):
                            self.slot_electives[s_num].append({"code": code, "name": name})

    def format_for_prompt(self) -> str:
        """Formats the cumulative established rules into a dense, token-lean context block (~120 tokens)."""
        lines = [
            "=== ESTABLISHED RULES & ACCUMULATED KNOWLEDGE BASE ===",
            f"• Exam: {self.exam_title} | Sem: {self.semester} | Program Credits: {int(self.total_program_credits) if self.total_program_credits.is_integer() else self.total_program_credits}",
            "• Locked Physical Column Slots (1..N):"
        ]
        for s_num in sorted(self.slot_profiles.keys()):
            p = self.slot_profiles[s_num]
            variants = self.slot_electives.get(s_num, [])
            c_codes = " / ".join(v["code"] for v in variants if v.get("code")) or p.get("code", "")
            heads_str = ",".join(p.get("heads", []))
            c_type = p.get("course_type", "Theory")
            cr = p.get("credits", "")
            max_t = int(p.get("max_tot", 100))
            lines.append(f"  Slot {s_num}: {c_codes} [{c_type}, {cr} Cr, {heads_str}, Max TOT: {max_t}]")

        if self.valid_grades:
            lines.append(f"• Verified Grades: {', '.join(self.valid_grades)}")

        lines.extend([
            "• Structural Invariants:",
            "  - Gender: Leading '/' before name marks Female candidate; absence marks Male.",
            "  - Candidate Type: Continuous SGPIs -> Regular; Sem I/II '--' or 'DIPLOMA' -> Diploma.",
            "  - SGPI: Total CG / Total Credits (verified inline).",
            "• In-Flight Instruction: Use these locked slot definitions to decode candidates directly.",
            "  If a candidate presents an elective course not yet listed, return it in 'newly_discovered_rules'.",
            "======================================================="
        ])
        return "\n".join(lines)
