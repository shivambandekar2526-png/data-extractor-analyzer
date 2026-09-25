"""
Ollama Conversational Data Assistant
====================================
Enables interactive, natural-language modifications to extracted student datasets.
Allows users to ask Ollama to filter, delete, update, sort, or analyze the current data in real time.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from .ollama_client import OllamaClient
except Exception:
    try:
        from data_extractor_AI.core.ollama_client import OllamaClient
    except Exception:
        from core.ollama_client import OllamaClient

CHAT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["delete_slice", "delete_where", "filter_keep", "update_where", "sort", "answer"]
        },
        "reply": {"type": "string"},
        "params": {
            "type": "object",
            "properties": {
                "start": {"type": "integer"},
                "count": {"type": "integer"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "values": {"type": "array", "items": {"type": "string"}},
                "match_key": {"type": "string"},
                "match_value": {"type": "string"},
                "match_prefix": {"type": "string"},
                "updates": {"type": "object"},
                "reverse": {"type": "boolean"},
                "numeric": {"type": "boolean"}
            }
        }
    },
    "required": ["action", "reply"]
}

SYSTEM_PROMPT = (
    "You are an academic data assistant manipulating a student result dataset. "
    "Translate the user's request into a structured JSON operation.\n"
    "Supported actions:\n"
    "1. 'delete_slice': Remove N records from start/end. params: {start: 0, count: 2}\n"
    "2. 'delete_where': Remove matching records. params: {key: 'Result_Status', values: ['FAIL', 'F']}\n"
    "3. 'filter_keep': Keep only matching records. params: {key: 'Gender', value: 'F'}\n"
    "4. 'update_where': Update specific fields. params: {match_key: 'Seat_No', match_value: '1033205', updates: {'Candidate_Type': 'Diploma'}}\n"
    "   or by prefix: {match_key: 'PRN', match_prefix: '2022', updates: {'Candidate_Type': 'Diploma'}}\n"
    "5. 'sort': Sort by column. params: {key: 'SGPI', reverse: true, numeric: true}\n"
    "6. 'answer': When user asks a question without changing data. params: {}\n"
    "Always provide a friendly, professional explanation in the 'reply' field."
)


def _safe_float(val: Any) -> float:
    try:
        clean = re.sub(r"[^\d\.]", "", str(val or ""))
        return float(clean) if clean else 0.0
    except Exception:
        return 0.0


def _resolve_key(d: Dict[str, Any], target: str) -> str:
    """Find the exact matching key in dict, ignoring case, underscores and spaces."""
    if not d or target in d:
        return target
    clean_target = re.sub(r"[_\s\-]", "", str(target).lower())
    for k in d.keys():
        if re.sub(r"[_\s\-]", "", str(k).lower()) == clean_target:
            return k
    return target


def apply_action(
    action: str,
    params: Dict[str, Any],
    students: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], str]:
    """Execute the structured action on the in-memory student records."""
    modified = [dict(s) for s in students]
    total_before = len(modified)
    first_row = modified[0] if modified else {}

    if action == "delete_slice":
        start = int(params.get("start", 0))
        count = int(params.get("count", 1))
        if start < len(modified):
            del modified[start : start + count]
        return modified, f"Removed {min(count, total_before)} record(s). Total records now: {len(modified)}."

    elif action == "delete_where":
        raw_key = params.get("key", "Result_Status")
        key = _resolve_key(first_row, raw_key)
        vals = [str(v).strip().upper() for v in params.get("values", [])]
        val = params.get("value")
        if val:
            vals.append(str(val).strip().upper())
        modified = [
            s for s in modified
            if str(s.get(key, "")).strip().upper() not in vals
        ]
        return modified, f"Removed {total_before - len(modified)} matching record(s). Total records now: {len(modified)}."

    elif action == "filter_keep":
        raw_key = params.get("key", "Result_Status")
        key = _resolve_key(first_row, raw_key)
        val = str(params.get("value", "")).strip().upper()
        modified = [
            s for s in modified
            if str(s.get(key, "")).strip().upper() == val
        ]
        return modified, f"Filtered dataset to {len(modified)} matching record(s)."

    elif action == "update_where":
        raw_match_key = params.get("match_key", "Seat_No")
        match_key = _resolve_key(first_row, raw_match_key)
        match_val = str(params.get("match_value", "")).strip()
        match_pfx = str(params.get("match_prefix", "")).strip()
        updates = params.get("updates", {})

        count_updated = 0
        for s in modified:
            cur_val = str(s.get(match_key, "")).strip()
            match = False
            if match_val and cur_val == match_val:
                match = True
            elif match_pfx and cur_val.startswith(match_pfx):
                match = True
            elif not match_val and not match_pfx:
                match = True

            if match:
                for uk, uv in updates.items():
                    resolved_uk = _resolve_key(s, uk)
                    s[resolved_uk] = uv
                count_updated += 1

        return modified, f"Updated {count_updated} student record(s)."

    elif action == "sort":
        raw_key = params.get("key", "SGPI")
        key = _resolve_key(first_row, raw_key)
        rev = bool(params.get("reverse", False))
        is_num = bool(params.get("numeric", False))

        if is_num or any(k in key.upper() for k in ["SGPI", "TOTAL", "MARKS", "CREDITS", "CG"]):
            modified.sort(key=lambda s: _safe_float(s.get(key, 0)), reverse=rev)
        else:
            modified.sort(key=lambda s: str(s.get(key, "")).strip().lower(), reverse=rev)

        order_str = "descending" if rev else "ascending"
        return modified, f"Sorted records by {key} ({order_str})."

    return modified, "Dataset unchanged."


def fallback_rule_engine(
    user_prompt: str,
    students: List[Dict[str, Any]]
) -> Optional[Tuple[List[Dict[str, Any]], str, str]]:
    """Fast deterministic regex fallback for common editing commands."""
    p = user_prompt.strip().lower()

    # 1. Remove first N records
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:the\s+)?(?:first|top)\s+(\d+)\s+records?", p)
    if m:
        n = int(m.group(1))
        updated, desc = apply_action("delete_slice", {"start": 0, "count": n}, students)
        return updated, f"Successfully removed the first {n} record(s).", "delete_slice"

    # 2. Remove last N records
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:the\s+)?(?:last|bottom)\s+(\d+)\s+records?", p)
    if m:
        n = int(m.group(1))
        start_idx = max(0, len(students) - n)
        updated, desc = apply_action("delete_slice", {"start": start_idx, "count": n}, students)
        return updated, f"Successfully removed the last {n} record(s).", "delete_slice"

    # 3. Remove single record by seat
    m = re.search(r"\b(?:remove|delete|drop)\s+(?:student\s+|seat\s+)?(\d{6,10})\b", p)
    if m:
        seat = m.group(1)
        updated, desc = apply_action("delete_where", {"key": "Seat_No", "value": seat}, students)
        return updated, f"Removed student record with Seat No {seat}.", "delete_where"

    # 4. Remove failed students
    if any(k in p for k in ["remove fail", "delete fail", "remove failed", "drop fail", "remove unsuccessful"]):
        updated, desc = apply_action("delete_where", {"key": "Result_Status", "values": ["FAIL", "FAILED", "Unsuccessful", "ABS"]}, students)
        return updated, "Removed all failed and absent students.", "delete_where"

    # 5. Keep only passed students
    if any(k in p for k in ["only pass", "only passed", "keep passed", "filter pass"]):
        updated, desc = apply_action("filter_keep", {"key": "Result_Status", "value": "PASS"}, students)
        return updated, "Filtered dataset to keep only passed students.", "filter_keep"

    # 6. Mark 2022 PRN as Diploma
    if "diploma" in p and ("2022" in p or "prn" in p):
        updated, desc = apply_action("update_where", {"match_key": "PRN", "match_prefix": "2022", "updates": {"Candidate_Type": "Diploma"}}, students)
        return updated, "Updated Candidate_Type to 'Diploma' for students with PRN starting with 2022.", "update_where"

    # 7. Sort by SGPI
    if "sort" in p and "sgpi" in p:
        rev = "asc" not in p
        updated, desc = apply_action("sort", {"key": "SGPI", "reverse": rev, "numeric": True}, students)
        return updated, f"Sorted dataset by SGPI {'descending' if rev else 'ascending'}.", "sort"

    # 8. Sort by Total Marks
    if "sort" in p and ("total" in p or "marks" in p):
        rev = "asc" not in p
        updated, desc = apply_action("sort", {"key": "Total_Marks", "reverse": rev, "numeric": True}, students)
        return updated, f"Sorted dataset by Total Marks {'descending' if rev else 'ascending'}.", "sort"

    return None


def process_chat_command(
    user_prompt: str,
    students: List[Dict[str, Any]],
    catalog: Optional[List[Dict[str, Any]]] = None,
    client: Optional[OllamaClient] = None
) -> Dict[str, Any]:
    """
    Process natural-language dataset commands using Ollama (Qwen 2.5) with fallback rule engine.
    Returns: {
        "success": bool,
        "reply": str,
        "action": str,
        "total_students": int,
        "students": list of modified dicts
    }
    """
    if not students:
        return {
            "success": False,
            "reply": "No student records are currently loaded to work on. Please extract or select a PDF first.",
            "action": "none",
            "total_students": 0,
            "students": []
        }

    # First check deterministic rule engine for instantaneous sub-millisecond execution
    rule_res = fallback_rule_engine(user_prompt, students)
    if rule_res is not None:
        updated_data, desc, action_type = rule_res
        return {
            "success": True,
            "reply": desc,
            "action": action_type,
            "total_students": len(updated_data),
            "students": updated_data
        }

    if client is None:
        client = OllamaClient()

    # If Ollama is available, invoke neural LLM with schema constraints
    if client.is_available():
        # Provide sample context
        keys = list(students[0].keys())[:15]
        sample = [
            {"Seat_No": s.get("Seat_No"), "Name": s.get("Student_Name"), "SGPI": s.get("SGPI"), "Status": s.get("Result_Status"), "Type": s.get("Candidate_Type")}
            for s in students[:3]
        ]

        llm_prompt = (
            f"USER REQUEST: \"{user_prompt}\"\n\n"
            f"DATASET INFO:\n"
            f"- Total records: {len(students)}\n"
            f"- Key columns: {keys}\n"
            f"- Sample records (first 3): {json.dumps(sample)}\n\n"
            "Return JSON matching the schema with the exact action and a helpful reply."
        )

        try:
            raw_json = client.generate(
                prompt=llm_prompt,
                system=SYSTEM_PROMPT,
                temperature=0.0,
                format_json=CHAT_JSON_SCHEMA,
                timeout=25
            )
            parsed = json.loads(raw_json)
            action = parsed.get("action", "answer")
            reply = parsed.get("reply", "Processed your request.")
            params = parsed.get("params", {})

            if action == "answer":
                return {
                    "success": True,
                    "reply": reply,
                    "action": "answer",
                    "total_students": len(students),
                    "students": students
                }

            updated_data, detail_msg = apply_action(action, params, students)
            full_reply = f"{reply} ({detail_msg})" if reply else detail_msg
            return {
                "success": True,
                "reply": full_reply,
                "action": action,
                "total_students": len(updated_data),
                "students": updated_data
            }
        except Exception as e:
            pass

    # Generic conversational response if unhandled
    return {
        "success": True,
        "reply": f"I analyzed your request ('{user_prompt}'), but could not determine a specific data modification. You can say: 'remove the first 2 records', 'filter only passed students', 'set candidate type to Diploma for PRN 2022*', or 'sort by SGPI'.",
        "action": "unrecognized",
        "total_students": len(students),
        "students": students
    }
