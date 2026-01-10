#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LLM_local.py — Source-aware autobiography analyzer (directory + subdirs as ONE book)

Features:
1) Timeline mode:
   - Extract date-like info (公历/民国/清代年号/干支/农历词/相对时间)
   - Estimate relative dates (第二年/后来/不久...) using nearest anchor year (optionally same chapter only)
   - Call local LLM (Ollama) to summarize ONE central event per candidate (chunk-based)
   - Outputs: dates_extracted.csv / dates_suspect.csv / dates_relative_estimated.csv / timeline.csv / annotated_with_dates.txt

2) Cases mode (recall-first):
   - Scan for: 买卖/抓捕/腐败/商业布局/白手套 (keyword recall-first)
   - Call local LLM to extract up to N verifiable records per chunk with evidence
   - Outputs: cases.csv

Source-aware outputs:
- Every row includes: source_file + source_line (line number within that file)
- When input is a directory, all *.txt under it (recursive) are treated as one book.

Example (PowerShell):
  .venv\Scripts\python.exe LLM_local.py "MyLifeBookAnalysis" --outdir tmp_llm --llm_backend ollama --base_url http://localhost:11434 --model "qwen2.5:7b" --mode all --chunk_before 6 --chunk_after 10 --lookback 600 --same-chapter-only --case_max 3000 --case_per_chunk 3
