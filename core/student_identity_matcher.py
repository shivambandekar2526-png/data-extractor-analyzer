"""
Universal AI Student Identity Matching & Entity Resolution Engine
==================================================================
Zero-assumption entity resolution across multi-semester university results.
Accounts for:
- Absent PRNs/ABC IDs in early semesters or internal spreadsheets.
- Seat numbers changing each semester.
- Inverted name tokens (Surname-First vs Firstname-First).
- Omission of patronymics (Father's name) and matronymics (Mother's name).
- Abbreviated initials (e.g. 'V. S. ADULKAR' vs 'VEDANT SANJAY ADULKAR').
- Phonetic transliterations & spelling variations ('DALVI' vs 'DALAVI').
- Bi-directional identity propagation (Union-Find Graph) so identifiers found in
  ANY semester back-fill to all semesters.
- Semester Conflict Guard: Strictly forbids merging records from the same semester.
- Strict Subset & Patronymic Invariance: Prevents false merges on shared surnames/fathers.
- Hybrid AI Entity Resolution: Deterministic fast-path + LLM agent for ambiguous matches.
"""

import os
import re
import json
import logging
import difflib
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

HONORIFIC_PREFIXES = {
    "KU", "KUMAR", "KUMARI", "MR", "MISS", "MRS", "SMT", "SHRI", "SHRIMATI",
    "DR", "MS", "MST", "MAST", "MASTER"
}

CLEAN_CHARS_REGEX = re.compile(r"[^A-Za-z0-9\s]")


def normalize_tokens(text: str) -> List[str]:
    """Tokenize and uppercase clean alphanumeric tokens, discarding academic honorifics."""
    if not text:
        return []
    clean = CLEAN_CHARS_REGEX.sub(" ", str(text)).upper()
    toks = [t for t in clean.split() if len(t) >= 1]
    return [t for t in toks if t not in HONORIFIC_PREFIXES and len(t) >= 2]


def are_tokens_algebraically_matching(toks1: List[str], toks2: List[str]) -> Tuple[bool, float, str]:
    """
    Deterministic Bag-of-Tokens matching.
    Returns (is_match, confidence, reason).
    Enforces strict subset matching to prevent false merges between siblings or common names.
    """
    if not toks1 or not toks2:
        return False, 0.0, "Empty tokens"

    s1, s2 = set(toks1), set(toks2)

    # 1. Exact token set equality (order independent: 'Mokal Yash' == 'Yash Mokal')
    if s1 == s2:
        return True, 1.0, "Exact token set equality"

    # 2. Strict subset match (Father/Mother name included or omitted)
    # The shorter representation must have at least 2 tokens (Surname + Given Name)
    # e.g. {'YASH', 'MOKAL'} in {'YASH', 'KALIDAS', 'MOKAL'}
    # e.g. {'THOSAR', 'MANAS'} in {'THOSAR', 'MANAS', 'MAHESH', 'MUGDHA'}
    if (s1.issubset(s2) or s2.issubset(s1)) and min(len(s1), len(s2)) >= 2:
        shorter = min(len(s1), len(s2))
        return True, 0.95, f"Parental infix / token subset ({shorter} shared tokens)"

    # 3. Non-subset: Check if difference is purely Initials or Phonetic Variations
    inter = s1.intersection(s2)
    if len(inter) >= 1:
        rem1 = [t for t in toks1 if t not in inter]
        rem2 = [t for t in toks2 if t not in inter]

        # Initials Expansion match: e.g. 'V S Adulkar' vs 'Vedant Sanjay Adulkar'
        # Or 'Adulkar V' vs 'Adulkar Vedant'
        if rem1 and rem2:
            initials_match = True
            for r1 in rem1:
                # Check if r1 is an initial of some r2 or matches phonetically
                if len(r1) == 1:
                    if not any(r2.startswith(r1) for r2 in rem2):
                        initials_match = False
                        break
                else:
                    matched_counterpart = False
                    for r2 in rem2:
                        if len(r2) == 1 and r1.startswith(r2):
                            matched_counterpart = True
                            break
                        elif difflib.SequenceMatcher(None, r1, r2).ratio() >= 0.85:
                            matched_counterpart = True
                            break
                    if not matched_counterpart:
                        initials_match = False
                        break

            if initials_match and len(inter) >= 1:
                return True, 0.90, f"Initials / phonetic match with core: {list(inter)}"

        # Phonetic variation on a single remaining token (e.g. 'DALVI' vs 'DALAVI')
        if len(rem1) == 1 and len(rem2) == 1:
            ratio = difflib.SequenceMatcher(None, rem1[0], rem2[0]).ratio()
            if ratio >= 0.84:
                return True, 0.88, f"Phonetic variation ({rem1[0]} ~ {rem2[0]})"

    return False, 0.0, "Insufficient token overlap or distinct given names"


