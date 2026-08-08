"""Deterministic screening tags and the existing skill-oriented language score."""

from __future__ import annotations

import re
from typing import Any


def _text(job: dict[str, Any]) -> str:
    return " ".join(str(job.get(key) or "") for key in ("title", "description", "meta", "location")).lower()


SWEDISH_REQUIRED = (
    r"\b(swedish|svenska)\b.{0,40}\b(required|mandatory|must|fluent|native|excellent|krav|obligatorisk|flytande)\b",
    r"\b(required|mandatory|must|fluent|native|excellent|krav|obligatorisk|flytande)\b.{0,40}\b(swedish|svenska)\b",
    r"\b(swedish|svenska)\b.{0,50}\b(language|speaking|språk|skills)\b.{0,40}\b(required|mandatory|must|fluent|native|krav|obligatorisk|flytande)\b",
)
SWEDISH_OPTIONAL = (r"(swedish|svenska).{0,80}(plus|nice to have|advantage|merit|fördel|önskvärt)",)
SECURITY_OR_CITIZENSHIP = (
    r"\b(citizenship|citizen|medborgarskap|svenskt medborgarskap|eu citizen|eu citizenship)\b",
    r"\b(security clearance|security cleared|säkerhetsklass|säkerhetsprövning|classified|defence|försvar)\b",
    r"\bsecurity screening\b.{0,40}\b(required|must|mandatory)\b",
)
SENIOR = r"\b(senior|lead|principal|staff|head of|manager|architect)\b"


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)


def classify_job(job: dict[str, Any]) -> dict[str, Any]:
    text = _text(job)
    language_optional = _matches(SWEDISH_OPTIONAL, text)
    explicit_language_phrase = bool(re.search(
        r"\b(swedish|svenska)\b(?:\s+(?:language|speaking|språk|skills|proficiency))?\s*(?:is|are|:)?\s*(required|mandatory|must|krav|obligatorisk|flytande)\b"
        r"|\b(fluent|native|excellent)\s+(?:in\s+)?(swedish|svenska)\b",
        text,
        flags=re.IGNORECASE,
    ))
    language_required = explicit_language_phrase or (_matches(SWEDISH_REQUIRED, text) and not language_optional)
    language_optional = language_optional and not language_required
    security_negative = bool(re.search(r"(no|without|not).{0,30}(citizenship|security clearance|säkerhetsklass)", text)) or bool(re.search(r"(citizenship|security clearance|säkerhetsklass).{0,30}(not required|not needed|not necessary|optional)", text))
    security = _matches(SECURITY_OR_CITIZENSHIP, text) and not security_negative
    title = str(job.get("title") or job.get("job_title") or "")
    senior = bool(re.search(SENIOR, title, flags=re.IGNORECASE)) or bool(re.search(r"\b(senior|lead|principal|staff)\s+(data|software|analytics|platform|machine learning|cloud)\b", text, flags=re.IGNORECASE))
    tags = []
    if language_required:
        tags.append("Swedish required")
    elif language_optional:
        tags.append("Swedish optional")
    if senior:
        tags.append("Senior")
    if security:
        tags.append("Citizenship/security gate")
    reason = "Excluded: citizenship/security requirement" if security else "Eligible for language and fit scoring"
    return {
        "language_required": int(language_required),
        "language_optional": int(language_optional),
        "citizenship_or_security_required": int(security),
        "senior": int(senior),
        "tags_json": tags,
        "screening_reason": reason,
        "stage": "all" if security else "review",
    }


def score_language_fit(job: dict[str, Any], resume_text: str) -> tuple[float, str]:
    """Score 0-10; this keeps the old keyword-based matching idea but makes it explicit."""
    text = _text(job)
    profile = resume_text.lower()
    has_swedish = bool(re.search(r"\b(swedish|svenska|svenska språket)\b", profile))
    has_english = bool(re.search(r"\b(english|engelska)\b", profile))
    if re.search(r"\b(swedish|svenska)\b", text) and job.get("language_required"):
        return (10.0, "Resume explicitly mentions Swedish") if has_swedish else (2.0, "Swedish appears required but is not evidenced in the resume")
    if re.search(r"\b(swedish|svenska)\b", text):
        return (8.0, "Swedish is mentioned but not detected as a hard gate") if has_swedish else (6.0, "Swedish appears optional; English-language profile remains viable")
    return (8.0 if has_english else 6.0, "English is present in the resume" if has_english else "No explicit language gate detected")


def score_skill_fit(job: dict[str, Any], resume_text: str, profile_skills: list[str] | None = None) -> tuple[float, str]:
    """Reuse the project's explicit-skills idea without loading the heavyweight embedding model."""
    profile = resume_text.lower()
    text = _text(job)
    skills = [s.lower().strip() for s in (profile_skills or []) if s.strip()]
    if not skills:
        vocabulary = ("python", "sql", "spark", "airflow", "dbt", "kafka", "aws", "azure", "gcp", "docker", "kubernetes", "java", "scala", "snowflake", "databricks", "tableau", "power bi")
        skills = [skill for skill in vocabulary if re.search(rf"\b{re.escape(skill)}\b", profile)]
    if not skills:
        return (5.0, "No explicit technical skills were detected")
    hits = [skill for skill in skills if re.search(rf"\b{re.escape(skill)}\b", text)]
    score = round(min(10.0, 10.0 * len(hits) / len(skills)), 2)
    return score, f"{len(hits)}/{len(skills)} resume skills appear in the job description"
