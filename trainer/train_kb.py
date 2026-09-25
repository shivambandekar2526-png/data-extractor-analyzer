"""
Knowledge Base Trainer
======================
Compiles layout archetypes, course schemas, and few-shot student exemplars
from historical batch data and ground-truth extractions.
Produces pre-trained knowledge base files in knowledge_base/.
"""

import csv
import json
import os
import re
from typing import Any, Dict, List, Optional

import pdfplumber

TRAINER_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(TRAINER_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, ".."))
BATCH_DATA_DIR = os.path.join(PROJECT_ROOT, "Batch Data")
OUTPUT_GT_DIR = os.path.join(PROJECT_ROOT, "data_extractor", "output")
KB_DIR = os.path.join(APP_DIR, "knowledge_base")
EXEMPLARS_DIR = os.path.join(KB_DIR, "exemplars")


def build_archetypes_file() -> Dict[str, Any]:
    """Compile comprehensive archetype signatures."""
    archetypes = {
        "tabulation": {
            "name": "tabulation",
            "description": "College Tabulation Ledger (Terna Engineering College, CBCGS Scheme, Semesters III-VI)",
            "keywords_primary": [
                "TERNA ENGINEERING COLLEGE",
                "TERNA ENGINEERINGCOLLEGE",
                "Tabulation Register",
                "Name of the Student"
            ],
            "keywords_secondary": [
                "Marks Obtained",
                "Grade Pt.",
                "Credit Pt.",
                "CP*GP",
                "EC ECG GPA",
                "Outof",
                "Sr. No.",
                "Seat No."
            ],
            "keywords_negative": [
                "OFFICE REGISTER",
                "NEP 2020",
                "PAPER 1"
            ],
            "seat_number_pattern": r"\b(38\d{6}|48\d{6}|58\d{6}|\d{8})\b",
            "student_block_structure": {
                "lines_per_student": 8,
                "anchor_token": "Marks",
                "row_type_tokens": ["Marks Obtained", "Grade", "Grade Pt.", "Credit Pt.", "CP*GP"],
                "gender_marker": "Leading slash '/' on student name"
            },
            "semesters": ["III", "IV", "V", "VI"],
            "max_marks_typical": [700, 725, 750, 775],
            "confidence_threshold": 0.75
        },
        "gazette_classic": {
            "name": "gazette_classic",
            "description": "University Gazette C-Scheme Ledger (Mumbai University, Semester VIII)",
            "keywords_primary": [
                "OFFICE REGISTER",
                "COLLEGE/CENTRE NAME",
                "äC:",
                "ΣC"
            ],
            "keywords_secondary": [
                "SEAT_NO",
                "PRN_NO",
                "ABCID",
                "Th(80/32)",
                "In(20/08)",
                "C G GP C*G",
                "SGPI:",
                "CREDITS:",
                "MARKS:",
                "CGPI:"
            ],
            "keywords_negative": [
                "NEP 2020",
                "PAPER 1",
                "Tabulation Register"
            ],
            "seat_number_pattern": r"\b(4\d{6}|\d{7})\b",
            "student_block_structure": {
                "lines_per_student": 8,
                "anchor_token": r"\b\d{7}\b\s+[/A-Za-z\s]+?\s+\(\d{16}\)",
                "fields": ["seat_no", "name", "prn", "abc_id", "sem_history", "cgpi"]
            },
            "semesters": ["VIII"],
            "max_marks_typical": [700],
            "confidence_threshold": 0.75
        },
        "gazette_spool": {
            "name": "gazette_spool",
            "description": "University Gazette Multi-Row Continuous Spool (Mumbai University, Semester VII)",
            "keywords_primary": [
                "PAPER 1",
                "PAPER 7",
                "PAPER 12"
            ],
            "keywords_secondary": [
                "ThUA",
                "ORCA",
                "TwCA",
                "Mother Name",
                "CENTRE-COLLEGE",
                "CR GR GP C*G"
            ],
            "keywords_negative": [
                "NEP 2020",
                "Tabulation Register"
            ],
            "seat_number_pattern": r"\b(1\d{6}|\d{7})\b",
            "student_block_structure": {
                "lines_per_student": 8,
                "layout": "Two-tier block: Papers 1-6 on top, Papers 7-12 below",
                "anchor_token": r"\b\d{7}\b\s+[/A-Za-z\s]+?\|"
            },
            "semesters": ["VII"],
            "max_marks_typical": [725, 750],
            "confidence_threshold": 0.75
        },
        "gazette_nep": {
            "name": "gazette_nep",
            "description": "University Gazette NEP 2020 Scheme (Mumbai University, Semester III/IV)",
            "keywords_primary": [
                "NEP 2020",
                "(NEP 2020)",
                "( NEP 2020 )"
            ],
            "keywords_secondary": [
                "TOTALF (TOTAL MARKS)",
                "I1 (INTERNAL EXAMINATION)",
                "E1 (EXTERNAL EXAMINATION)",
                "T1 (TERM WORK)",
                "O1 (ORAL)",
                "Bachelor of Engineering(Arti",
                "ERN COLLEGE"
            ],
            "keywords_negative": [
                "Tabulation Register",
                "PAPER 1"
            ],
            "seat_number_pattern": r"\b\d{9,10}\b",
            "student_block_structure": {
                "anchor_token": r"\b\d{9,10}\b\s+[A-Za-z\s/\.]+?\s+(Regular|Diploma)",
                "component_prefixes": ["E1", "I1", "T1", "O1", "TOT"]
            },
            "semesters": ["III", "IV"],
            "max_marks_typical": [750],
            "confidence_threshold": 0.70
        }
    }
    return archetypes