class EntityNode:
    """Represents a student record in a specific semester with all available signals."""
    def __init__(self, node_id: str, semester: int, record: Dict[str, Any]):
        self.node_id = node_id
        self.semester = semester
        self.raw_record = record
        
        # Raw names
        name_val = record.get("Student_Name") or record.get("Student Name") or record.get("name") or record.get("canonical_name") or ""
        self.raw_name = str(name_val).strip()
        self.clean_tokens = normalize_tokens(self.raw_name)
        
        # Clean canonical label
        self.canonical_name = " ".join(self.clean_tokens) if self.clean_tokens else self.raw_name

        # Identifiers (PRN, ABC ID, Seat)
        prn_raw = record.get("PRN") or record.get("prn") or ""
        self.prn = str(prn_raw).strip() if prn_raw and str(prn_raw).strip() not in ("None", "nan", "0", "") else None
        
        abc_raw = record.get("ABC_ID") or record.get("abc_id") or ""
        self.abc_id = str(abc_raw).strip() if abc_raw and str(abc_raw).strip() not in ("None", "nan", "0", "") else None

        seat_raw = record.get("Seat_No") or record.get("Seat No") or record.get("seat_no") or record.get("Sr. No.") or ""
        self.seat_no = str(seat_raw).strip() if seat_raw else ""

        # Category tags (Regular, Diploma, Lateral, etc.)
        ctype_raw = (
            record.get("Candidate_Type") or record.get("Category") or record.get("Entry_Type") or
            record.get("Admission_Type") or record.get("Remark") or ""
        )
        self.candidate_type = str(ctype_raw).strip()
        self.is_explicit_diploma = any(kw in self.candidate_type.lower() for kw in ("diploma", "lateral", "dse", "direct second"))

        # Academic performance signals
        self.sgpi = self._parse_float(record.get("SGPI") or record.get("sgpi") or record.get("GPA") or record.get("CGPI"))
        raw_res = (
            record.get("Result") or record.get("result") or record.get("Result_Status") or
            record.get("result_status") or record.get("Remark") or ""
        )
        self.result = str(raw_res).strip().upper() if raw_res else ""

        kt_raw = record.get("kt_count") or record.get("KT Count") or record.get("kt") or 0
        try:
            self.kt_count = int(kt_raw)
        except Exception:
            self.kt_count = 0

        if "F" in self.result or self.kt_count > 0:
            self.result = "FAIL"
        elif "P" in self.result:
            self.result = "PASS"
        elif self.sgpi is not None and self.sgpi > 0:
            self.result = "PASS"
        elif not self.result:
            self.result = "—"

    @staticmethod
    def _parse_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(str(val).strip())
            return f if f > 0.0 else None
        except Exception:
            return None


class UniversalIdentityGraph:
    """
    Bi-directional Disjoint-Set Graph with Semester Conflict Guard.
    Strictly forbids merging two records that originate from the same examination semester.
    Propagates PRN, ABC ID, and canonical names across all linked semester nodes.
    """
    def __init__(self):
        self.parent: Dict[str, str] = {}
        self.nodes: Dict[str, EntityNode] = {}
        self.clusters: Dict[str, Set[str]] = {}

    def add_node(self, node: EntityNode):
        self.nodes[node.node_id] = node
        self.parent[node.node_id] = node.node_id
        self.clusters[node.node_id] = {node.node_id}

    def find(self, node_id: str) -> str:
        if self.parent[node_id] != node_id:
            self.parent[node_id] = self.find(self.parent[node_id])
        return self.parent[node_id]

    def can_union(self, id1: str, id2: str) -> bool:
        """Semester Conflict Guard: A single student cannot have 2 separate records in the same semester."""
        root1 = self.find(id1)
        root2 = self.find(id2)
        if root1 == root2:
            return True
        sems1 = {self.nodes[nid].semester for nid in self.clusters[root1]}
        sems2 = {self.nodes[nid].semester for nid in self.clusters[root2]}
        if sems1.intersection(sems2):
            return False  # Semester conflict!
        return True

    def union(self, id1: str, id2: str, reason: str = "") -> bool:
        root1 = self.find(id1)
        root2 = self.find(id2)
        if root1 == root2:
            return True

        # Check Semester Conflict Guard
        if not self.can_union(id1, id2):
            logger.debug(f"Blocked merge {id1} <-> {id2} due to semester conflict (both present in same semester).")
            return False

        # Merge root2 into root1
        self.parent[root2] = root1
        self.clusters[root1].update(self.clusters[root2])
        del self.clusters[root2]
        logger.debug(f"Merged {id1} ({self.nodes[id1].raw_name}) <-> {id2} ({self.nodes[id2].raw_name}): {reason}")
        return True

    def get_cluster_nodes(self, root_id: str) -> List[EntityNode]:
        return [self.nodes[nid] for nid in self.clusters.get(root_id, [])]


