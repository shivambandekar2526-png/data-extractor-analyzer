"""
AI Executive Insights & Narrative Synthesizer
=============================================
Synthesizes executive academic observations, HOD briefs, and NAAC/NBA recommendations
from compact pre-computed EDA statistical summaries.

Token Efficiency:
- Never receives raw rows (saving 20,000+ tokens).
- Transmits only ~150 tokens of computed metrics.
- Provides automatic rule-based fallback if offline.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("AIInsights")


class AIInsightsSynthesizer:
    """Generates qualitative executive insights from compact numerical summaries."""

    def __init__(self, ollama_client=None, cloud_client=None):
        self.ollama = ollama_client
        self.cloud = cloud_client

    def generate_narrative(self, eda_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate academic narrative report from computed EDA metrics.
        Returns a dictionary with structured sections and token metadata.
        """
        overview = eda_data.get("overview", {})
        toughest = eda_data.get("toughest_subject") or {}
        top_sub = eda_data.get("top_scoring_subject") or {}
        demographics = eda_data.get("demographics", {})
        cand = demographics.get("candidate_type", {})
        gender = demographics.get("gender", {})

        # Build compact payload (~150 tokens)
        compact_metrics = {
            "total_students": overview.get("total_students", 0),
            "pass_rate_pct": overview.get("pass_percentage", 0.0),
            "avg_sgpi": overview.get("avg_sgpi", 0.0),
            "distinction_count": overview.get("distinction_count", 0),
            "distinction_pct": round(
                (overview.get("distinction_count", 0) / max(1, overview.get("total_students", 1))) * 100, 1
            ),
            "atkt_fail_count": overview.get("atkt_fail_count", 0),
            "toughest_course": {
                "name": toughest.get("name", "N/A"),
                "pass_pct": toughest.get("pass_pct", 0.0)
            },
            "top_course": {
                "name": top_sub.get("name", "N/A"),
                "avg_marks": top_sub.get("avg_marks", 0.0)
            },
            "demographics": {
                "regular_pass_pct": cand.get("regular", {}).get("pass_pct", 0.0),
                "diploma_pass_pct": cand.get("diploma", {}).get("pass_pct", 0.0),
                "male_pass_pct": gender.get("male", {}).get("pass_pct", 0.0),
                "female_pass_pct": gender.get("female", {}).get("pass_pct", 0.0)
            }
        }

        # Attempt AI generation
        prompt = (
            "You are a Senior Academic Auditor and Department Dean. Analyze these semester result metrics:\n"
            f"{json.dumps(compact_metrics, indent=2)}\n\n"
            "Produce an executive analytical briefing with these 4 sections (use markdown):\n"
            "### 1. Executive Summary\n"
            "2-3 sentences summarizing overall cohort health.\n\n"
            "### 2. Academic Strengths\n"
            "Key positive indicators and strong performance areas.\n\n"
            "### 3. Critical Vulnerabilities & Bottlenecks\n"
            "Subjects, candidate segments, or grade distributions that require urgent attention.\n\n"
            "### 4. NAAC / NBA Accreditation & Remedial Action Plan\n"
            "3 specific, actionable recommendations for faculty and departmental mentors."
        )

        tokens_est = len(prompt.split()) + 250
        ai_response = ""
        engine_used = "offline_fallback"

        # Try Cloud Client first if configured
        if self.cloud and self.cloud.is_configured():
            try:
                ai_response = self.cloud.generate(
                    prompt=prompt,
                    system="You are an expert university academic auditor. Deliver concise, professional evaluations.",
                    temperature=0.3
                )
                if ai_response:
                    model_name = self.cloud.config.get("model") or "Cloud AI"
                    engine_used = f"Cloud AI ({model_name})"
            except Exception as e:
                logger.warning(f"Cloud AI narrative generation failed: {e}")

        # Fallback to local Ollama
        if not ai_response and self.ollama and self.ollama.is_available():
            try:
                ai_response = self.ollama.generate(
                    prompt=prompt,
                    system="You are an expert university academic auditor. Deliver concise, professional evaluations.",
                    temperature=0.3
                )
                if ai_response:
                    engine_used = f"Local Ollama ({self.ollama.model})"
            except Exception as e:
                logger.warning(f"Ollama narrative generation failed: {e}")

        # If still no AI response, use smart deterministic template
        if not ai_response:
            ai_response = self._build_deterministic_narrative(compact_metrics)
            engine_used = "Deterministic Academic Rule Engine (Offline)"
            tokens_est = 0
        else:
            ai_response = self._normalize_to_markdown(ai_response)

        return {
            "narrative_markdown": ai_response,
            "engine_used": engine_used,
            "tokens_consumed": tokens_est,
            "summary_metrics": compact_metrics
        }

    def _normalize_to_markdown(self, raw: str) -> str:
        """Convert LLM JSON output or raw text into formatted Markdown."""
        s = raw.strip()
        if not s.startswith("{"):
            return s
        try:
            data = json.loads(s)
            md_parts = []
            title_map = {
                "executive_summary": "### 1. Executive Summary",
                "academic_strengths": "### 2. Academic Strengths",
                "critical_vulnerabilities": "### 3. Critical Vulnerabilities & Bottlenecks",
                "recommendations": "### 4. NAAC / NBA Accreditation & Remedial Action Plan",
                "action_plan": "### 4. NAAC / NBA Accreditation & Remedial Action Plan"
            }
            # Handle dictionary of sections or nested structures
            for k, v in data.items():
                clean_k = k.lower().replace(" ", "_")
                header = title_map.get(clean_k, f"### {k.replace('_', ' ').title()}")
                md_parts.append(header)
                if isinstance(v, list):
                    for item in v:
                        md_parts.append(f"- {item}")
                elif isinstance(v, dict):
                    for subk, subv in v.items():
                        md_parts.append(f"- **{subk}**: {subv}")
                else:
                    md_parts.append(str(v).strip())
                md_parts.append("")
            return "\n".join(md_parts).strip()
        except Exception:
            return s

    def _build_deterministic_narrative(self, metrics: Dict[str, Any]) -> str:
        """Deterministic high-quality fallback narrative when AI is unreachable."""
        total = metrics["total_students"]
        pass_pct = metrics["pass_rate_pct"]
        avg_sgpi = metrics["avg_sgpi"]
        dist_count = metrics["distinction_count"]
        dist_pct = metrics["distinction_pct"]
        fail_count = metrics["atkt_fail_count"]
        toughest = metrics["toughest_course"]
        top = metrics["top_course"]
        demo = metrics["demographics"]

        lines = [
            "### 1. Executive Summary",
            f"The cohort comprising **{total} students** achieved an overall pass rate of **{pass_pct}%** with a mean SGPI of **{avg_sgpi}**. "
            f"A notable **{dist_pct}%** ({dist_count} students) secured First Class with Distinction, while {fail_count} students require academic remediation under KT/ATKT provisions.",
            "",
            "### 2. Academic Strengths",
            f"- **High Achievers**: {dist_count} students demonstrated exceptional performance with SGPI ≥ 7.50.",
            f"- **Peak Course Performance**: **{top['name']}** registered the highest scoring engagement with an average score of {top['avg_marks']}.",
            f"- **Demographic Engagement**: Female students achieved a pass rate of {demo['female_pass_pct']}%, compared to {demo['male_pass_pct']}% for male students.",
            "",
            "### 3. Critical Vulnerabilities & Bottlenecks",
            f"- **Subject Bottleneck**: **{toughest['name']}** represents the critical hurdle for this semester, with a pass rate of only **{toughest['pass_pct']}%**.",
            f"- **Cohort Disparity**: Lateral entry diploma students registered a pass rate of {demo['diploma_pass_pct']}%, requiring bridge course alignment with regular students ({demo['regular_pass_pct']}%).",
            "",
            "### 4. NAAC / NBA Accreditation & Remedial Action Plan",
            f"1. **Remedial Teaching**: Institute 15 hours of targeted tutorial sessions for students who failed **{toughest['name']}** before the university re-examination.",
            "2. **Mentorship & Slow Learner Tracking**: Faculty mentors must schedule 1-on-1 performance reviews for the KT students to align with NAAC Criterion 2.2.1.",
            "3. **Peer Learning Groups**: Pair high-performing distinction students with lateral-entry students for difficult analytical theory subjects."
        ]
        return "\n".join(lines)
