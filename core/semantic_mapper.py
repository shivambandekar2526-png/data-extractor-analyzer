"""
Semantic Parameter Mapper
=========================
Intelligent parameter mapping engine that dynamically bridges heterogeneous
university result schemas to canonical EDA analytical parameters.

Architecture (Token-Minimization):
- Tier 1: Local Regex Synonym Resolver (0 tokens, <0.1ms execution time).
  Resolves standard variations: SGPI, Sgpi, sgpi, SGPA, GPA, Seat_No, ROLL NO, etc.
- Tier 2: AI Semantic Parameter Resolver (~30-50 tokens).
  Only triggered if critical parameters cannot be resolved deterministically.
  Sends only column headers + 1 sample record to Ollama / Cloud AI.
- Cache: Results are hashed and cached in-memory and to disk per column signature.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("SemanticMapper")

# Canonical parameters required for comprehensive academic EDA
CANONICAL_TARGETS = {
    "seat": "Unique student identifier / Roll No / Seat No",
    "name": "Full student name",
    "sgpi": "Grade point index for the semester (numeric, e.g. 8.45)",
    "total_marks": "Total marks obtained by the student in the semester",
    "max_marks": "Maximum possible total marks (e.g. 750, 775, 800)",
    "result": "Pass/Fail or Successful/Unsuccessful status",
    "gender": "Student gender (M/F)",
    "candidate_type": "Regular vs. Direct Second Year (Diploma)",
    "prn": "Permanent Registration Number / ABC ID",
    "semester": "Exam semester (e.g. III, V, VII, VIII)",
}

# Tier 1 Regex Dictionary
REGEX_SYNONYMS = {
    "seat": [
        r"^seat[_\s]?no\.?$", r"^seat$", r"^roll[_\s]?no\.?$", r"^roll$",
        r"^candidate[_\s]?id$", r"^registration[_\s]?no\.?$"
    ],
    "name": [
        r"^student[_\s]?name$", r"^name$", r"^candidate[_\s]?name$",
        r"^name[_\s]of[_\s]the[_\s]candidate$", r"^full[_\s]?name$"
    ],
    "sgpi": [
        r"^sgpi$", r"^sgpa$", r"^gpa$", r"^cgpa$", r"^spi$",
        r"^grade[_\s]?point[_\s]?(average|avg|index)$", r"^sem[_\s]?sgpi$"
    ],
    "total_marks": [
        r"^total[_\s]?marks$", r"^total$", r"^tot[_\s]?marks$",
        r"^marks[_\s]?obtained$", r"^aggregate[_\s]?marks$", r"^grand[_\s]?total$"
    ],
    "max_marks": [
        r"^max[_\s]?marks$", r"^out[_\s]?of$", r"^maximum[_\s]?marks$",
        r"^total[_\s]?max$", r"^max$"
    ],
    "result": [
        r"^result[_\s]?status$", r"^result$", r"^status$", r"^remark$", r"^outcome$"
    ],
    "gender": [
        r"^gender$", r"^sex$"
    ],
    "candidate_type": [
        r"^candidate[_\s]?type$", r"^admission[_\s]?type$", r"^category$",
        r"^reg[_\s/]?dip$", r"^student[_\s]?type$"
    ],
    "prn": [
        r"^prn[_\s]?no\.?$", r"^prn$", r"^abc[_\s]?id$", r"^ern$",
        r"^enrollment[_\s]?no\.?$"
    ],
    "semester": [
        r"^semester$", r"^sem$", r"^sem[_\s]?no\.?$"
    ]
}

# Subject component patterns
SUB_COMPONENT_PATTERN = re.compile(
    r"^(?P<code>[A-Za-z0-9]+)[_\s\-]+(?P<comp>SE|IA|TOT|TW|PO|OR|OR_PR|Grade|GP|Credits|CG|Subject|Total)$",
    re.IGNORECASE
)


class SemanticMapper:
    """Dynamically resolves column names to canonical parameters with zero-token preference."""

    def __init__(self, cache_file: Optional[str] = None):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.cache_file = cache_file or os.path.join(
            os.path.dirname(__file__), "..", "knowledge_base", "semantic_map_cache.json"
        )
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load semantic cache: {e}")

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save semantic cache: {e}")

    def _signature(self, columns: List[str]) -> str:
        """Create deterministic SHA-256 signature from column names."""
        sorted_cols = sorted([c.strip().lower() for c in columns])
        return hashlib.sha256("||".join(sorted_cols).encode("utf-8")).hexdigest()[:16]

    def map_columns(
        self,
        columns: List[str],
        sample_rows: Optional[List[Dict[str, Any]]] = None,
        ollama_client=None,
        cloud_client=None
    ) -> Dict[str, Any]:
        """
        Map input column names to canonical EDA parameters.
        Returns a dictionary containing:
          - 'mappings': { canonical_name -> column_name }
          - 'subjects': { course_code -> { component -> column_name } }
          - 'unmapped': list of columns not mapped
          - 'tier_used': 1 (Deterministic), 2 (AI), or 'cache'
          - 'token_usage': estimated tokens spent (0 for Tier 1 / cache)
        """
        clean_cols = [str(c).strip() for c in columns if str(c).strip()]
        sig = self._signature(clean_cols)

        # Check Cache
        if sig in self.cache:
            res = dict(self.cache[sig])
            res["tier_used"] = "cache"
            res["token_usage"] = 0
            return res

        # Tier 1: Deterministic Regex Matching
        mappings: Dict[str, str] = {}
        used_cols: Set[str] = set()

        for canonical, patterns in REGEX_SYNONYMS.items():
            for col in clean_cols:
                if col in used_cols:
                    continue
                for pat in patterns:
                    if re.match(pat, col, re.IGNORECASE):
                        mappings[canonical] = col
                        used_cols.add(col)
                        break
                if canonical in mappings:
                    break

        # Discover Subject Columns
        reserved_prefixes = {"TOTAL", "MAX", "MIN", "CREDITS", "SGPI", "MARKS", "SUM", "RESULT", "OUT"}
        subjects: Dict[str, Dict[str, str]] = {}
        for col in clean_cols:
            if col in used_cols:
                continue
            m = SUB_COMPONENT_PATTERN.match(col)
            if m:
                code = m.group("code").upper()
                if code in reserved_prefixes:
                    continue
                comp = m.group("comp").upper()
                if comp == "TOTAL":
                    comp = "TOT"
                if code not in subjects:
                    subjects[code] = {}
                subjects[code][comp] = col
                used_cols.add(col)

        unmapped = [c for c in clean_cols if c not in used_cols]
        tier_used = 1
        tokens_spent = 0

        # Tier 2: AI Fallback (only if critical parameters 'sgpi' or 'result' are missing)
        critical_missing = [req for req in ["sgpi", "result", "seat"] if req not in mappings]
        if critical_missing and unmapped and (ollama_client or cloud_client):
            try:
                ai_mappings, tokens_spent = self._resolve_with_ai(
                    unmapped=unmapped,
                    missing=critical_missing,
                    sample_row=sample_rows[0] if sample_rows else None,
                    ollama_client=ollama_client,
                    cloud_client=cloud_client
                )
                for k, v in ai_mappings.items():
                    if k in critical_missing and v in unmapped:
                        mappings[k] = v
                        unmapped.remove(v)
                tier_used = 2
            except Exception as e:
                logger.warning(f"AI parameter mapping fallback failed: {e}")

        result = {
            "mappings": mappings,
            "subjects": subjects,
            "unmapped": unmapped,
            "tier_used": tier_used,
            "token_usage": tokens_spent
        }

        # Cache valid mappings
        self.cache[sig] = result
        self._save_cache()

        return result

    def _resolve_with_ai(
        self,
        unmapped: List[str],
        missing: List[str],
        sample_row: Optional[Dict[str, Any]],
        ollama_client=None,
        cloud_client=None
    ) -> Tuple[Dict[str, str], int]:
        """
        Micro-prompt to resolve only the missing canonical fields.
        Payload size is ~40 tokens.
        """
        targets_desc = {k: CANONICAL_TARGETS[k] for k in missing if k in CANONICAL_TARGETS}
        sample_snippet = {}
        if sample_row:
            for col in unmapped[:8]:
                sample_snippet[col] = str(sample_row.get(col, ""))[:30]

        prompt = (
            "Map the unmapped table column names to the target academic fields.\n"
            f"Target Fields: {json.dumps(targets_desc)}\n"
            f"Available Columns: {json.dumps(unmapped)}\n"
            f"Sample Record: {json.dumps(sample_snippet)}\n"
            "Respond ONLY with a JSON dictionary matching canonical target field to column name, e.g. "
            "{\"sgpi\": \"col_name\"}."
        )

        response_text = ""
        tokens_est = len(prompt.split()) + 30

        # Try Cloud Client first if available, else Ollama
        if cloud_client and cloud_client.is_configured():
            try:
                response_text = cloud_client.generate(prompt=prompt, temperature=0.1)
            except Exception:
                response_text = ""

        if not response_text and ollama_client and ollama_client.is_available():
            try:
                response_text = ollama_client.generate(
                    prompt=prompt,
                    system="You are an academic dataset schema mapping assistant. Output JSON only.",
                    temperature=0.1
                )
            except Exception:
                response_text = ""

        if not response_text:
            return {}, 0

        # Extract JSON from response
        try:
            m = re.search(r"\{.*\}", response_text, re.DOTALL)
            if m:
                mapping = json.loads(m.group(0))
                return mapping, tokens_est
        except Exception:
            pass

        return {}, tokens_est
