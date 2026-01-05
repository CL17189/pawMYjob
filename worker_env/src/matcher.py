# worker_env/src/matcher.py
from sentence_transformers import SentenceTransformer, util
import os, json, re
from typing import List, Dict, Any
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parents[2]
dotenv_path = ROOT_DIR / ".env"
load_dotenv(dotenv_path)

# NOTE: your existing LLM flag (LANGCHAIN_API_KEY) is reused as a feature flag.
# For Gemini you will typically set GOOGLE_API_KEY or GEMINI_API_KEY / GOOGLE_GENAI_USE_VERTEXAI etc.
USE_LLM = bool(os.getenv("LANGCHAIN_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
if USE_LLM: os.environ["GOOGLE_API_KEY"] = os.getenv("LANGCHAIN_API_KEY")

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
    # clamp to [-1,1]
    sim = max(min(sim, 1.0), -1.0)
    # convert from [-1,1] -> [0,1] if needed
    return float((sim + 1) / 2) if sim < 0 else float(sim)


# ----- LLM scoring helper (LangChain / Google Gemini via langchain-google-genai) -----
def call_llm_evaluate(job_title: str, job_text: str, profile_text: str, skills: List[str],
                      model_name: str = "gemini-3-flash-preview",
                      temperature: float = 0.0,
                      max_job_tokens: int = 4000,
                      max_profile_tokens: int = 2000) -> Dict[str, Any]:
    """
    调用 Gemini（通过 langchain-google-genai -> ChatGoogleGenerativeAI）。
    返回结构应为：
      {"confidence": 0-100, "label": "must apply|recommended|can apply|general", "explanation": "..."}
    注意：此函数假定环境中已设置 GOOGLE_API_KEY 或 GEMINI_API_KEY（或其他 LangChain 要求的凭证）。
    """

    if not USE_LLM:
        raise RuntimeError("LLM not configured (missing GOOGLE_API_KEY/GEMINI_API_KEY/LANGCHAIN_API_KEY).")

    # lazy import to avoid hard dependency when LLM disabled
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except Exception as e:
        raise RuntimeError("langchain-google-genai (ChatGoogleGenerativeAI) is not available; please install it.") from e

    # truncate heuristics (keep ends, avoid mid-sentence cutoff ideally)
    def _truncate(s: str, max_chars: int) -> str:
        if len(s) <= max_chars:
            return s
        # keep first 75% and last 25% to preserve summary + recent items
        keep_front = int(max_chars * 0.75)
        keep_back = max_chars - keep_front
        return s[:keep_front].rsplit("\n", 1)[0] + "\n...\n" + s[-keep_back:].lstrip()

    jt = _truncate(job_text, max_job_tokens)
    pt = _truncate(profile_text, max_profile_tokens)

    # Build a concise system + user prompt asking STRICT JSON output.
    system_prompt = (
        "You are a senior technical recruiter. Evaluate how well the candidate matches the job. "
        "Be concise, factual, and return EXACTLY one JSON object (no surrounding text) with keys: "
        "\"confidence\" (0-100), \"label\" (one of \"must apply\",\"recommended\",\"can apply\",\"general\"), "
        "\"explanation\" (1-3 short sentences)."
    )

    user_prompt = (
        f"Job Title:\n{job_title}\n\n"
        f"Job Description (trimmed):\n{jt}\n\n"
        f"Candidate Profile (trimmed):\n{pt}\n\n"
        f"Candidate explicit skills: {', '.join(skills) if skills else '(none)'}\n\n"
        "Rules:\n"
        "- Confidence must be a number 0-100 representing match strength.\n"
        "- Label must be one of the four specified values and reflect how strongly you'd recommend application.\n"
        "- Consider explicit skills, and whether skills are shown in experience/project bullets.\n"
        "- Output ONLY the JSON object. No commentary, no code block.\n"
    )

    # instantiate Gemini LLM
    llm = ChatGoogleGenerativeAI(model=model_name, temperature=temperature)

    # invoke: using tuple format (system/human) compatible with LangChain docs
    try:
        resp = llm.invoke([("system", system_prompt), ("human", user_prompt)])
    except Exception as e:
        raise RuntimeError(f"LLM invocation failed: {e}")

    # resp may be an AIMessage-like object; obtain textual content robustly
    resp_text = None
    # Try a few ways to extract text depending on returned type
    if isinstance(resp, str):
        resp_text = resp
    else:
        # object-like response (LangChain AIMessage)
        try:
            # many LangChain wrappers put text in resp.content or resp.ai_message.content
            if hasattr(resp, "content"):
                # content can be list/dict or text; try to normalize
                c = resp.content
                if isinstance(c, str):
                    resp_text = c
                elif isinstance(c, list):
                    # list of dicts with 'text'
                    collected = []
                    for block in c:
                        if isinstance(block, dict) and block.get("text"):
                            collected.append(block.get("text"))
                    resp_text = "\n".join(collected) if collected else None
            if resp_text is None and hasattr(resp, "text"):
                resp_text = getattr(resp, "text")
            if resp_text is None and hasattr(resp, "content_blocks"):
                # some wrappers offer content_blocks
                blocks = getattr(resp, "content_blocks")
                if isinstance(blocks, list):
                    texts = []
                    for b in blocks:
                        if isinstance(b, dict) and "text" in b:
                            texts.append(b["text"])
                    resp_text = "\n".join(texts) if texts else None
        except Exception:
            resp_text = None

    if not resp_text:
        # fallback to string conversion
        resp_text = str(resp)

    # extract JSON object
    m = re.search(r'\{[\s\S]*\}', resp_text)
    if not m:
        # try direct load
        try:
            return json.loads(resp_text)
        except Exception:
            raise RuntimeError("LLM returned non-JSON output and couldn't be parsed. Raw: " + resp_text[:1000])

    try:
        parsed = json.loads(m.group(0))
    except Exception as e:
        raise RuntimeError("Failed to parse JSON from LLM response: " + str(e) + "\nRaw: " + resp_text[:1000])

    # normalise confidence if present
    if "confidence" in parsed:
        try:
            conf = float(parsed.get("confidence") or 0.0)
            parsed["confidence"] = max(0.0, min(100.0, conf))
        except Exception:
            parsed["confidence"] = 0.0

    return parsed


# ----- main scoring function -----
def score_job(job: Dict[str, Any], profile_raw: str, profile_skills: List[str]) -> Dict[str, Any]:
    """
    Score a single job dict. This function:
      - constructs a combined job text from title + company + description + workplace_type + employment_type
      - computes embedding similarity between combined job text and profile_raw
      - does skill-hit counting with word-boundary matching and also checks if skill is present in experience/project sections if provided
      - optionally calls LLM (call_llm_evaluate) to get structured judgment
      - returns job copy with added fields: embed_score, final_score, category, llm, explanation, evaluated_at
    """
    title = job.get("title") or job.get("job_title") or ""
    company = job.get("company_name") or job.get("company") or ""
    desc = job.get("description") or job.get("raw") or job.get("text") or ""
    workplace = job.get("workplace_type") or job.get("location") or ""
    emp_type = job.get("employment_type") or ""

    # When job['raw'] is a dict (some scrapers put multiple fields), try to pick best textual fields
    if isinstance(desc, dict):
        desc = desc.get("markdown") or desc.get("text") or " ".join([str(v) for v in desc.values() if isinstance(v, str)])

    parts = [str(x).strip() for x in (title, company, desc, workplace, emp_type) if x]
    combined = " \n".join(parts)[:8000]  # truncate to reasonable length for embeddings/LLM

    # embedding similarity
    emb = embed_similarity(combined, profile_raw)
    emb_score_100 = float(emb * 100)

    # skill matching - word-boundary matching (case-insensitive)
    job_text_lower = combined.lower()
    # normalize skills: strip and lowercase
    normalized_skills = [s.strip().lower() for s in profile_skills if s and s.strip()]
    # exact token match using regex word boundaries or punctuation separation
    skill_hits = 0
    skill_details = {}
    for s in normalized_skills:
        if not s:
            continue
        # escape regex special chars in skill token
        token = re.escape(s)
        # allow multi-word tokens; require word boundaries at ends
        if re.search(rf'\b{token}\b', job_text_lower):
            skill_hits += 1
            skill_details[s] = skill_details.get(s, 0) + 1

    skill_ratio = skill_hits / (len(normalized_skills) or 1)

    llm_result = None
    if USE_LLM:
        try:
            # call Gemini LLM for fine-grained judgement; keep inputs trimmed in call_llm_evaluate
            llm_result = call_llm_evaluate(title, combined, profile_raw, normalized_skills)
            # normalise confidence already done in call_llm_evaluate
            conf_val = llm_result.get("confidence")
            if conf_val is None:
                llm_result["confidence"] = None
        except Exception as e:
            llm_result = {"confidence": None, "label": None, "explanation": f"LLM error: {str(e)}"}

    # combine scores
    if llm_result and llm_result.get("confidence") is not None:
        final = 0.7 * float(llm_result["confidence"]) + 0.3 * emb_score_100
        label = llm_result.get("label") or _label_from_score(final)
        explanation = llm_result.get("explanation", "")
    else:
        # fallback heuristic: embed + skill ratio with heavier weight to embedding
        final = 0.7 * emb_score_100 + 30.0 * skill_ratio
        label = _label_from_score(final)
        explanation = f"Embed sim {emb_score_100:.1f}; skill hits {skill_hits}/{len(normalized_skills) or 0}."

    final = max(0.0, min(100.0, final))

    job_out = job.copy()
    job_out.update({
        "embed_score": round(emb, 4),
        "final_score": round(final, 2),
        "category": label,
        "llm": llm_result,
        "explanation": explanation,
        "skill_hits": skill_hits,
        "skill_details": skill_details,
        "evaluated_at": datetime.now(timezone.utc).isoformat()
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


def match_all(job_groups: List[Dict], profile: Dict) -> Dict[str, Dict[str, List[Dict]]]:
    """
    job_groups: [
      {"country": "sweden", "query": "data engineer", "jobs": [...]},
      ...
    ]
    返回 { country: { query: [scored_job, ...] } }
    """
    out: Dict[str, Dict[str, List[Dict]]] = {}
    raw = profile.get("raw", "")
    skills = profile.get("skills", [])

    for group in job_groups:
        country = group.get("country", "unknown")
        query = group.get("query", "")
        jobs = group.get("jobs", [])

        out.setdefault(country, {})
        out[country].setdefault(query, [])

        for job in jobs:
            try:
                scored = score_job(job, raw, skills)
            except Exception as e:
                scored = job.copy()
                scored.update({"error": str(e)})

            out[country][query].append(scored)

    return out
