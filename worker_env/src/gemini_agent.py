"""Rate-limited Gemini agent for fit evaluation and application artifacts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import ARTIFACTS_DIR, DATA_DIR, env_float

try:
    import fcntl
except ImportError:  # pragma: no cover - Docker/Linux and macOS both use fcntl
    fcntl = None

_GENERATION_THREAD_LOCK = threading.Lock()


def _json_from_text(value: str) -> dict[str, Any]:
    value = value.strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.MULTILINE).strip()
    match = re.search(r"\{[\s\S]*\}", value)
    if not match:
        raise ValueError("Gemini did not return a JSON object")
    return json.loads(match.group(0))


def _skill_guidance() -> str:
    path = os.getenv("APPLY_SKILL_PATH", "").strip()
    if not path:
        return ""
    skill_path = Path(path).expanduser()
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"
    if not skill_path.exists():
        return ""
    return skill_path.read_text(encoding="utf-8")[:12000]


class SharedGenerationLimiter:
    """Rate-limit material-generation requests across threads and Docker processes.

    The lock/state file lives on the shared data volume, so the web process and the
    scheduler cannot start generation calls at the same time. The timestamp is written
    before each attempt, which guarantees a minimum interval between request starts.
    """

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self.path = DATA_DIR / ".gemini_generation_rate"

    @contextmanager
    def slot(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _GENERATION_THREAD_LOCK:
            with self.path.open("a+", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield handle
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def wait_and_mark(self, handle) -> None:
        handle.seek(0)
        raw = handle.read().strip()
        try:
            last_started = float(raw)
        except ValueError:
            last_started = 0.0
        wait = self.interval_seconds - (time.time() - last_started)
        if wait > 0:
            time.sleep(wait)
        handle.seek(0)
        handle.truncate()
        handle.write(str(time.time()))
        handle.flush()


@contextmanager
def _no_op_context():
    yield None


def _retry_after_seconds(exc: Exception) -> float:
    """Extract Google's retry hint when available, with a safe fallback."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                pass
    message = str(exc)
    match = re.search(r"retry(?:Delay|[- ]after)[^0-9]*(\d+(?:\.\d+)?)\s*s?", message, re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


class GeminiApplicationAgent:
    def __init__(self) -> None:
        # LANGCHAIN_API_KEY is accepted only as a migration fallback for the old project.
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        # GEMINI_DELAY_SECONDS remains a migration alias for the general evaluator.
        self.delay = env_float("GEMINI_REQUEST_DELAY_SECONDS", env_float("GEMINI_DELAY_SECONDS", 8.0))
        self.generation_delay = env_float("GEMINI_GENERATION_DELAY_SECONDS", 30.0)
        self.generation_limiter = SharedGenerationLimiter(self.generation_delay)
        self.max_retries = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
        self._last_call = 0.0
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured")
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _request_once(self, prompt: str) -> dict[str, Any]:
        from google.genai import types
        response = self._get_client().models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        self._last_call = time.monotonic()
        return _json_from_text(response.text or "")

    def _generate_json(self, prompt: str, request_kind: str = "evaluation") -> dict[str, Any]:
        wait = max(0.0, self.delay - (time.monotonic() - self._last_call))
        if wait:
            time.sleep(wait)
        last_error: Exception | None = None
        limiter_context = self.generation_limiter.slot() if request_kind == "generation" else None
        with (limiter_context if limiter_context is not None else _no_op_context()) as generation_handle:
            for attempt in range(self.max_retries + 1):
                try:
                    if request_kind == "generation":
                        # The file lock is held for the request and any retries.
                        # Each retry gets its own full interval as well.
                        self.generation_limiter.wait_and_mark(generation_handle)
                    return self._request_once(prompt)
                except Exception as exc:  # API errors vary by google-genai version
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    retry_wait = max(2 ** attempt, _retry_after_seconds(exc))
                    time.sleep(min(300.0, retry_wait))
        raise RuntimeError(f"Gemini request failed: {last_error}")

    def evaluate(self, resume_text: str, job: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""
You are a careful job-fit reviewer. Score this candidate against this job on a 0-10 scale.
Return JSON only with: fit_score (number 0-10), explanation (2 concise sentences),
strengths (array of strings), gaps (array of strings), and evidence (array of strings).
Use only facts present in the resume. Do not penalize the candidate for information that is
not required by the job, and do not invent experience.

RESUME:
{resume_text[:18000]}

JOB:
{json.dumps(job, ensure_ascii=False)[:22000]}
"""
        result = self._generate_json(prompt, request_kind="evaluation")
        try:
            result["fit_score"] = max(0.0, min(10.0, float(result.get("fit_score", 0))))
        except (TypeError, ValueError):
            result["fit_score"] = 0.0
        return result

    def generate_application(self, resume_text: str, job: dict[str, Any], job_id: int) -> dict[str, str]:
        skill = _skill_guidance()
        prompt = f"""
Act as a resume and cover-letter agent following these constraints:
- Produce ATS-friendly LaTeX resume and LaTeX cover letter.
- Use only facts in the source resume; never invent metrics, employers, dates, skills or degrees.
- Tailor wording to the job description while preserving truth.
- Return JSON only with exactly two keys: resume_tex and cover_letter_tex.
- Each value must be complete compilable LaTeX source and must not be wrapped in Markdown fences.
- Keep the resume concise and one to two pages. Use ordinary LaTeX packages only.

The external apply skill guidance, if present, is:
{skill[:12000]}

SOURCE RESUME:
{resume_text[:20000]}

JOB:
{json.dumps(job, ensure_ascii=False)[:24000]}
"""
        result = self._generate_json(prompt, request_kind="generation")
        return {
            "resume_tex": str(result.get("resume_tex", "")),
            "cover_letter_tex": str(result.get("cover_letter_tex", "")),
        }


def compile_latex(tex_path: Path) -> Path | None:
    """Compile when pdflatex is installed; generated .tex remains the source of truth."""
    if not os.getenv("COMPILE_LATEX", "false").lower() in {"1", "true", "yes", "on"}:
        return None
    try:
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name], cwd=tex_path.parent, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return None
    pdf = tex_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def write_application_artifacts(job_id: int, generated: dict[str, str]) -> dict[str, str | None]:
    target = ARTIFACTS_DIR / str(job_id)
    target.mkdir(parents=True, exist_ok=True)
    resume_tex = target / "resume.tex"
    cover_tex = target / "cover_letter.tex"
    resume_tex.write_text(generated.get("resume_tex", ""), encoding="utf-8")
    cover_tex.write_text(generated.get("cover_letter_tex", ""), encoding="utf-8")
    pdf = compile_latex(resume_tex)
    return {
        "resume_tex_path": str(resume_tex),
        "cover_letter_tex_path": str(cover_tex),
        "resume_pdf_path": str(pdf) if pdf else None,
    }