"""

import argparse
import csv
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, NamedTuple


# =========================================================
# Source-aware line meta (for verification)
# =========================================================
class LineMeta(NamedTuple):
    source_file: str   # relative path within directory, or filename for single file
    source_line: int   # 1-based line number within that file


def read_book_lines(input_path: str) -> Tuple[List[str], List[LineMeta]]:
    """
    If input_path is a file -> read it as one book.
    If input_path is a directory -> recursively read all *.txt files as one book (sorted by path).
    Returns:
      lines: list[str]
      meta:  list[LineMeta] aligned with lines
    """
    p = Path(input_path)

    def read_text_any(fp: Path) -> List[str]:
        for enc in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return fp.read_text(encoding=enc).splitlines()
            except Exception:
                pass
        return fp.read_text(encoding="utf-8", errors="replace").splitlines()

    lines: List[str] = []
    meta: List[LineMeta] = []

    if p.is_file():
        file_lines = read_text_any(p)
        for i, s in enumerate(file_lines, start=1):
            lines.append(s)
            meta.append(LineMeta(source_file=p.name, source_line=i))
        return lines, meta

    # directory (recursive)
    txt_files = sorted(p.rglob("*.txt"))
    if not txt_files:
        raise ValueError(f"No .txt files found under directory: {input_path}")

    for fp in txt_files:
        rel = fp.relative_to(p).as_posix()
        file_lines = read_text_any(fp)
        for i, s in enumerate(file_lines, start=1):
            lines.append(s)
            meta.append(LineMeta(source_file=rel, source_line=i))

    return lines, meta


# =========================================================
# Data structures
# =========================================================
@dataclass
class DateHit:
    line_no: int
    match: str
    context: str
    kind: str               # iso/greg_full/cn_greg_year/roc/reign/ganzhi/lunar_day/lunar_month/relative
    normalized: str
    ad_year: str
    confidence: str         # HIGH/MED/SUSPECT
    reason: str


@dataclass
class RelEstimate:
    line_no: int
    rel_token: str
    anchor_line: int
    anchor_year: int
    estimate: str
    estimate_year_start: Optional[int]
    estimate_year_end: Optional[int]
    confidence: str         # MED/LOW
    basis: str
    context: str


@dataclass
class EventInfo:
    line_no: int
    source_file: str
    source_line: int
    ad_year_sort: Optional[int]
    date_display: str
    precision: str
    event_summary: str
    tags: str
    who: str
    where: str
    confidence: str
    basis: str
    chunk_span: str
    snippet: str


# =========================================================
# Small helpers
# =========================================================
def context_window(line: str, start: int, end: int, w: int = 60) -> str:
    return line[max(0, start - w): min(len(line), end + w)].strip()


def format_span(line_meta: List[LineMeta], start_ln: int, end_ln: int) -> str:
    a = line_meta[start_ln - 1]
    b = line_meta[end_ln - 1]
    if a.source_file == b.source_file:
        return f"{a.source_file}:{a.source_line}-{b.source_line}"
    return f"{a.source_file}:{a.source_line} .. {b.source_file}:{b.source_line}"


# =========================================================
# OCR heuristics
# =========================================================
OCR_CONFUSABLE = set("卜士干丨丿亅—")


def is_ocr_suspect(text: str) -> bool:
    if "民国" in text and any(c in text for c in "卜士干"):
        return True
    if any(c in OCR_CONFUSABLE for c in text) and any(k in text for k in "年月日"):
        return True
    return False


# =========================================================
# Chinese numerals
# =========================================================
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "廿": 20, "卅": 30, "两": 2}


def cn_digits_to_int(s: str) -> Optional[int]:
    try:
        n = 0
        for c in s:
            n = n * 10 + CN_DIGIT[c]
        return n
    except KeyError:
        return None


def cn_number_to_int(s: str) -> Optional[int]:
    if not s or any(c in s for c in "卜士干"):
        return None
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None

    total = 0
    if "廿" in s:
        total += 20
        s = s.replace("廿", "")
    if "卅" in s:
        total += 30
        s = s.replace("卅", "")

    if "十" in s:
        left, _, right = s.partition("十")
        left_val = (CN_NUM.get(left, 1) if left else 1)
        if left_val is None:
            return None
        total += left_val * 10
        if right:
            rv = 0
            for c in right:
                if c not in CN_NUM or CN_NUM[c] >= 10:
                    return None
                rv = rv * 10 + CN_NUM[c]
            total += rv
        return total

    for c in s:
        if c not in CN_NUM or CN_NUM[c] >= 10:
            return None
        total = total * 10 + CN_NUM[c]
    return total


# =========================================================
# Qing reign conversion (Qing only)
# =========================================================
QING_START = {
    "顺治": 1644, "康熙": 1662, "雍正": 1723, "乾隆": 1736, "嘉庆": 1796,
    "道光": 1821, "咸丰": 1851, "同治": 1862, "光绪": 1875, "宣统": 1909
}


def reign_to_ad(reign: str, year: int) -> Optional[int]:
    start = QING_START.get(reign)
    return start + year - 1 if start and year > 0 else None


# =========================================================
# Patterns
# =========================================================
def build_patterns():
    reigns = "|".join(list(QING_START.keys()))
    return {
        "iso": re.compile(r"(?P<year>(?:18|19|20)\d{2})[-/\.](?P<month>\d{1,2})[-/\.](?P<day>\d{1,2})"),
        "greg_full": re.compile(
            r"(?P<year>(?:18|19|20)\d{2})\s*年"
            r"(?:\s*(?P<month>0?[1-9]|1[0-2])\s*月)?"
            r"(?:\s*(?P<day>0?[1-9]|[12]\d|3[01])\s*(?:日|号))?"
        ),
        "cn_greg_year": re.compile(r"(?P<year_cn>[〇零一二三四五六七八九]{4})\s*年"),
        "roc": re.compile(r"民国\s*(?P<roc_cn>[〇零一二三四五六七八九十廿卅两卜士干\d]+)\s*年"),
        "reign": re.compile(rf"(?P<reign>{reigns})\s*(?P<ycn>[〇零一二三四五六七八九十廿卅两卜士干\d]+)\s*年"),
        "ganzhi": re.compile(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]年"),
        "lunar_day": re.compile(r"(正|腊|冬|闰?[一二三四五六七八九十廿卅]+)月\s*([一二三四五六七八九十廿卅]+)(日|号)"),
        "lunar_month": re.compile(r"(正|腊|冬|闰?[一二三四五六七八九十廿卅]+)月"),
        "relative": re.compile(
            r"(第二年|次年|翌年|当年|那年|今年|去年|明年|"
            r"不久|过了不久|后来|以后|此后|"
            r"数年后|若干年后|几年后)"
        ),
        "chapter": re.compile(r"^第[一二三四五六七八九十百千〇零]+章")
    }


# =========================================================
# Normalize absolute hits
# =========================================================
def normalize_absolute(kind: str, m: re.Match) -> Tuple[str, str, str, str]:
    if kind == "iso":
        y = int(m.group("year"))
        mo = int(m.group("month"))
        d = int(m.group("day"))
        return f"{y:04d}-{mo:02d}-{d:02d}", str(y), "HIGH", "ISO-like numeric date"

    if kind == "greg_full":
        y = int(m.group("year"))
        mo = m.group("month")
        d = m.group("day")
        if mo and d:
            return f"{y:04d}-{int(mo):02d}-{int(d):02d}", str(y), "HIGH", "Gregorian numeric year+month+day"
        if mo:
            return f"{y:04d}-{int(mo):02d}", str(y), "HIGH", "Gregorian numeric year+month"
        return f"{y:04d}", str(y), "HIGH", "Gregorian numeric year"

    if kind == "cn_greg_year":
        y_cn = m.group("year_cn")
        y = cn_digits_to_int(y_cn)
        if y is None:
            return y_cn + "年", "", "SUSPECT", "Chinese-digit year parse failed"
        return f"{y:04d}", str(y), "HIGH", "Chinese-digit Gregorian year"

    if kind == "roc":
        raw = m.group("roc_cn")
        y = cn_number_to_int(raw)
        if y is None:
            return f"民国{raw}年", "", "SUSPECT", "ROC year contains OCR-confusable/unparsable numerals"
        ad = y + 1911
        return f"{ad:04d} (民国{y}年)", str(ad), "HIGH", "ROC year converted to AD"

    if kind == "reign":
        reign = m.group("reign")
        yraw = m.group("ycn")
        y = cn_number_to_int(yraw)
        if y is None:
            return f"{reign}{yraw}年", "", "SUSPECT", "Reign-year contains OCR-confusable/unparsable numerals"
        ad = reign_to_ad(reign, y)
        if ad is None:
            return f"{reign}{y}年", "", "SUSPECT", "Reign-year parsed but not convertible (missing table)"
        return f"{ad:04d} ({reign}{y}年)", str(ad), "HIGH", "Qing reign-year converted to AD"

    if kind == "ganzhi":
        gz = m.group(0)
        return gz, "", "MED", "Ganzhi year (no AD mapping in this script)"

    if kind == "lunar_day":
        return m.group(0), "", "SUSPECT", "Standalone lunar month+day (needs year anchoring)"

    if kind == "lunar_month":
        return m.group(0), "", "SUSPECT", "Standalone lunar month (needs year anchoring)"

    return m.group(0), "", "SUSPECT", "Unknown kind"


# =========================================================
# Extract all hits
# =========================================================
def extract_all_hits(lines: List[str]) -> List[DateHit]:
    pats = build_patterns()
    ordered = [
        ("iso", pats["iso"]),
        ("greg_full", pats["greg_full"]),
        ("cn_greg_year", pats["cn_greg_year"]),
        ("roc", pats["roc"]),
        ("reign", pats["reign"]),
        ("ganzhi", pats["ganzhi"]),
        ("lunar_day", pats["lunar_day"]),
        ("lunar_month", pats["lunar_month"]),
        ("relative", pats["relative"]),
    ]

    hits: List[DateHit] = []
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        for kind, pat in ordered:
            for m in pat.finditer(line):
                raw = m.group(0)
                ctx = context_window(line, m.start(), m.end())

                if kind == "relative":
                    normalized, ad_year, conf, reason = raw, "", "SUSPECT", "Relative time token (to be estimated)"
                else:
                    normalized, ad_year, conf, reason = normalize_absolute(kind, m)

                if is_ocr_suspect(raw) and conf != "SUSPECT":
                    conf = "SUSPECT"
                    reason = "Contains OCR-confusable characters; please verify"

                hits.append(DateHit(
                    line_no=i,
                    match=raw,
                    context=ctx,
                    kind=kind,
                    normalized=normalized,
                    ad_year=ad_year,
                    confidence=conf,
                    reason=reason
                ))
    return hits


# =========================================================
# Anchors + relative estimation
# =========================================================
def build_anchor_years(hits: List[DateHit]) -> Dict[int, Tuple[int, str]]:
    anchors: Dict[int, Tuple[int, str]] = {}
    pri = {"iso": 0, "greg_full": 1, "cn_greg_year": 2, "roc": 3, "reign": 4}

    for h in hits:
        if h.ad_year:
            try:
                y = int(h.ad_year)
            except Exception:
                continue
            note = f"from {h.kind}: {h.match} -> {h.normalized}"
            if h.line_no not in anchors:
                anchors[h.line_no] = (y, note)
            else:
                cur_kind_m = re.search(r"from (\w+):", anchors[h.line_no][1])
                cur_kind = cur_kind_m.group(1) if cur_kind_m else "reign"
                if pri.get(h.kind, 99) < pri.get(cur_kind, 99):
                    anchors[h.line_no] = (y, note)
    return anchors


def estimate_relative_token(token: str, anchor_year: int) -> Tuple[str, Optional[int], Optional[int], str, str]:
    if token in ("第二年", "次年", "翌年"):
        y = anchor_year + 1
        return str(y), y, y, "MED", f"{token} => anchor+1"
    if token == "去年":
        y = anchor_year - 1
        return str(y), y, y, "MED", f"{token} => anchor-1"
    if token == "明年":
        y = anchor_year + 1
        return str(y), y, y, "MED", f"{token} => anchor+1"
    if token in ("当年", "今年", "那年"):
        y = anchor_year
        return str(y), y, y, "MED", f"{token} => anchor"

    if token == "不久":
        a, b = anchor_year, anchor_year + 1
        return f"{a}–{b}", a, b, "LOW", f"{token} => anchor+(0–1y)"
    if token == "过了不久":
        a, b = anchor_year, anchor_year + 2
        return f"{a}–{b}", a, b, "LOW", f"{token} => anchor+(0–2y)"
    if token in ("后来", "以后", "此后"):
        a, b = anchor_year, anchor_year + 5
        return f"{a}–{b}", a, b, "LOW", f"{token} => anchor+(0–5y)"
    if token in ("数年后", "几年后", "若干年后"):
        a, b = anchor_year + 2, anchor_year + 10
        return f"{a}–{b}", a, b, "LOW", f"{token} => anchor+(2–10y)"

    a, b = anchor_year, anchor_year + 3
    return f"{a}–{b}", a, b, "LOW", f"{token} => default anchor+(0–3y)"


def estimate_relative_dates(
    lines: List[str],
    hits: List[DateHit],
    anchors: Dict[int, Tuple[int, str]],
    lookback_lines: int = 200,
    same_chapter_only: bool = False
) -> List[RelEstimate]:
    pats = build_patterns()
    chapter_pat = pats["chapter"]

    chapter_id: Dict[int, int] = {}
    cid = 0
    for i, line in enumerate(lines, start=1):
        if chapter_pat.search(line.strip()):
            cid += 1
        chapter_id[i] = cid

    rel_hits = [h for h in hits if h.kind == "relative"]
    estimates: List[RelEstimate] = []

    for rh in rel_hits:
        ln = rh.line_no
        token = rh.match
        target_cid = chapter_id.get(ln, 0)

        anchor_line = -1
        anchor_year = None
        anchor_note = ""

        start = max(1, ln - lookback_lines)
        for j in range(ln, start - 1, -1):
            if same_chapter_only and chapter_id.get(j, 0) != target_cid:
                continue
            if j in anchors:
                anchor_line = j
                anchor_year, anchor_note = anchors[j]
                break

        if anchor_year is None:
            continue

        est_str, est_a, est_b, conf, rule_reason = estimate_relative_token(token, anchor_year)
        basis = f"anchor {anchor_year} at L{anchor_line} ({anchor_note}); {rule_reason}"
        estimates.append(RelEstimate(
            line_no=ln,
            rel_token=token,
            anchor_line=anchor_line,
            anchor_year=anchor_year,
            estimate=est_str,
            estimate_year_start=est_a,
            estimate_year_end=est_b,
            confidence=conf,
            basis=basis,
            context=rh.context
        ))

    return estimates


# =========================================================
# Chunking (source-aware labels inside chunk)
# =========================================================
def build_chunk(lines: List[str], line_meta: List[LineMeta], center_line: int, before: int = 6, after: int = 10) -> Tuple[str, int, int]:
    start = max(1, center_line - before)
    end = min(len(lines), center_line + after)
    out = []
    for g in range(start, end + 1):
        src = line_meta[g - 1]
        out.append(f"[G{g} {src.source_file}:{src.source_line}] {lines[g - 1].strip()}")
    return "\n".join(out), start, end


# =========================================================
# Local LLM client (Ollama / OpenAI-compatible)
# =========================================================
def http_post_json(url: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(
            f"Cannot reach LLM endpoint: {url}. "
            f"Is the server running and base_url correct? Original error: {e}"
        ) from e


def llm_chat_ollama(base_url: str, model: str, system: str, user: str, temperature: float = 0.0) -> str:
    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": temperature}
    }
    r = http_post_json(url, payload, timeout=180)
    return (r.get("message") or {}).get("content", "")


def llm_chat_openai_compat(base_url: str, model: str, system: str, user: str, temperature: float = 0.0, api_key: str = "local") -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read().decode("utf-8", errors="replace"))
    return r["choices"][0]["message"]["content"]


def parse_llm_json(text: str) -> Optional[dict]:
    if not text:
        return None
    t = text.strip().replace("\ufeff", "")
    if t == "{}":
        return {}
    t = t.strip("`").strip()

    if t.startswith("{") and t.endswith("}"):
        try:
            return json.loads(t)
        except Exception:
            pass

    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        return None
    candidate = m.group(0).strip()
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)  # trailing commas
    try:
        return json.loads(candidate)
    except Exception:
        return None


# =========================================================
# Timeline prompts (strict JSON)
# =========================================================
SYSTEM_EVENT_PROMPT = """You are a careful historian assistant extracting ONE event from an autobiography excerpt.