def build_schemas_file() -> Dict[str, Any]:
    """Crawl ground truth catalogs to build complete course and semester schemas."""
    catalogs_by_sem: Dict[str, Any] = {}

    if os.path.isdir(OUTPUT_GT_DIR):
        for entry in sorted(os.listdir(OUTPUT_GT_DIR)):
            folder = os.path.join(OUTPUT_GT_DIR, entry)
            if not os.path.isdir(folder):
                continue
            cat_file = os.path.join(folder, f"{entry}_catalog.json")
            if os.path.isfile(cat_file):
                try:
                    with open(cat_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    sem = data.get("semester") or entry
                    arch = data.get("archetype") or "tabulation"
                    courses = data.get("courses", [])

                    key = f"{arch}_{sem}"
                    if key not in catalogs_by_sem:
                        catalogs_by_sem[key] = {
                            "archetype": arch,
                            "semester": sem,
                            "exam_title": data.get("exam_title", ""),
                            "college_name": data.get("college_name", ""),
                            "courses": courses
                        }
                except Exception:
                    pass

    schemas_data = {
        "version": "1.0",
        "archetypes": {
            "tabulation": {
                "theory_heads": ["SE", "IA", "TOT"],
                "theory_with_tw_heads": ["SE", "IA", "TOT", "TW"],
                "lab_heads": ["TW", "PO", "TOT"],
                "lab_tw_only_heads": ["TW"],
                "project_heads": ["TW", "PO", "TOT"],
                "summary_fields": ["Credits_Earned", "Total_CG", "SGPI", "Result_Status", "Total_Marks"]
            },
            "gazette_classic": {
                "theory_heads": ["TH", "IN", "Total"],
                "lab_heads": ["TW", "OR", "Total"],
                "project_heads": ["TW", "OR_PR", "Total"],
                "summary_fields": ["Credits_Earned", "Total_CG", "SGPI", "CGPI", "Result_Status", "Total_Marks"]
            },
            "gazette_spool": {
                "theory_heads": ["TH_OR", "IA_TW", "Total"],
                "lab_heads": ["TW"],
                "summary_fields": ["Credits_Earned", "Total_CG", "SGPI", "Result_Status", "Total_Marks"]
            },
            "gazette_nep": {
                "component_prefixes": ["E1", "I1", "T1", "O1", "TOT"],
                "summary_fields": ["Credits_Earned", "Total_CG", "SGPI", "Result_Status", "Total_Marks"]
            }
        },
        "catalogs": catalogs_by_sem
    }
    return schemas_data


def get_first_csv_rows(csv_path: str, max_rows: int = 5) -> List[Dict[str, str]]:
    """Read top student records from a ground truth CSV file."""
    if not os.path.isfile(csv_path):
        return []
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, r in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(r)
    return rows


def build_exemplars() -> None:
    """Build high quality exemplar training files for each archetype."""
    os.makedirs(EXEMPLARS_DIR, exist_ok=True)

    # 1. Tabulation Exemplar (Sem III CBCGS)
    tab_csv = os.path.join(OUTPUT_GT_DIR, "2223-3rd", "2223-3rd_extracted_data.csv")
    tab_rows = get_first_csv_rows(tab_csv, 4)

    tab_exemplar = {
        "archetype": "tabulation",
        "description": "Exemplars for College Tabulation Grid (CBCGS Sem III-VI)",
        "exemplars": [
            {
                "description": "Regular male student passing all courses",
                "seat_no": tab_rows[0].get("Seat_No", "38213001") if tab_rows else "38213001",
                "student_name": tab_rows[0].get("Student_Name", "BASUDE ADITYA YASHWANT") if tab_rows else "BASUDE ADITYA YASHWANT",
                "input_snippet": (
                    "1 BASUDE ADITYA YASHWANT Marks 34 12 46 16 54 11 65 52 14 66 32 08 40 36 12 48 17 17 34 17 16 17 33 35 18 53 17 18 35 453\n"
                    "Obtained\n"
                    "Grade P C E C C D C C B C P P P E C E C C C C C C C B B B C B B P\n"
                    "Grade Pt. 5 7 7 7 4 5 7 7 7 8 8\n"
                    "38213001\n"
                    "Credit Pt. 3 1 3 3 3 3 1 1 1 2 2\n"
                    "23 144 6.26\n"
                    "CP*GP 15 7 21 21 12 15 7 7 7 16 16"
                ),
                "expected_record": tab_rows[0] if tab_rows else {}
            },
            {
                "description": "Female student (name starts with slash '/')",
                "seat_no": tab_rows[1].get("Seat_No", "38213002") if len(tab_rows) > 1 else "38213002",
                "student_name": tab_rows[1].get("Student_Name", "BHALEKAR SIDDHI SATISH") if len(tab_rows) > 1 else "BHALEKAR SIDDHI SATISH",
                "input_snippet": (
                    "2 /BHALEKAR SIDDHI SATISH Marks 32 08 40 16 32 12 44 33 13 46 32 10 42 34 10 44 18 19 37 17 17 20 37 40 20 60 19 19 38 421\n"
                    "Obtained\n"
                    "Grade P P P C P C P P C E P D P P D P B A B C C O B O O O A A A P\n"
                    "Grade Pt. 4 7 4 5 4 4 8 7 8 10 9\n"
                    "38213002\n"
                    "Credit Pt. 3 1 3 3 3 3 1 1 1 2 2\n"
                    "23 131 5.7\n"
                    "CP*GP 12 7 12 15 12 12 8 7 8 20 18"
                ),
                "expected_record": tab_rows[1] if len(tab_rows) > 1 else {}
            },
            {
                "description": "Failing / ATKT student with carried marks (E/F suffixes)",
                "seat_no": tab_rows[2].get("Seat_No", "38213003") if len(tab_rows) > 2 else "38213003",
                "student_name": tab_rows[2].get("Student_Name", "BHATTACHARJEE BIBHUPRASAD BISWANATH") if len(tab_rows) > 2 else "BHATTACHARJEE BIBHUPRASAD BISWANATH",
                "input_snippet": (
                    "3 BHATTACHARJEE BIBHUPRASAD Marks 18F 08E 26 18E 50E 13E 63 39E 12E 51 43E 08E 51 34E 09E 43 16E 15E 31 18E 14E 15E 29 39E 17E 56 17E 17E 34 420\n"
                    "BISWANATH Obtained\n"
                    "Grade F P -- B C C C E C D D P D P E P C C C B D C D A C B C C C F\n"
                    "Grade Pt. -- 8 7 6 6 4 7 8 6 8 7\n"
                    "38213003\n"
                    "Credit Pt. -- 1 3 3 3 3 1 1 1 2 2\n"
                    "20 128 --\n"
                    "CP*GP -- 8 21 18 18 12 7 8 6 16 14"
                ),
                "expected_record": tab_rows[2] if len(tab_rows) > 2 else {}
            }
        ]
    }
    with open(os.path.join(EXEMPLARS_DIR, "tabulation.json"), "w", encoding="utf-8") as f:
        json.dump(tab_exemplar, f, indent=2, ensure_ascii=False)

    # 2. Gazette Classic Exemplar (Sem VIII C-Scheme)
    classic_csv = os.path.join(OUTPUT_GT_DIR, "2223-8th", "2223-8th_extracted_data.csv")
    classic_rows = get_first_csv_rows(classic_csv, 4)

    classic_exemplar = {
        "archetype": "gazette_classic",
        "description": "Exemplars for University Gazette B.E. C-Scheme (Sem VIII)",
        "exemplars": [
            {
                "description": "Regular male student passing Sem VIII with previous semester history",
                "seat_no": classic_rows[1].get("Seat_No", "4005501") if len(classic_rows) > 1 else "4005501",
                "student_name": classic_rows[1].get("Student_Name", "SHAIKH HUZAIFA MUSTAFA") if len(classic_rows) > 1 else "SHAIKH HUZAIFA MUSTAFA",
                "input_snippet": (
                    "4005501 SHAIKH HUZAIFA MUSTAFA SHAGUFTA (2022016402248621) (747454521408)\n"
                    "48 57 60 51 17 20 15 78\n"
                    "16 16 14 19 23 21 22 39 P 516\n"
                    "64 73 74 70 40 41 37 117\n"
                    "3 C 7 21 3 B 8 24 3 B 8 24 3 B 8 24 1 O 10 10 1 O 10 10 1 B 8 8 6 A 9 54 21 175 8.33\n"
                    "SGPI: SEM-I: 7.17 ; SEM-II: 6.20 ; SEM-III: 6.52 ; SEM-IV: 6.00 ; SEM-V: 7.23 ; SEM-VI: 6.78 ; SEM-VII: 7.86 ; CGPI: 6.9\n"
                    "CREDITS: SEM-I: 18 ; SEM-II: 20 ; SEM-III: 23 ; SEM-IV: 24 ; SEM-V: 22 ; SEM-VI: 23 ; SEM-VII: 22\n"
                    "MARKS: SEM-I:437/675; SEM-II:428/725; SEM-III:454/775; SEM-IV:429/775; SEM-V:482/750; SEM-VI:463/775; SEM-VII:519/725; TOTAL: 3212/4900"
                ),
                "expected_record": classic_rows[1] if len(classic_rows) > 1 else {}
            },
            {
                "description": "Diploma candidate (Direct Second Year) Sem VIII record",
                "seat_no": classic_rows[2].get("Seat_No", "4005540") if len(classic_rows) > 2 else "4005540",
                "student_name": classic_rows[2].get("Student_Name", "BASUDE ADITYA YASHWANT") if len(classic_rows) > 2 else "BASUDE ADITYA YASHWANT",
                "input_snippet": (
                    "4005540 BASUDE ADITYA YASHWANT KALAWATI (2023016402110417) (803444155335)\n"
                    "46 42 54 43 21 22 19 84\n"
                    "13 10 10 15 21 18 19 43 P 480\n"
                    "59 52 64 58 42 40 38 127\n"
                    "3 D 6 18 3 D 6 18 3 C 7 21 3 D 6 18 1 O 10 10 1 O 10 10 1 A 9 9 6 O 10 60 21 164 7.81\n"
                    "(DIPLOMA STUDENT) SGPI: SEM-III: 6.26 ; SEM-IV: 6.88 ; SEM-V: 6.68 ; SEM-VI: 7.04 ; SEM-VII: 7.55 ; CGPI: 7.02\n"
                    "CREDITS: SEM-III: 23 ; SEM-IV: 24 ; SEM-V: 22 ; SEM-VI: 23 ; SEM-VII: 22\n"
                    "MARKS: SEM-III:453/775; SEM-IV:465/775; SEM-V:462/750; SEM-VI:494/775; SEM-VII:485/725; TOTAL: 2839/4500 (63.09%)"
                ),
                "expected_record": classic_rows[2] if len(classic_rows) > 2 else {}
            }
        ]
    }
    with open(os.path.join(EXEMPLARS_DIR, "gazette_classic.json"), "w", encoding="utf-8") as f:
        json.dump(classic_exemplar, f, indent=2, ensure_ascii=False)

    # 3. Gazette Spool Exemplar (Sem VII)
    spool_csv = os.path.join(OUTPUT_GT_DIR, "2223-7th", "2223-7th_extracted_data.csv")
    spool_rows = get_first_csv_rows(spool_csv, 3)

    spool_exemplar = {
        "archetype": "gazette_spool",
        "description": "Exemplars for Continuous Tape Spool (Sem VII, 12 Papers)",
        "exemplars": [
            {
                "description": "Regular student passing Sem VII spool record",
                "seat_no": spool_rows[0].get("Seat_No", "1006190") if spool_rows else "1006190",
                "student_name": spool_rows[0].get("Student_Name", "BIBHUPRASAD BISWANATH BHATTACHARJEE") if spool_rows else "BIBHUPRASAD BISWANATH BHATTACHARJEE",
                "input_snippet": (
                    "1006190 BIBHUPRASAD BISWANATH |42211 |42212 |AIDSCSL701 |AIDSCSL702 |AIDSCSP701 |42213 | Successful\n"
                    "BHATTACHARJEE - SHIKHA |32 15 47 |44 16 60 |-- 17 34 |-- 19 36 |-- 21 41 |49 15 64 | 22.00 153.00\n"
                    " | | |-- 17 |-- 17 |-- 20 | |\n"
                    "2022016402245232 |3.00 E 5.00 15.00 |3.00 C 7.00 21.00 |1.00 C 7.00 7.00 |1.00 B 8.00 8.00 |3.00 D 6.00 18.00 |3.00 C 7.00 21.00 | 6.95 --"
                ),
                "expected_record": spool_rows[0] if spool_rows else {}
            }
        ]
    }
    with open(os.path.join(EXEMPLARS_DIR, "gazette_spool.json"), "w", encoding="utf-8") as f:
        json.dump(spool_exemplar, f, indent=2, ensure_ascii=False)

    # 4. Gazette NEP 2020 Exemplar
    nep_exemplar = {
        "archetype": "gazette_nep",
        "description": "Exemplars for Mumbai University NEP 2020 Scheme (Sem III/IV)",
        "exemplars": [
            {
                "description": "NEP 2020 student record with prefixed components (E1/I1/T1/O1/TOT)",
                "seat_no": "202401640001",
                "student_name": "DESHMUKH ARYAN VIKAS",
                "input_snippet": (
                    "202401640001 DESHMUKH ARYAN VIKAS Regular MALE ERN:2024016402391001 237:TCE\n"
                    "1221311 Introduction to Management (2) External: 22 Internal: 14 TOT: 36 GP: 8 G: B C: 2.00 CG: 16.00\n"
                    "2113111 Mathematics for Computer Engineering External: 42 Internal: 28 Term Work: 18 TOT: 88 GP: 9 G: A C: 3.00 CG: 27.00\n"
                    "TOTAL: 532 RESULT: PASS REMARK: None ΣC: 22.00 ΣCG: 185.00 SGPI: 8.41"
                ),
                "expected_record": {
                    "Seat_No": "202401640001",
                    "Student_Name": "DESHMUKH ARYAN VIKAS",
                    "Gender": "M",
                    "Candidate_Type": "Regular",
                    "ERN": "2024016402391001",
                    "Result_Status": "PASS",
                    "Total_Marks": "532",
                    "Credits_Earned": "22.00",
                    "Total_CG": "185.00",
                    "SGPI": "8.41"
                }
            }
        ]
    }
    with open(os.path.join(EXEMPLARS_DIR, "gazette_nep.json"), "w", encoding="utf-8") as f:
        json.dump(nep_exemplar, f, indent=2, ensure_ascii=False)


def run_training() -> Dict[str, Any]:
    """Execute complete initial training and save all knowledge base files."""
    os.makedirs(KB_DIR, exist_ok=True)
    os.makedirs(EXEMPLARS_DIR, exist_ok=True)

    print("Building archetypes.json...")
    archetypes = build_archetypes_file()
    with open(os.path.join(KB_DIR, "archetypes.json"), "w", encoding="utf-8") as f:
        json.dump(archetypes, f, indent=2, ensure_ascii=False)

    print("Building schemas.json...")
    schemas = build_schemas_file()
    with open(os.path.join(KB_DIR, "schemas.json"), "w", encoding="utf-8") as f:
        json.dump(schemas, f, indent=2, ensure_ascii=False)

    print("Building exemplars...")
    build_exemplars()

    summary = {
        "status": "trained",
        "archetypes_count": len(archetypes),
        "catalogs_count": len(schemas.get("catalogs", {})),
        "exemplar_files": os.listdir(EXEMPLARS_DIR)
    }
    print(f"Training completed successfully: {summary}")
    return summary


if __name__ == "__main__":
    run_training()
