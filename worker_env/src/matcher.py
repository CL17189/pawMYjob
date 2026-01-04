# worker_env/src/matcher.py
from sentence_transformers import SentenceTransformer, util
import numpy as np
import os, json, re
from typing import List, Dict, Any
from datetime import datetime

# optional LangChain LLM usage
USE_LLM = bool(os.getenv("OPENAI_API_KEY", "") or os.getenv("LLM_PROVIDER", ""))

# load embedding model (singleton)
_EMB_MODEL = None
def get_emb_model(name="all-MiniLM-L6-v2"):
    global _EMB_MODEL
    if _EMB_MODEL is None:
        _EMB_MODEL = SentenceTransformer(name)
    return _EMB_MODEL

def embed_similarity(text_a: str, text_b: str) -> float:
    model = get_emb_model()
    a = model.encode(text_a, convert_to_tensor=True)
    b = model.encode(text_b, convert_to_tensor=True)
    sim = util.cos_sim(a, b).item()
    # clamp to [0,1]
    sim = max(min(sim, 1.0), -1.0)
    # convert from [-1,1] -> [0,1] if needed; but cos sim for sentence-transformers typically [0,1]
    return float((sim + 1) / 2) if sim < 0 else float(sim)

# ----- LLM scoring helper (LangChain/OpenAI) -----
def call_llm_evaluate(job_title: str, job_text: str, profile_text: str, skills: List[str]) -> Dict[str, Any]:
    """
    调用 LLM 要求返回 JSON：
    {"confidence": 0-100, "label": "must apply|recommended|can apply|general", "explanation": "..."}
    如果未配置 LLM（环境变量缺失），此函数会抛出 RuntimeError，调用者应处理回退策略。
    """
    if not USE_LLM:
        raise RuntimeError("LLM not configured (OPENAI_API_KEY missing).")

    # lazy import langchain to avoid hard dependency when disabled
    try:
        from langchain import OpenAI
        from langchain.prompts import PromptTemplate
    except Exception as e:
        # fallback: try older import path
        try:
            from langchain.llms import OpenAI
            from langchain import PromptTemplate
        except Exception:
            raise RuntimeError("langchain/OpenAI unavailable in environment.") from e

    # build prompt - concise and deterministic, ask for strict JSON
    prompt_template = """
You are an assistant that evaluates how well a candidate matches a job posting.
Return a single JSON object with EXACT keys: confidence (number 0-100), label (one of "must apply", "recommended", "can apply", "general"), explanation (short, 1-3 sentences).

Job Title:
{job_title}

Job Description:
{job_text}

Candidate Profile (short):
{profile_text}

Candidate explicit skill list (comma-separated):
{skills}

Rules:
- Confidence should reflect match strength: 0 (no fit) to 100 (near perfect fit).
- Consider both skills and experience; be precise but concise.
- Do not output anything except the valid JSON object.

Return strictly JSON.
"""
    prompt = PromptTemplate.from_template(prompt_template)
    llm = OpenAI(temperature=0, model="gpt-4o-mini" if os.getenv("OPENAI_API_KEY") else None)
    formatted = prompt.format(job_title=job_title, job_text=job_text[:3000], profile_text=profile_text[:2000], skills=", ".join(skills))
    resp = llm(formatted)
    # attempt to parse JSON in response
    import json, re
    m = re.search(r'\{.*\}', resp, flags=re.S)
    if not m:
        # try direct parse
        try:
            return json.loads(resp)
        except Exception:
            raise RuntimeError("LLM returned non-JSON output and couldn't be parsed.")
    try:
        return json.loads(m.group(0))
    except Exception as e:
        raise RuntimeError("Failed to parse JSON from LLM response: " + str(e))

# ----- main matching function -----
def score_job(job: Dict, profile_raw: str, profile_skills: List[str]) -> Dict:
    """
    job: {title, raw, url, country, ...}
    profile_raw: resume raw text
    profile_skills: list of skills extracted from resume
    returns job dict with added fields:
      - embed_score (0-1)
      - llm (dict) if available: {confidence, label, explanation}
      - final_score (0-100)
      - category (label)
    """
    title = job.get("title") or ""
    raw_text = ""
    if isinstance(job.get("raw"), dict):
        raw_text = job["raw"].get("markdown") or job["raw"].get("text") or str(job.get("raw"))
    else:
        raw_text = str(job.get("raw", ""))

    combined = " ".join([title, raw_text])[:5000]
    emb = embed_similarity(combined, profile_raw)
    emb_score_100 = float(emb * 100)

    llm_result = None
    if USE_LLM:
        try:
            llm_result = call_llm_evaluate(title, combined, profile_raw, profile_skills)
            # normalize confidence to number 0-100
            conf = float(llm_result.get("confidence", 0))
            conf = max(0.0, min(100.0, conf))
            llm_result["confidence"] = conf
        except Exception as e:
            # LLM failed -> fallback
            llm_result = {"confidence": None, "label": None, "explanation": f"LLM error: {str(e)}"}

    # Combine: prefer LLM confidence if available; otherwise use embedding heuristic
    if llm_result and llm_result.get("confidence") is not None:
        # mix LLM + embedding: 70% LLM, 30% embedding
        final = 0.7 * llm_result["confidence"] + 0.3 * emb_score_100
        label = llm_result.get("label") or _label_from_score(final)
        explanation = llm_result.get("explanation", "")
    else:
        # no LLM -> use embedding thresholds and skills-matching heuristic
        # compute skill hit ratio
        job_text_lower = combined.lower()
        hits = sum(1 for s in profile_skills if s.lower() in job_text_lower)
        skill_ratio = hits / (len(profile_skills) or 1)
        # final (0-100) combine embedding and skill ratio
        final = 0.7 * emb_score_100 + 30 * skill_ratio
        label = _label_from_score(final)
        explanation = f"Embed sim {emb_score_100:.1f}; skill hits {hits}/{len(profile_skills) or 0}."

    # clamp and set fields
    final = max(0.0, min(100.0, final))
    job_out = job.copy()
    job_out.update({
        "embed_score": round(emb, 4),
        "final_score": round(final, 2),
        "category": label,
        "llm": llm_result,
        "explanation": explanation,
        "evaluated_at": datetime.utcnow().isoformat() + "Z"
    })
    return job_out

def _label_from_score(score: float) -> str:
    if score >= 85:
        return "must apply"
    if score >= 70:
        return "recommended"
    if score >= 50:
        return "can apply"
    return "general"

def match_all(jobs: Dict[str, List[Dict]], profile: Dict) -> Dict[str, List[Dict]]:
    """
    jobs: {country: [jobs]}
    profile: output of parse_resume_md() -> includes raw & skills
    """
    out = {}
    raw = profile.get("raw", "")
    skills = profile.get("skills", [])
    for country, jlist in jobs.items():
        out[country] = []
        for j in jlist:
            try:
                scored = score_job(j, raw, skills)
            except Exception as e:
                # make sure one failure doesn't stop whole run
                scored = j.copy()
                scored.update({"error": str(e)})
            out[country].append(scored)
    return out
