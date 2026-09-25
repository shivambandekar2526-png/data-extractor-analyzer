"""
Prompt Builder
==============
Constructs high-precision extraction prompts conditioned on:
1. Document Archetype
2. Discovered Course Catalog
3. Pre-trained few-shot exemplars from knowledge_base/
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

KB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "knowledge_base"))
EXEMPLARS_DIR = os.path.join(KB_DIR, "exemplars")
SCHEMAS_FILE = os.path.join(KB_DIR, "schemas.json")


def load_exemplars(archetype: str) -> List[Dict[str, Any]]:
    """Load pre-trained few-shot examples for an archetype."""
    path = os.path.join(EXEMPLARS_DIR, f"{archetype}.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("exemplars", [])
        except Exception:
            pass
    return []


def build_catalog_prompt(header_text: str, archetype: str) -> Tuple[str, str]:
    """
    Construct prompt to discover course catalog from page 0 headers.
    """
    system_prompt = (
        "You are an academic course catalog extractor. "
        "Extract all distinct course codes, course names, component heads, and credit weights "
        "from the provided university document header into JSON format."
    )

    schema_example = {
        "exam_title": "string (e.g. SECOND YEAR ARTIFICIAL INTELLIGENCE SEMESTER - III)",
        "semester": "string (III, IV, V, VI, VII, or VIII)",
        "college_name": "string",
        "result_date": "string (e.g. DECEMBER 2023)",
        "courses": [
            {
                "code": "CSC301",
                "name": "Engineering Mathematics - III",
                "heads": ["SE", "IA", "TOT", "TW"],
                "credits": 4.0
            }
        ]
    }

    user_prompt = f"""Extract examination metadata and course catalog from the following header text into JSON:

JSON SCHEMA:
{json.dumps(schema_example, indent=2)}

HEADER TEXT:
{header_text[:4000]}
"""
    return system_prompt, user_prompt


def build_data_extraction_prompt(
    page_text: str,
    archetype: str,
    course_catalog: Optional[List[Dict[str, Any]]] = None,
    page_num: int = 1,
    max_exemplars: int = 2
) -> Tuple[str, str]:
    """
    Build prompt to extract student records from a page.
    Includes few-shot exemplars and the exact JSON schema.
    """
    system_prompt = (
        f"You are an expert data extractor specialized in university {archetype} result sheets. "
        "Extract EVERY student record present in the page text into valid, normalized JSON. "
        "Never hallucinate or skip any numbers. "
        "Remove leading slashes from female student names and record gender as 'F'."
    )

    schema_def = {
        "students": [
            {
                "seat_no": "string (7-10 digit seat number)",
                "student_name": "string (full student name, without leading '/')",
                "gender": "string ('M' or 'F')",
                "candidate_type": "string ('Regular' or 'Diploma')",
                "prn": "string (16-digit PRN or blank)",
                "abc_id": "string (12-digit ABC ID or blank)",
                "ern": "string (NEP enrollment number or blank)",
                "mother_name": "string (if present or blank)",
                "result_status": "string ('P', 'F', 'PASS', 'FAIL', 'Successful', 'Unsuccessful', 'ABS')",
                "remark": "string ('None', 'RPV', 'ATKT')",
                "total_marks": "string (numeric)",
                "max_marks": "string (e.g. '750', '775', '700')",
                "credits_earned": "string (numeric credits)",
                "total_cg": "string (sum of credits * GP)",
                "sgpi": "string (semester GPA, e.g. '6.26')",
                "cgpi": "string (cumulative GPA if present)",
                "courses": {
                    "COURSE_CODE": {
                        "HEAD": "string (mark value)",
                        "Grade": "string (e.g. 'O', 'A', 'B', 'C', 'D', 'E', 'P', 'F', '--')",
                        "GP": "string (grade point)",
                        "Credits": "string",
                        "CG": "string"
                    }
                }
            }
        ]
    }

    prompt_parts = [
        f"EXTRACT ALL STUDENT RECORDS FROM PAGE {page_num + 1} INTO THE FOLLOWING JSON SCHEMA:",
        json.dumps(schema_def, indent=2)
    ]

    if course_catalog:
        prompt_parts.append("\nDISCOVERED COURSE CATALOG FOR THIS DOCUMENT:")
        cat_summary = [{"code": c.get("code"), "name": c.get("name"), "heads": c.get("heads")} for c in course_catalog]
        prompt_parts.append(json.dumps(cat_summary[:15], indent=2))

    # Add 1-2 few-shot exemplars from knowledge base
    exemplars = load_exemplars(archetype)
    if exemplars:
        prompt_parts.append("\nTRAINED FEW-SHOT EXAMPLES:")
        for idx, ex in enumerate(exemplars[:max_exemplars], 1):
            prompt_parts.append(f"\n--- EXAMPLE {idx} ({ex.get('description', '')}) ---")
            prompt_parts.append("SAMPLE TEXT:")
            prompt_parts.append(ex.get("input_snippet", ""))
            exp_rec = ex.get("expected_record", {})
            prompt_parts.append("EXPECTED JSON OUTPUT FOR THIS STUDENT:")
            prompt_parts.append(json.dumps(exp_rec, indent=2))

    prompt_parts.append(f"\nNOW EXTRACT ALL STUDENTS FROM THIS PAGE TEXT (DO NOT SKIP ANY STUDENT):\n{page_text}")

    user_prompt = "\n".join(prompt_parts)
    return system_prompt, user_prompt