class AIEntityResolutionAgent:
    """
    LLM-powered Semantic Entity Matcher.
    Invoked strictly on remaining ambiguous or partially matched records.
    """
    def __init__(self):
        self.cloud_client = None
        self.ollama_client = None
        self._init_clients()

    def _init_clients(self):
        try:
            from .cloud_client import CloudClient
            self.cloud_client = CloudClient()
        except Exception:
            self.cloud_client = None

        try:
            from .ollama_client import OllamaClient
            self.ollama_client = OllamaClient()
        except Exception:
            self.ollama_client = None

    def resolve_ambiguous_pairs(
        self,
        candidate_pairs: List[Tuple[EntityNode, EntityNode]]
    ) -> List[Tuple[str, str, float, str]]:
        """
        Evaluate candidate pairs with AI LLM.
        Returns list of (id1, id2, confidence, reason).
        """
        if not candidate_pairs:
            return []

        queries = []
        for n1, n2 in candidate_pairs:
            queries.append({
                "pair_id": f"{n1.node_id}___{n2.node_id}",
                "record_a": {
                    "name": n1.raw_name,
                    "tokens": n1.clean_tokens,
                    "sem": n1.semester
                },
                "record_b": {
                    "name": n2.raw_name,
                    "tokens": n2.clean_tokens,
                    "sem": n2.semester
                }
            })

        prompt = (
            "You are an expert Indian University Registrar resolving student identity across examination gazettes and ledgers.\n"
            "Determine if each record pair represents the SAME individual student across different semesters.\n"
            "Rules to consider:\n"
            "1. Surname-first vs Firstname-first inversion ('Mokal Yash' == 'Yash Mokal').\n"
            "2. Father's name / middle name inserted or omitted ('Yash Mokal' == 'Yash Kalidas Mokal').\n"
            "3. Mother's name appended in Mumbai University results ('Thosar Manas Mahesh Mugdha' == 'Thosar Manas').\n"
            "4. Minor spelling or phonetic transliteration variations ('Dalvi' == 'Dalavi', 'Dubey' == 'Dube', 'Shaikh' == 'Sheikh').\n"
            "5. If they have distinct given names (e.g. 'Sujal' vs 'Devika', or 'Ajinkya' vs 'Rohan'), they are SIBLINGS or different students: is_match MUST BE FALSE.\n\n"
            f"Query Pairs:\n{json.dumps(queries[:30], indent=2)}\n\n"
            "Return valid JSON array of objects:\n"
            "[{\"pair_id\": \"...\", \"is_match\": true|false, \"confidence\": 0.95, \"reason\": \"...\"}]"
        )

        response_text = ""
        # 1. Try Cloud Client
        if self.cloud_client and getattr(self.cloud_client, "is_configured", lambda: False)():
            try:
                response_text = self.cloud_client.generate(prompt)
            except Exception as e:
                logger.warning(f"AI entity matcher cloud call failed: {e}")

        # 2. Try Ollama Client
        if not response_text and self.ollama_client:
            try:
                response_text = self.ollama_client.generate(prompt)
            except Exception as e:
                logger.warning(f"AI entity matcher ollama call failed: {e}")

        if not response_text:
            return []

        # Parse JSON array from response
        matches = []
        try:
            m = re.search(r"\[.*\]", response_text, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                for item in parsed:
                    if item.get("is_match") and float(item.get("confidence", 0.0)) >= 0.85:
                        p_id = item.get("pair_id", "")
                        if "___" in p_id:
                            id1, id2 = p_id.split("___", 1)
                            matches.append((id1, id2, float(item["confidence"]), item.get("reason", "AI Verified")))
        except Exception as e:
            logger.warning(f"Failed to parse AI entity matcher response: {e}")

        return matches


class StudentIdentityMatcher:
    """
    High-level orchestrator for zero-assumption student entity resolution.
    Connects records across all semesters into unified MasterStudent profiles.
    """
    def __init__(self, enable_ai: bool = True):
        self.enable_ai = enable_ai
        self.ai_agent = AIEntityResolutionAgent() if enable_ai else None

    def resolve_all_semesters(
        self,
        semester_data: Dict[int, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """
        Execute universal multi-signal entity matching across all supplied semesters.
        Returns unified master student profiles.
        """
        graph = UniversalIdentityGraph()
        available_sems = sorted(semester_data.keys())

        # Step 1: Register all record nodes into the Identity Graph
        nodes_by_sem: Dict[int, List[EntityNode]] = {}
        for sem in available_sems:
            nodes_by_sem[sem] = []
            raw_list = semester_data[sem]
            for idx, rec in enumerate(raw_list):
                node_id = f"sem_{sem}_idx_{idx}"
                node = EntityNode(node_id, sem, rec)
                graph.add_node(node)
                nodes_by_sem[sem].append(node)

        # Step 2: Global Identifier Linkage (PRN and ABC ID - Tier 1)
        # Any PRN or ABC ID found in ANY semester links records with 100% certainty
        prn_map: Dict[str, str] = {}
        abc_map: Dict[str, str] = {}

        for sem in available_sems:
            for node in nodes_by_sem[sem]:
                if node.prn:
                    if node.prn in prn_map:
                        graph.union(node.node_id, prn_map[node.prn], reason=f"PRN match: {node.prn}")
                    else:
                        prn_map[node.prn] = node.node_id

                if node.abc_id:
                    if node.abc_id in abc_map:
                        graph.union(node.node_id, abc_map[node.abc_id], reason=f"ABC ID match: {node.abc_id}")
                    else:
                        abc_map[node.abc_id] = node.node_id

        # Step 3: Token-Algebra Cross-Semester Matching (Tier 2)
        # For nodes in different semesters that aren't already merged
        candidate_ai_pairs: List[Tuple[EntityNode, EntityNode]] = []

        all_nodes = list(graph.nodes.values())
        for i in range(len(all_nodes)):
            n1 = all_nodes[i]
            for j in range(i + 1, len(all_nodes)):
                n2 = all_nodes[j]
                # Only compare nodes from DIFFERENT semesters that are not yet united
                if n1.semester == n2.semester:
                    continue
                if graph.find(n1.node_id) == graph.find(n2.node_id):
                    continue
                # Skip if already conflict
                if not graph.can_union(n1.node_id, n2.node_id):
                    continue

                # Run token algebra
                matched, conf, reason = are_tokens_algebraically_matching(n1.clean_tokens, n2.clean_tokens)
                if matched and conf >= 0.88:
                    graph.union(n1.node_id, n2.node_id, reason=f"Token Algebra ({reason})")
                elif self.enable_ai:
                    # Check if potential candidate for AI matching (shares surname or high similarity)
                    s1 = set(n1.clean_tokens)
                    s2 = set(n2.clean_tokens)
                    if len(s1.intersection(s2)) >= 1 or difflib.SequenceMatcher(None, n1.canonical_name, n2.canonical_name).ratio() >= 0.75:
                        candidate_ai_pairs.append((n1, n2))

        # Step 4: AI Entity Matcher for remaining ambiguous candidates (Tier 3)
        if self.enable_ai and self.ai_agent and candidate_ai_pairs:
            unresolved = [
                (n1, n2) for (n1, n2) in candidate_ai_pairs
                if graph.find(n1.node_id) != graph.find(n2.node_id) and graph.can_union(n1.node_id, n2.node_id)
            ]
            if unresolved:
                logger.info(f"Invoking AI Entity Resolution on {len(unresolved)} candidate pairs...")
                ai_matches = self.ai_agent.resolve_ambiguous_pairs(unresolved)
                for id1, id2, conf, reason in ai_matches:
                    graph.union(id1, id2, reason=f"AI Entity Match ({reason}, conf={conf})")

        # Step 5: Synthesize Unified Master Student Entities
        master_students: List[Dict[str, Any]] = []
        fe_sems = [s for s in available_sems if s in (1, 2)]
        se_sems = [s for s in available_sems if s in (3, 4)]
        fe_sem = min(fe_sems) if fe_sems else 1
        se_sem = min(se_sems) if se_sems else 3

        for root_id in list(graph.clusters.keys()):
            cluster_nodes = graph.get_cluster_nodes(root_id)
            if not cluster_nodes:
                continue

            # Bi-directionally resolve authoritative attributes
            # 1. Authoritative PRN & ABC ID (found in any semester)
            unified_prn = next((n.prn for n in cluster_nodes if n.prn), None)
            unified_abc = next((n.abc_id for n in cluster_nodes if n.abc_id), None)

            # 2. Canonical Name: Pick the longest tokenized name (most descriptive)
            names_sorted = sorted(cluster_nodes, key=lambda n: len(n.clean_tokens), reverse=True)
            canonical_name = names_sorted[0].raw_name.upper()

            # 3. Explicit Diploma Tag: Check across all records
            is_explicit_diploma = any(n.is_explicit_diploma for n in cluster_nodes)

            # 4. Semesters appeared
            semesters_appeared = sorted(set(n.semester for n in cluster_nodes))
            first_sem = min(semesters_appeared)
            last_sem = max(semesters_appeared)

            # 5. Zero-Assumption Cohort Intake Rule:
            # - If present in First Year (Sem 1 or 2): strictly Regular Entry (N1).
            # - If first appearance is in Second Year (Sem 3/4) or explicitly tagged: strictly Diploma Lateral Entry (N3).
            # - If first appearance is in later years: Transfer / Readmitted.
            if any(s in semesters_appeared for s in fe_sems):
                entry_type = "Regular Entry (Semester 1)"
                entry_category = "Regular Entry (Semester 1)"
                is_diploma = False
            elif (first_sem in se_sems) or (is_explicit_diploma and first_sem <= 4):
                entry_type = "Diploma Student (Lateral Entry)"
                entry_category = "Diploma Student (Lateral Entry)"
                is_diploma = True
            else:
                entry_type = f"Transfer / Lateral (Semester {first_sem})"
                entry_category = "Transfer / Readmitted"
                is_diploma = False

            # 6. Build semester-by-semester records map
            sem_records: Dict[int, str] = {}
            sem_sgpis: Dict[int, Optional[float]] = {}
            sem_seats: Dict[int, str] = {}
            for n in cluster_nodes:
                sem_records[n.semester] = n.result
                sem_sgpis[n.semester] = n.sgpi
                if n.seat_no:
                    sem_seats[n.semester] = n.seat_no

            # 7. Clearance evaluation
            final_sem = max(available_sems)
            final_result = sem_records.get(final_sem, "NOT_APPEARED")
            passed_final = (final_result == "PASS")
            
            # Backlog tracking across all semesters appeared
            had_any_kt = any(res == "FAIL" for res in sem_records.values()) or any(getattr(n, "kt_count", 0) > 0 for n in cluster_nodes)
            passed_zero_kt = passed_final and not had_any_kt

            # Valid SGPI values
            valid_sgpis = [val for val in sem_sgpis.values() if val is not None and val > 0.0]
            avg_sgpi = round(float(sum(valid_sgpis) / len(valid_sgpis)), 2) if valid_sgpis else None

            # Primary student ID for display
            student_id = unified_prn or next((n.seat_no for n in cluster_nodes if n.seat_no), f"ST_{len(master_students)+1:03d}")

            # 8. Rich semester dictionary for downstream analytics compatibility
            sem_dict = {}
            for s in available_sems:
                if s in sem_records:
                    s_info = {
                        "result": sem_records.get(s, "—"),
                        "sgpi": sem_sgpis.get(s),
                        "seat_no": sem_seats.get(s, "")
                    }
                    sem_dict[int(s)] = s_info

            master_students.append({
                "student_id": student_id,
                "canonical_name": canonical_name,
                "name": canonical_name,
                "student_name": canonical_name,
                "all_names": list(set(n.raw_name for n in cluster_nodes)),
                "prn": unified_prn or "",
                "abc_id": unified_abc or "",
                "entry_type": entry_type,
                "entry_category": entry_category,
                "entry_sem": first_sem,
                "first_appeared_sem": first_sem,
                "last_appeared_sem": last_sem,
                "is_diploma": is_diploma,
                "semesters": sem_dict,
                "semesters_appeared": semesters_appeared,
                "semesters_appeared_count": len(semesters_appeared),
                "sem_records": {str(s): sem_records.get(s, "—") for s in available_sems},
                "sem_sgpis": {str(s): sem_sgpis.get(s) for s in available_sems},
                "sem_seats": {str(s): sem_seats.get(s, "") for s in available_sems},
                "final_status": final_result,
                "passed_final": passed_final,
                "zero_kt": passed_zero_kt,
                "passed_without_backlog": passed_zero_kt,
                "had_backlogs": had_any_kt,
                "passed_with_backlogs": passed_final and had_any_kt,
                "average_sgpi": avg_sgpi,
                "avg_sgpi": avg_sgpi,
                "cumulative_avg_sgpi": avg_sgpi
            })

        # Sort master students canonically by Name
        master_students.sort(key=lambda x: x["canonical_name"])
        for idx, st in enumerate(master_students, 1):
            st["sr_no"] = idx

        logger.info(f"StudentIdentityMatcher resolved {len(master_students)} unique master students across {len(available_sems)} semesters.")
        return master_students
