# worker_env/src/parse_md.py
from typing import Dict, List
import re
from pathlib import Path

def parse_resume_md(md_path: str) -> Dict:
    """
    解析 resume.md：
    - 返回 raw 文本
    - 提取 sections（基于 markdown headings）
    - 尝试提取 skills 列表（按行或逗号分隔），优先取最后一个可能命名为 skills 的 section
    """
    p = Path(md_path)
    text = p.read_text(encoding="utf-8")
    # 基于 heading 划分
    lines = text.splitlines()
    sections = []
    cur_h = None
    cur_lines = []
    for ln in lines:
        m = re.match(r'^(#{1,6})\s*(.+)', ln)
        if m:
            if cur_h is not None:
                sections.append((cur_h, "\n".join(cur_lines).strip()))
            cur_h = m.group(2).strip()
            cur_lines = []
        else:
            cur_lines.append(ln)
    # flush
    if cur_h is not None:
        sections.append((cur_h, "\n".join(cur_lines).strip()))
    else:
        # 没有任何 heading，整个文件作为 intro
        sections = [("intro", text)]

    # normalize section keys
    sections_norm = {k.strip().lower(): v for k, v in sections}

    # 找 skills 相关的 section（关键词）
    skill_keys = [k for k in sections_norm.keys() if any(tok in k for tok in ("skill", "技能", "technology", "tech", "tech stack", "tools", "skills"))]
    skills = []
    if skill_keys:
        # 取最后一个出现的技能段（通常用户把技能放末尾）
        k = skill_keys[-1]
        block = sections_norm[k]
        # split by lines, commas, or bullets
        items = []
        for line in block.splitlines():
            line = line.strip().lstrip("-*•").strip()
            if not line:
                continue
            # comma separated
            for part in re.split(r'[;,\/]| and ', line):
                part = part.strip()
                if part:
                    items.append(part)
        # dedupe and lowercase
        skills = list(dict.fromkeys([s.lower() for s in items]))
    else:
        # 作为兜底：在全文里找常见以逗号或换行分隔的技能行（极简 heuristics）
        cand = []
        for line in text.splitlines()[-12:]:  # look at last 12 lines
            if len(line) < 200 and ("," in line or "•" in line or "-" in line):
                cand.append(line)
        if cand:
            last = cand[-1]
            items = [p.strip().lower() for p in re.split(r'[;,\/]| and ', last) if p.strip()]
            skills = list(dict.fromkeys(items))

    return {"raw": text, "sections": sections_norm, "skills": skills}