HARD RULES (must follow exactly):
- Output MUST be a single valid JSON object and NOTHING else (no markdown, no prose, no code fences).
- Use EXACTLY these keys: event_summary, tags, who, where, confidence
- Do NOT output any additional keys.
- If you cannot comply, output exactly: {}

FACTUALITY RULES:
- DO NOT invent facts that are not explicitly present in the excerpt.
- "who" and "where" MUST be copied verbatim from the excerpt (exact substring).
  If not explicitly present, use "".

SCOPE RULES:
- If multiple events appear in the excerpt, summarize ONLY the central/main event.

TAG RULES:
- tags MUST be an array of 0-3 items selected ONLY from:
  ["family","education","military","politics","travel","health","economy","religion","other"]

STYLE RULES:
- event_summary must be Simplified Chinese.
- event_summary should be 1 sentence, <= 30 Chinese characters if possible.
- If no clear event, set event_summary="" and confidence="LOW".

RETURN FORMAT (valid JSON):
{
  "event_summary": "",
  "tags": [],
  "who": "",
  "where": "",
  "confidence": "LOW"
}
"""

ALLOWED_EVENT_TAGS = {"family", "education", "military", "politics", "travel", "health", "economy", "religion", "other"}


def sanitize_event_json(j: dict) -> dict:
    if not isinstance(j, dict) or j == {}:
        return {"event_summary": "", "tags": ["other"], "who": "", "where": "", "confidence": "LOW"}

    event_summary = str(j.get("event_summary") or "").strip()
    who = str(j.get("who") or "").strip()
    where = str(j.get("where") or "").strip()

    tags = j.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip() in ALLOWED_EVENT_TAGS]
    tags = tags[:3] if len(tags) > 3 else tags
    if not tags:
        tags = ["other"]

    conf = str(j.get("confidence") or "LOW").strip().upper()
    if conf not in ("HIGH", "MED", "LOW"):
        conf = "LOW"

    return {"event_summary": event_summary, "tags": tags, "who": who, "where": where, "confidence": conf}


def make_event_user_prompt(
    line_no: int,
    lines: List[str],
    line_meta: List[LineMeta],
    abs_annos: List[DateHit],
    rel_annos: List[RelEstimate],
    chunk_before: int,
    chunk_after: int
) -> Tuple[str, int, int]:
    chunk_text, start_ln, end_ln = build_chunk(lines, line_meta, line_no, before=chunk_before, after=chunk_after)

    abs_str = []
    for h in abs_annos:
        if h.kind != "relative":
            abs_str.append(f"{h.match}→{h.normalized}({h.confidence})")

    rel_str = []
    for r in rel_annos:
        rel_str.append(f"{r.rel_token} EST:{r.estimate}({r.confidence})")

    prompt = (
        f"CHUNK (centered at global line G{line_no}):\n{chunk_text}\n\n"
        f"ABS_TIME: {', '.join(abs_str) if abs_str else 'None'}\n"
        f"REL_TIME: {', '.join(rel_str) if rel_str else 'None'}\n\n"
        f"Task: Extract ONE main event from this chunk without inventing facts.\n"
        f"IMPORTANT: Return ONLY a single JSON object. No extra text."
    )
    return prompt, start_ln, end_ln


def get_event_from_llm(
    backend: str,
    base_url: str,
    model: str,
    line_no: int,
    lines: List[str],
    line_meta: List[LineMeta],
    abs_annos: List[DateHit],
    rel_annos: List[RelEstimate],
    chunk_before: int,
    chunk_after: int,
    temperature: float = 0.0,
    api_key: str = "local"
) -> Tuple[dict, int, int]:
    user_prompt, start_ln, end_ln = make_event_user_prompt(
        line_no, lines, line_meta, abs_annos, rel_annos, chunk_before, chunk_after
    )

    if backend == "ollama":
        out = llm_chat_ollama(base_url, model, SYSTEM_EVENT_PROMPT, user_prompt, temperature=temperature)
    elif backend == "openai_compat":
        out = llm_chat_openai_compat(base_url, model, SYSTEM_EVENT_PROMPT, user_prompt, temperature=temperature, api_key=api_key)
    else:
        raise ValueError(f"Unknown backend: {backend}")

    j = parse_llm_json(out)
    if j is None:
        return {"event_summary": "", "tags": ["other"], "who": "", "where": "", "confidence": "LOW"}, start_ln, end_ln
    return sanitize_event_json(j), start_ln, end_ln


# =========================================================
# CASES (recall-first) scanning
# =========================================================
CASE_KEYWORDS = [
    # 买卖/交易
    "买卖", "交易", "倒卖", "贩卖", "收购", "出售", "转手", "走私", "囤货", "分销", "回扣", "佣金", "抽成", "返点", "利润", "账",
    "合同", "协议", "发票", "结算", "货源", "渠道", "供货", "批文", "许可证", "配额", "指标", "关税",

    # 抓捕/拘押
    "抓捕", "逮捕", "拘留", "扣押", "带走", "传唤", "审讯", "讯问", "审问", "调查", "关押", "羁押", "收监", "监狱", "看守所", "拘留所",
    "押解", "缉拿", "拿办", "拿获", "审办",

    # 腐败/舞弊/贪污（兼容旧式表达）
    "腐败", "贪污", "受贿", "行贿", "索贿", "挪用", "侵吞", "中饱", "克扣", "舞弊", "勒索", "包庇", "通风报信",
    "查账", "审计", "追责", "处分", "专案", "立案",

    # 商业布局/经营
    "布局", "版图", "产业", "赛道", "业务", "板块", "并购", "参股", "控股", "投资", "融资", "项目", "承包", "特许", "贸易", "金融",
    "地产", "矿", "矿产", "物流", "基建", "园区", "供应链", "代理", "经销", "垄断", "扩张", "落地", "开办", "经营",

    # 白手套/代持/影子控制
    "白手套", "代持", "挂名", "名下", "代管", "代收", "代付", "影子股东", "实际控制", "空壳", "皮包公司", "通道", "走账", "洗钱", "过桥"
]
CASE_RE = re.compile("|".join(re.escape(k) for k in CASE_KEYWORDS))

SYSTEM_CASE_PROMPT = """You extract verifiable records from an autobiography excerpt.

