"""
Data Models for AI-Powered Data Extractor
==========================================
Standalone models supporting multi-archetype academic result extraction,
dual-identifier flat dictionary serialization, and mathematical verification.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Course:
    """A single course/subject definition."""
    code: str                                              # e.g. "CSC501", "42211"
    name: str                                              # e.g. "Computer Network"
    heads: List[str] = field(default_factory=list)         # e.g. ["SE", "IA", "TOT"] or ["TW", "PO", "TOT"]
    max_marks: Dict[str, int] = field(default_factory=dict)
    min_marks: Dict[str, int] = field(default_factory=dict)
    credits: float = 0.0


@dataclass
class CourseCatalog:
    """Registry of all courses discovered from a document."""
    courses: List[Course] = field(default_factory=list)

    def code_to_name(self) -> Dict[str, str]:
        return {c.code: c.name for c in self.courses}

    def name_to_code(self) -> Dict[str, str]:
        return {c.name: c.code for c in self.courses}

    def get_by_code(self, code: str) -> Optional[Course]:
        clean = code.strip().replace(" ", "").upper()
        for c in self.courses:
            if c.code.strip().replace(" ", "").upper() == clean:
                return c
        return None

    def to_dict(self) -> List[Dict[str, Any]]:
        return [
            {
                "code": c.code,
                "name": c.name,
                "heads": c.heads,
                "max_marks": c.max_marks,
                "min_marks": c.min_marks,
                "credits": c.credits
            }
            for c in self.courses
        ]


@dataclass
class ExamMeta:
    """Examination-level metadata extracted from headers."""
    exam_title: str = ""
    college_name: str = ""
    college_code: str = ""
    result_date: str = ""
    semester: str = ""          # "III", "IV", "V", "VI", "VII", "VIII"
    semester_num: int = 0
    scheme: str = ""            # "CBCGS", "C-Scheme", "NEP 2020"
    academic_year: str = ""
    max_total_marks: int = 0
    archetype: str = ""
    catalog: CourseCatalog = field(default_factory=CourseCatalog)


@dataclass
class CourseResult:
    """A single student's marks and grades for one course."""
    course_code: str
    course_name: str = ""
    marks: Dict[str, str] = field(default_factory=dict)    # e.g. {"SE": "34", "IA": "12", "TOT": "46"}
    grade: str = ""
    grade_point: str = ""
    credits: str = ""
    credit_points: str = ""    # credits * grade_point


@dataclass
class StudentRecord:
    """Complete student record supporting all semesters and archetypes."""
    seat_no: str = ""
    student_name: str = ""
    gender: str = ""           # "M" or "F"
    candidate_type: str = ""   # "Regular" or "Diploma"
    prn: str = ""
    abc_id: str = ""
    ern: str = ""
    mother_name: str = ""
    semester: str = ""
    result_status: str = ""
    remark: str = ""
    total_marks: str = ""
    max_marks: str = ""
    credits_earned: str = ""
    total_cg: str = ""
    sgpi: str = ""
    cgpi: str = ""
    course_results: List[CourseResult] = field(default_factory=list)
    history: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def to_flat_dict(self, catalog: Optional[CourseCatalog] = None) -> Dict[str, Any]:
        """
        Flatten to dict with DUAL IDENTIFIERS matching existing extractor output.
        Produces columns like: CSC301_SE, CSC301_IA, CSC301_TOT, CSC301_Grade,
        CSC301_GP, CSC301_Credits, CSC301_CG, CSC301_Subject
        """
        d: Dict[str, Any] = {
            "Seat_No": self.seat_no,
            "Student_Name": self.student_name,
            "Gender": self.gender,
            "Candidate_Type": self.candidate_type or "Regular",
            "PRN": self.prn,
            "ABC_ID": self.abc_id,
            "Semester": self.semester or "Current",
            "Result_Status": self.result_status,
            "Remark": self.remark or "None",
            "Total_Marks": self.total_marks,
            "Max_Marks": self.max_marks,
            "Credits_Earned": self.credits_earned,
            "Total_CG": self.total_cg,
            "SGPI": self.sgpi,
        }

        if self.ern:
            d["ERN"] = self.ern
        if self.mother_name:
            d["Mother_Name"] = self.mother_name

        for cr in self.course_results:
            c_name = cr.course_name
            if not c_name and catalog:
                c_obj = catalog.get_by_code(cr.course_code)
                if c_obj:
                    c_name = c_obj.name

            for head, val in cr.marks.items():
                d[f"{cr.course_code}_{head}"] = val
            d[f"{cr.course_code}_Grade"] = cr.grade
            d[f"{cr.course_code}_GP"] = cr.grade_point
            d[f"{cr.course_code}_Credits"] = cr.credits
            d[f"{cr.course_code}_CG"] = cr.credit_points
            if c_name:
                d[f"{cr.course_code}_Subject"] = c_name

        # Historical semester data
        for sem_label, sem_data in self.history.items():
            if isinstance(sem_data, dict):
                if sem_data.get("sgpi"):
                    d[f"SGPI_Sem_{sem_label}"] = sem_data["sgpi"]
                if sem_data.get("credits"):
                    d[f"Credits_Sem_{sem_label}"] = sem_data["credits"]
                if sem_data.get("marks"):
                    d[f"Marks_Sem_{sem_label}"] = sem_data["marks"]
                if sem_data.get("max"):
                    d[f"Max_Sem_{sem_label}"] = sem_data["max"]
            elif isinstance(sem_data, (str, int, float)) and str(sem_data).strip():
                d[f"SGPI_Sem_{sem_label}"] = str(sem_data).strip()

        if self.cgpi:
            d["CGPI"] = self.cgpi

        return d


@dataclass
class ChecksumResult:
    """Result of mathematical verification for one student."""
    student_seat_no: str = ""
    component_sum_ok: bool = True
    course_cg_ok: bool = True
    sem_total_cg_ok: bool = True
    sgpi_ok: bool = True
    all_passed: bool = True
    failures: List[str] = field(default_factory=list)


@dataclass
class PageContent:
    """Extracted text content from a single PDF page."""
    page_num: int = 0
    text: str = ""
    line_count: int = 0
    char_count: int = 0
    is_ocr: bool = False
    numeric_density: float = 0.0


@dataclass
class PageClassification:
    """Result of possibility matching for a page."""
    archetype: str = ""          # "tabulation", "gazette_classic", "gazette_spool", "gazette_nep"
    page_type: str = ""          # "header", "data", "boilerplate"
    confidence: float = 0.0
    matched_keywords: List[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Overall outcome of a document extraction."""
    meta: ExamMeta = field(default_factory=ExamMeta)
    students: List[StudentRecord] = field(default_factory=list)
    raw_dicts: List[Dict[str, Any]] = field(default_factory=list)
    archetype: str = ""
    total_pages: int = 0
    processed_pages: int = 0
    checksum_summary: Dict[str, Any] = field(default_factory=dict)
    model_used: str = ""
    duration_seconds: float = 0.0