HARD RULES:
- Output MUST be strict JSON only, nothing else. If you cannot comply, output {}.
- DO NOT invent facts.
- "entities" must be exact substrings appearing in the excerpt.
- Always provide evidence_lines and evidence_quote copied from the excerpt.
- evidence_quote must be <= 25 Chinese characters.

case_type must be one of:
["trade","arrest","corruption","business_layout","white_glove","other"]

Return a JSON object with top-level key "records", containing up to N items.

JSON schema:
{
  "records": [
    {
      "case_type": "trade|arrest|corruption|business_layout|white_glove|other",
      "summary": "",
      "entities": [],
      "money_or_asset": "",
      "method": "",
      "evidence_lines": "",
      "evidence_quote": "",
      "confidence": "HIGH|MED|LOW"
    }
  ]
}
"""

ALLOWED_CASE_TYPES = {"trade", "arrest", "corruption", "business_layout", "white_glove", "other"}


def make_case_user_prompt(
    line_no: int,
    lines: List[str],
    line_meta: List[LineMeta],
    chunk_before: int,
    chunk_after: int,
    case_per_chunk: int
) -> Tuple[str, int, int]:
    chunk_text, start_ln, end_ln = build_chunk(lines, line_meta, line_no, before=chunk_before, after=chunk_after)
    prompt = (
        f"CHUNK (centered at global line G{line_no}):\n{chunk_text}\n\n"
        f"Task: Extract up to {case_per_chunk} verifiable records related to: 买卖/抓捕/腐败/商业布局/白手套.\n"
        f"Each record MUST include evidence_lines (prefer using the global line labels like G123-G130) "
        f"and evidence_quote copied from the chunk.\n"
        f"Return ONLY JSON with top-level key 'records'. No extra text."
    )
    return prompt, start_ln, end_ln


def sanitize_case_records(records: list, case_per_chunk: int) -> List[dict]:
    cleaned: List[dict] = []
    if not isinstance(records, list):
        return cleaned

    for r in records[:case_per_chunk]:
        if not isinstance(r, dict):
            continue

        ctype = str(r.get("case_type", "other")).strip()
        if ctype not in ALLOWED_CASE_TYPES:
            ctype = "other"

        summary = str(r.get("summary", "")).strip()
        entities = r.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        entities = [str(x).strip() for x in entities if str(x).strip()]
        entities = entities[:12]

        money_or_asset = str(r.get("money_or_asset", "")).strip()
        method = str(r.get("method", "")).strip()
        evidence_lines = str(r.get("evidence_lines", "")).strip()
        evidence_quote = str(r.get("evidence_quote", "")).strip()
        if len(evidence_quote) > 25:
            evidence_quote = evidence_quote[:25]

        conf = str(r.get("confidence", "LOW")).strip().upper()
        if conf not in ("HIGH", "MED", "LOW"):
            conf = "LOW"

        # recall-first: keep as long as there's at least summary OR evidence_quote
        if not summary and not evidence_quote:
            continue

        cleaned.append({
            "case_type": ctype,
            "summary": summary,
            "entities": entities,
            "money_or_asset": money_or_asset,
            "method": method,
            "evidence_lines": evidence_lines,
            "evidence_quote": evidence_quote,
            "confidence": conf
        })

    return cleaned


def get_cases_from_llm(
    backend: str,
    base_url: str,
    model: str,
    line_no: int,
    lines: List[str],
    line_meta: List[LineMeta],
    chunk_before: int,
    chunk_after: int,
    case_per_chunk: int,
    temperature: float = 0.0,
    api_key: str = "local"
) -> Tuple[List[dict], int, int]:
    user_prompt, start_ln, end_ln = make_case_user_prompt(
        line_no, lines, line_meta, chunk_before, chunk_after, case_per_chunk
    )

    if backend == "ollama":
        out = llm_chat_ollama(base_url, model, SYSTEM_CASE_PROMPT, user_prompt, temperature=temperature)
    elif backend == "openai_compat":
        out = llm_chat_openai_compat(base_url, model, SYSTEM_CASE_PROMPT, user_prompt, temperature=temperature, api_key=api_key)
    else:
        raise ValueError(f"Unknown backend: {backend}")

    j = parse_llm_json(out)
    if not isinstance(j, dict) or j == {}:
        return [], start_ln, end_ln

    recs = j.get("records", [])
    return sanitize_case_records(recs, case_per_chunk=case_per_chunk), start_ln, end_ln


# =========================================================
# Timeline helpers
# =========================================================
def build_line_index(hits: List[DateHit], rel_est: List[RelEstimate]) -> Tuple[Dict[int, List[DateHit]], Dict[int, List[RelEstimate]]]:
    hits_by_line: Dict[int, List[DateHit]] = {}
    for h in hits:
        hits_by_line.setdefault(h.line_no, []).append(h)
    rel_by_line: Dict[int, List[RelEstimate]] = {}
    for r in rel_est:
        rel_by_line.setdefault(r.line_no, []).append(r)
    return hits_by_line, rel_by_line


def is_timeline_candidate(abs_hits: List[DateHit], rels: List[RelEstimate]) -> bool:
    if any(h.kind != "relative" for h in abs_hits):
        return True
    if rels:
        return True
    return False


def pick_best_time_for_line(abs_hits: List[DateHit], rels: List[RelEstimate]) -> Tuple[Optional[int], str, str, str]:
    abs_with_ad = [h for h in abs_hits if h.ad_year]
    if abs_with_ad:
        kind_pri = {"iso": 0, "greg_full": 1, "cn_greg_year": 2, "roc": 3, "reign": 4}
        abs_with_ad.sort(key=lambda h: (0 if h.confidence == "HIGH" else 1, kind_pri.get(h.kind, 99)))
        best = abs_with_ad[0]
        y = int(best.ad_year)
        return y, best.normalized, "EXACT", f"ABS: {best.match} -> {best.normalized}"

    if rels:
        r = rels[0]
        ysort = r.estimate_year_start if r.estimate_year_start is not None else None
        disp = f"{r.estimate} (EST)"
        prec = "RANGE" if (r.estimate_year_start is not None and r.estimate_year_end is not None and r.estimate_year_end != r.estimate_year_start) else "EST"
        return ysort, disp, prec, f"REL: {r.rel_token}; {r.basis}"

    return None, "UNKNOWN", "UNKNOWN", "No usable time anchor"


# =========================================================
# Writers (source-aware)
# =========================================================
def write_hits_csv(path: str, rows: List[DateHit], line_meta: List[LineMeta]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_no", "source_file", "source_line", "ad_year", "match", "normalized", "kind", "confidence", "reason", "context"])
        for r in rows:
            src = line_meta[r.line_no - 1]
            w.writerow([r.line_no, src.source_file, src.source_line, r.ad_year, r.match, r.normalized, r.kind, r.confidence, r.reason, r.context])


def write_rel_csv(path: str, rows: List[RelEstimate], line_meta: List[LineMeta]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "line_no", "source_file", "source_line",
            "rel_token",
            "anchor_line", "anchor_source_file", "anchor_source_line",
            "anchor_year",
            "estimate", "estimate_year_start", "estimate_year_end",
            "confidence",
            "basis",
            "context"
        ])
        for r in rows:
            src = line_meta[r.line_no - 1]
            asrc = line_meta[r.anchor_line - 1] if r.anchor_line > 0 else LineMeta("", 0)
            w.writerow([
                r.line_no, src.source_file, src.source_line,
                r.rel_token,
                r.anchor_line, asrc.source_file, asrc.source_line,
                r.anchor_year,
                r.estimate, r.estimate_year_start, r.estimate_year_end,
                r.confidence,
                r.basis,
                r.context
            ])


def write_timeline_csv(path: str, rows: List[EventInfo]) -> None:
    def sk(r: EventInfo):
        return (r.ad_year_sort if r.ad_year_sort is not None else 999999, r.source_file, r.source_line, r.line_no)

    rows_sorted = sorted(rows, key=sk)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ad_year_sort", "date_display", "precision",
            "event_summary", "tags", "who", "where", "confidence",
            "basis",
            "source_file", "source_line",
            "global_line_no",
            "chunk_span",
            "snippet"
        ])
        for r in rows_sorted:
            w.writerow([
                r.ad_year_sort if r.ad_year_sort is not None else "",
                r.date_display, r.precision,
                r.event_summary, r.tags, r.who, r.where, r.confidence,
                r.basis,
                r.source_file, r.source_line,
                r.line_no,
                r.chunk_span,
                r.snippet
            ])


def write_cases_csv(path: str, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "source_file", "source_line", "global_line_no",
            "chunk_span",
            "case_type", "summary", "entities", "money_or_asset", "method",
            "evidence_lines", "evidence_quote", "confidence"
        ])
        for r in rows:
            w.writerow([
                r.get("source_file", ""),
                r.get("source_line", ""),
                r.get("line_no", ""),
                r.get("chunk_span", ""),
                r.get("case_type", ""),
                r.get("summary", ""),
                ";".join(r.get("entities", []) or []),
                r.get("money_or_asset", ""),
                r.get("method", ""),
                r.get("evidence_lines", ""),
                r.get("evidence_quote", ""),
                r.get("confidence", "")
            ])


def write_annotated_text(
    lines: List[str],
    line_meta: List[LineMeta],
    hits_by_line: Dict[int, List[DateHit]],
    rel_by_line: Dict[int, List[RelEstimate]],
    events_by_line: Dict[int, EventInfo],
    out_path: str
) -> None:
    ABS_ORDER = {"iso": 0, "greg_full": 1, "cn_greg_year": 2, "roc": 3, "reign": 4, "ganzhi": 5, "lunar_day": 6, "lunar_month": 7, "relative": 99}

    with open(out_path, "w", encoding="utf-8") as f:
        for g, line in enumerate(lines, start=1):
            src = line_meta[g - 1]
            f.write(f"[G{g} {src.source_file}:{src.source_line}] {line}\n")

            if g in hits_by_line:
                for h in sorted(hits_by_line[g], key=lambda x: ABS_ORDER.get(x.kind, 999)):
                    if h.kind == "relative":
                        continue
                    f.write(
                        f"【DATE | G{g} {src.source_file}:{src.source_line} | {h.match} → {h.normalized}"
                        f"{' | AD:' + h.ad_year if h.ad_year else ''}"
                        f" | {h.confidence} | {h.reason}】\n"
                    )

            if g in rel_by_line:
                for r in rel_by_line[g]:
                    asrc = line_meta[r.anchor_line - 1] if r.anchor_line > 0 else LineMeta("", 0)
                    f.write(
                        f"【REL | G{g} {src.source_file}:{src.source_line} | {r.rel_token} | EST:{r.estimate} | {r.confidence}"
                        f" | anchor G{r.anchor_line} {asrc.source_file}:{asrc.source_line} | {r.basis}】\n"
                    )

            if g in events_by_line:
                ev = events_by_line[g]
                f.write(
                    f"【EVENT | {ev.date_display} | {ev.precision} | {ev.event_summary} | tags:{ev.tags}"
                    f"{' | who:' + ev.who if ev.who else ''}"
                    f"{' | where:' + ev.where if ev.where else ''}"
                    f" | {ev.confidence} | {ev.basis} | span:{ev.chunk_span}】\n"
                )


# =========================================================
# MAIN
# =========================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="Path to OCR txt file OR a directory (recursive) treated as one book")
    ap.add_argument("--outdir", default=".", help="Output directory")

    # shared LLM
    ap.add_argument("--llm_backend", choices=["ollama", "openai_compat"], default="ollama")
    ap.add_argument("--base_url", default="http://localhost:11434", help="Ollama base URL or OpenAI-compatible base URL")
    ap.add_argument("--model", default="qwen2.5:7b", help="Local model name")
    ap.add_argument("--api_key", default="local", help="API key for openai_compat (often ignored by local servers)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between LLM calls")
    ap.add_argument("--chunk_before", type=int, default=6, help="Lines before candidate to include in chunk")
    ap.add_argument("--chunk_after", type=int, default=10, help="Lines after candidate to include in chunk")

    # timeline
    ap.add_argument("--lookback", type=int, default=200, help="Lines to look back for anchor year")
    ap.add_argument("--same-chapter-only", action="store_true", help="Restrict relative anchoring within chapter boundaries")
    ap.add_argument("--max_candidates", type=int, default=1200, help="Max timeline candidate lines to send to LLM")

    # cases
    ap.add_argument("--mode", choices=["timeline", "cases", "all"], default="timeline")
    ap.add_argument("--case_max", type=int, default=3000, help="Max case candidate lines (recall-first)")
    ap.add_argument("--case_per_chunk", type=int, default=3, help="How many records to extract per chunk (recall-first)")

    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    lines, line_meta = read_book_lines(args.input)

    # ----------------------------
    # TIMELINE MODE
    # ----------------------------
    if args.mode in ("timeline", "all"):
        hits = extract_all_hits(lines)
        anchors = build_anchor_years(hits)
        rel_est = estimate_relative_dates(
            lines, hits, anchors,
            lookback_lines=args.lookback,
            same_chapter_only=args.same_chapter_only
        )

        write_hits_csv(os.path.join(args.outdir, "dates_extracted.csv"), hits, line_meta)
        write_hits_csv(os.path.join(args.outdir, "dates_suspect.csv"), [h for h in hits if h.confidence == "SUSPECT"], line_meta)
        write_rel_csv(os.path.join(args.outdir, "dates_relative_estimated.csv"), rel_est, line_meta)

        hits_by_line, rel_by_line = build_line_index(hits, rel_est)

        candidate_lines: List[int] = []
        for ln in range(1, len(lines) + 1):
            abs_hits = hits_by_line.get(ln, [])
            rels = rel_by_line.get(ln, [])
            if is_timeline_candidate(abs_hits, rels):
                if len(lines[ln - 1].strip()) >= 6:
                    candidate_lines.append(ln)

        if len(candidate_lines) > args.max_candidates:
            candidate_lines = candidate_lines[:args.max_candidates]

        timeline_rows: List[EventInfo] = []
        events_by_line: Dict[int, EventInfo] = {}

        for idx, ln in enumerate(candidate_lines, start=1):
            abs_hits = hits_by_line.get(ln, [])
            rels = rel_by_line.get(ln, [])

            ad_year_sort, date_display, precision, basis = pick_best_time_for_line(abs_hits, rels)

            llm_out, start_ln, end_ln = get_event_from_llm(
                backend=args.llm_backend,
                base_url=args.base_url,
                model=args.model,
                line_no=ln,
                lines=lines,
                line_meta=line_meta,
                abs_annos=abs_hits,
                rel_annos=rels,
                chunk_before=args.chunk_before,
                chunk_after=args.chunk_after,
                temperature=args.temperature,
                api_key=args.api_key
            )

            event_summary = (llm_out.get("event_summary") or "").strip()
            tags = llm_out.get("tags") or ["other"]
            who = (llm_out.get("who") or "").strip()
            where = (llm_out.get("where") or "").strip()
            llm_conf = (llm_out.get("confidence") or "LOW").strip().upper()
            if llm_conf not in ("HIGH", "MED", "LOW"):
                llm_conf = "LOW"

            if not event_summary:
                event_summary = "（未明确事件，需人工核对）"
                if llm_conf == "HIGH":
                    llm_conf = "MED"

            # source aware
            src = line_meta[ln - 1]
            chunk_span = format_span(line_meta, start_ln, end_ln)

            # snippet: compact chunk
            chunk_text, _, _ = build_chunk(lines, line_meta, ln, before=args.chunk_before, after=args.chunk_after)
            chunk_snip = chunk_text.replace("\n", " / ")
            if len(chunk_snip) > 320:
                chunk_snip = chunk_snip[:320] + "…"

            row = EventInfo(
                line_no=ln,
                source_file=src.source_file,
                source_line=src.source_line,
                ad_year_sort=ad_year_sort,
                date_display=date_display,
                precision=precision,
                event_summary=event_summary,
                tags=";".join([str(t) for t in tags]),
                who=who,
                where=where,
                confidence=llm_conf,
                basis=basis,
                chunk_span=chunk_span,
                snippet=chunk_snip
            )
            timeline_rows.append(row)
            events_by_line[ln] = row

            if args.sleep > 0:
                time.sleep(args.sleep)
            if idx % 50 == 0:
                print(f"[timeline] LLM summarized {idx}/{len(candidate_lines)} candidates...")

        write_timeline_csv(os.path.join(args.outdir, "timeline.csv"), timeline_rows)
        write_annotated_text(
            lines,
            line_meta,
            hits_by_line,
            rel_by_line,
            events_by_line,
            os.path.join(args.outdir, "annotated_with_dates.txt")
        )
        print(f"\n[timeline] DONE. timeline rows: {len(timeline_rows)}")

    # ----------------------------
    # CASES MODE (recall-first)
    # ----------------------------
    if args.mode in ("cases", "all"):
        case_candidate_lines: List[int] = []
        for ln in range(1, len(lines) + 1):
            line = lines[ln - 1].strip()
            if len(line) < 4:
                continue
            if CASE_RE.search(line):
                case_candidate_lines.append(ln)

        # light dedup: if within 2 lines, keep only one to reduce redundant chunks
        dedup: List[int] = []
        last = -999
        for ln in case_candidate_lines:
            if ln - last <= 2:
                continue
            dedup.append(ln)
            last = ln
        case_candidate_lines = dedup[:args.case_max]

        cases_rows: List[dict] = []
        for idx, ln in enumerate(case_candidate_lines, start=1):
            recs, start_ln, end_ln = get_cases_from_llm(
                backend=args.llm_backend,
                base_url=args.base_url,
                model=args.model,
                line_no=ln,
                lines=lines,
                line_meta=line_meta,
                chunk_before=args.chunk_before,
                chunk_after=args.chunk_after,
                case_per_chunk=args.case_per_chunk,
                temperature=args.temperature,
                api_key=args.api_key
            )

            src = line_meta[ln - 1]
            chunk_span = format_span(line_meta, start_ln, end_ln)

            for r in recs:
                r["line_no"] = ln
                r["source_file"] = src.source_file
                r["source_line"] = src.source_line
                r["chunk_span"] = chunk_span
                cases_rows.append(r)

            if args.sleep > 0:
                time.sleep(args.sleep)
            if idx % 50 == 0:
                print(f"[cases] scanned {idx}/{len(case_candidate_lines)} chunks...")

        write_cases_csv(os.path.join(args.outdir, "cases.csv"), cases_rows)
        print(f"\n[cases] DONE. case records: {len(cases_rows)}")

    print("\nALL DONE")


if __name__ == "__main__":
    main()
