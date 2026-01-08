#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LLM_local.py — Autobiography timeline builder (local Ollama, chunk-based)

What it does:
1) Extract date-like info from Chinese OCR text (AD / 民国 / 清代年号 / 干支 / 农历月日 / 相对时间).
2) Normalize: 民国->公历, 清代年号(仅清)->公历.
3) Estimate relative dates (第二年/次年/翌年/后来/不久...) using nearest previous anchor year.
4) Build timeline candidates and call a LOCAL model (Ollama) to produce JSON event summaries.
   IMPORTANT: This version uses CHUNKS (± lines) instead of single-line prompts for better quality.
5) Output:
   - dates_extracted.csv
   - dates_suspect.csv
   - dates_relative_estimated.csv
   - timeline.csv
   - annotated_with_dates.txt (original content + DATE + REL + EVENT)

Example (Windows PowerShell):
  .venv\Scripts\python.exe LLM_local.py "book.txt" --outdir tmp_llm --llm_backend ollama --base_url http://localhost:11434 --model "qwen2.5:7b" --same-chapter-only
"""

import argparse
import csv
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import urllib.request


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
    ad_year_sort: Optional[int]
    date_display: str       # e.g., "1882", "1893–1894 (EST)", "UNKNOWN"
    precision: str          # EXACT/EST/RANGE/UNKNOWN
    event_summary: str
    tags: str               # semicolon separated
    who: str
    where: str
    confidence: str         # HIGH/MED/LOW
    basis: str              # time basis
    snippet: str            # chunk snippet (short)


# =========================================================
# IO helpers
# =========================================================
def read_text_lines(path: str) -> List[str]:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read().splitlines()
        except Exception:
            pass
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def context_window(line: str, start: int, end: int, w: int = 60) -> str:
    return line[max(0, start - w): min(len(line), end + w)].strip()


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
CN_DIGIT = {"零":0,"〇":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
CN_NUM = {"零":0,"〇":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"廿":20,"卅":30,"两":2}

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
    "顺治":1644,"康熙":1662,"雍正":1723,"乾隆":1736,"嘉庆":1796,
    "道光":1821,"咸丰":1851,"同治":1862,"光绪":1875,"宣统":1909
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
    pri = {"iso":0,"greg_full":1,"cn_greg_year":2,"roc":3,"reign":4}

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
                cur_y, cur_note = anchors[h.line_no]
                cur_kind_m = re.search(r"from (\w+):", cur_note)
                cur_kind = cur_kind_m.group(1) if cur_kind_m else "reign"
                if pri.get(h.kind, 99) < pri.get(cur_kind, 99):
                    anchors[h.line_no] = (y, note)
    return anchors


def estimate_relative_token(token: str, anchor_year: int) -> Tuple[str, Optional[int], Optional[int], str, str]:
    # deterministic
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

    # ranges
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

    # chapter id per line
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
# Chunking (IMPORTANT: per-chunk prompt)
# =========================================================
def build_chunk(lines: List[str], center_line: int, before: int = 3, after: int = 5) -> str:
    start = max(1, center_line - before)
    end = min(len(lines), center_line + after)
    out = []
    for i in range(start, end + 1):
        out.append(f"[L{i}] {lines[i - 1].strip()}")
    return "\n".join(out)


# =========================================================
# Local LLM client (Ollama / OpenAI-compatible)
# =========================================================
def http_post_json(url: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
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
        "messages": [{"role":"system","content":system},{"role":"user","content":user}],
        "stream": False,
        "options": {"temperature": temperature}
    }
    r = http_post_json(url, payload, timeout=180)
    return (r.get("message") or {}).get("content", "")


def llm_chat_openai_compat(base_url: str, model: str, system: str, user: str, temperature: float = 0.0, api_key: str = "local") -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role":"system","content":system},{"role":"user","content":user}],
        "temperature": temperature
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type":"application/json", "Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            r = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(
            f"Cannot reach OpenAI-compatible endpoint: {url}. "
            f"Original error: {e}"
        ) from e
    return r["choices"][0]["message"]["content"]


SYSTEM_EVENT_PROMPT = """You are a careful historian assistant extracting events from an autobiography.
Rules:
- DO NOT invent facts not present in the excerpt.
- If unclear, say so by returning empty fields (event_summary can be empty).
- Keep event_summary in Simplified Chinese, 1 sentence, <= 30 Chinese characters if possible.
- Extract who/where only if explicitly present in the excerpt.
- Output MUST be strict JSON only, no extra text.

Schema:
{
  "event_summary": "string",
  "tags": ["family|education|military|politics|travel|health|economy|religion|other", ...],
  "who": "string",
  "where": "string",
  "confidence": "HIGH|MED|LOW"
}
"""


def parse_llm_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def make_user_prompt(
    line_no: int,
    lines: List[str],
    abs_annos: List[DateHit],
    rel_annos: List[RelEstimate],
    chunk_before: int,
    chunk_after: int
) -> str:
    chunk_text = build_chunk(lines, line_no, before=chunk_before, after=chunk_after)

    abs_str = []
    for h in abs_annos:
        if h.kind != "relative":
            abs_str.append(f"{h.match}→{h.normalized}({h.confidence})")

    rel_str = []
    for r in rel_annos:
        rel_str.append(f"{r.rel_token} EST:{r.estimate}({r.confidence})")

    return (
        f"CHUNK (centered at L{line_no}):\n"
        f"{chunk_text}\n\n"
        f"ABS_TIME: {', '.join(abs_str) if abs_str else 'None'}\n"
        f"REL_TIME: {', '.join(rel_str) if rel_str else 'None'}\n\n"
        f"Task: Extract ONE main event from this chunk without inventing facts. "
        f"If no clear event, return empty event_summary and LOW confidence."
    )


def get_event_from_llm(
    backend: str,
    base_url: str,
    model: str,
    line_no: int,
    lines: List[str],
    abs_annos: List[DateHit],
    rel_annos: List[RelEstimate],
    chunk_before: int,
    chunk_after: int,
    temperature: float = 0.0,
    api_key: str = "local"
) -> dict:
    user_prompt = make_user_prompt(line_no, lines, abs_annos, rel_annos, chunk_before, chunk_after)

    if backend == "ollama":
        out = llm_chat_ollama(base_url, model, SYSTEM_EVENT_PROMPT, user_prompt, temperature=temperature)
    elif backend == "openai_compat":
        out = llm_chat_openai_compat(base_url, model, SYSTEM_EVENT_PROMPT, user_prompt, temperature=temperature, api_key=api_key)
    else:
        raise ValueError(f"Unknown backend: {backend}")

    j = parse_llm_json(out)
    if not j:
        return {"event_summary": "", "tags": ["other"], "who": "", "where": "", "confidence": "LOW"}

    event_summary = str(j.get("event_summary", "")).strip()
    tags = j.get("tags", [])
    if not isinstance(tags, list):
        tags = ["other"]
    tags = [str(t).strip() for t in tags if str(t).strip()]
    if not tags:
        tags = ["other"]

    who = str(j.get("who", "")).strip()
    where = str(j.get("where", "")).strip()
    conf = str(j.get("confidence", "LOW")).strip().upper()
    if conf not in ("HIGH", "MED", "LOW"):
        conf = "LOW"

    return {"event_summary": event_summary, "tags": tags, "who": who, "where": where, "confidence": conf}


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
        kind_pri = {"iso":0, "greg_full":1, "cn_greg_year":2, "roc":3, "reign":4}
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
# Output writers
# =========================================================
def write_hits_csv(path: str, rows: List[DateHit]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_no","ad_year","match","normalized","kind","confidence","reason","context"])
        for r in rows:
            w.writerow([r.line_no, r.ad_year, r.match, r.normalized, r.kind, r.confidence, r.reason, r.context])


def write_rel_csv(path: str, rows: List[RelEstimate]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_no","rel_token","anchor_line","anchor_year","estimate","estimate_year_start","estimate_year_end","confidence","basis","context"])
        for r in rows:
            w.writerow([r.line_no,r.rel_token,r.anchor_line,r.anchor_year,r.estimate,r.estimate_year_start,r.estimate_year_end,r.confidence,r.basis,r.context])


def write_timeline_csv(path: str, rows: List[EventInfo]) -> None:
    def sk(r: EventInfo):
        return (r.ad_year_sort if r.ad_year_sort is not None else 999999, r.line_no)

    rows_sorted = sorted(rows, key=sk)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ad_year_sort","date_display","precision","event_summary","tags","who","where","confidence","basis","line_no","snippet"])
        for r in rows_sorted:
            w.writerow([
                r.ad_year_sort if r.ad_year_sort is not None else "",
                r.date_display,
                r.precision,
                r.event_summary,
                r.tags,
                r.who,
                r.where,
                r.confidence,
                r.basis,
                r.line_no,
                r.snippet
            ])


def write_annotated_text(
    lines: List[str],
    hits_by_line: Dict[int, List[DateHit]],
    rel_by_line: Dict[int, List[RelEstimate]],
    events_by_line: Dict[int, EventInfo],
    out_path: str
) -> None:
    ABS_ORDER = {"iso":0,"greg_full":1,"cn_greg_year":2,"roc":3,"reign":4,"ganzhi":5,"lunar_day":6,"lunar_month":7,"relative":99}

    with open(out_path, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines, start=1):
            f.write(line + "\n")

            if i in hits_by_line:
                for h in sorted(hits_by_line[i], key=lambda x: ABS_ORDER.get(x.kind, 999)):
                    if h.kind == "relative":
                        continue
                    f.write(
                        f"【DATE@L{i} | {h.match} → {h.normalized}"
                        f"{' | AD:' + h.ad_year if h.ad_year else ''}"
                        f" | {h.confidence} | {h.reason}】\n"
                    )

            if i in rel_by_line:
                for r in rel_by_line[i]:
                    f.write(f"【REL@L{i} | {r.rel_token} | EST:{r.estimate} | {r.confidence} | basis: {r.basis}】\n")

            if i in events_by_line:
                ev = events_by_line[i]
                f.write(
                    f"【EVENT@L{i} | {ev.date_display} | {ev.precision} | {ev.event_summary} | tags:{ev.tags}"
                    f"{' | who:' + ev.who if ev.who else ''}"
                    f"{' | where:' + ev.where if ev.where else ''}"
                    f" | {ev.confidence} | {ev.basis}】\n"
                )


# =========================================================
# Main
# =========================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="Path to OCR autobiography txt")
    ap.add_argument("--outdir", default=".", help="Output directory")
    ap.add_argument("--lookback", type=int, default=200, help="Lines to look back for anchor year")
    ap.add_argument("--same-chapter-only", action="store_true", help="Restrict relative anchoring within chapter boundaries")
    ap.add_argument("--llm_backend", choices=["ollama","openai_compat"], default="ollama")
    ap.add_argument("--base_url", default="http://localhost:11434", help="Ollama base URL or OpenAI-compatible base URL")
    ap.add_argument("--model", default="qwen2.5:7b", help="Local model name")
    ap.add_argument("--api_key", default="local", help="API key for openai_compat (often ignored by local servers)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max_candidates", type=int, default=1200, help="Max candidate lines to send to LLM")
    ap.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between LLM calls")
    ap.add_argument("--chunk_before", type=int, default=3, help="Lines before candidate to include in chunk")
    ap.add_argument("--chunk_after", type=int, default=5, help="Lines after candidate to include in chunk")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # 1) Read
    lines = read_text_lines(args.input)

    # 2) Extract hits
    hits = extract_all_hits(lines)

    # 3) Anchors + relative estimates
    anchors = build_anchor_years(hits)
    rel_est = estimate_relative_dates(
        lines, hits, anchors,
        lookback_lines=args.lookback,
        same_chapter_only=args.same_chapter_only
    )

    # 4) Write base CSVs
    write_hits_csv(os.path.join(args.outdir, "dates_extracted.csv"), hits)
    write_hits_csv(os.path.join(args.outdir, "dates_suspect.csv"), [h for h in hits if h.confidence == "SUSPECT"])
    write_rel_csv(os.path.join(args.outdir, "dates_relative_estimated.csv"), rel_est)

    # 5) Build indices
    hits_by_line, rel_by_line = build_line_index(hits, rel_est)

    # 6) Choose candidate lines
    candidate_lines: List[int] = []
    for ln in range(1, len(lines) + 1):
        abs_hits = hits_by_line.get(ln, [])
        rels = rel_by_line.get(ln, [])
        if is_timeline_candidate(abs_hits, rels):
            if len(lines[ln - 1].strip()) >= 6:
                candidate_lines.append(ln)

    if len(candidate_lines) > args.max_candidates:
        candidate_lines = candidate_lines[:args.max_candidates]

    # 7) LLM summarization per candidate (CHUNK-based)
    timeline_rows: List[EventInfo] = []
    events_by_line: Dict[int, EventInfo] = {}

    for idx, ln in enumerate(candidate_lines, start=1):
        abs_hits = hits_by_line.get(ln, [])
        rels = rel_by_line.get(ln, [])

        ad_year_sort, date_display, precision, basis = pick_best_time_for_line(abs_hits, rels)

        llm_out = get_event_from_llm(
            backend=args.llm_backend,
            base_url=args.base_url,
            model=args.model,
            line_no=ln,
            lines=lines,
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

        chunk_snip = build_chunk(lines, ln, before=args.chunk_before, after=args.chunk_after).replace("\n", " / ")
        if len(chunk_snip) > 280:
            chunk_snip = chunk_snip[:280] + "…"

        row = EventInfo(
            line_no=ln,
            ad_year_sort=ad_year_sort,
            date_display=date_display,
            precision=precision,
            event_summary=event_summary,
            tags=";".join([str(t) for t in tags]),
            who=who,
            where=where,
            confidence=llm_conf,
            basis=basis,
            snippet=chunk_snip
        )
        timeline_rows.append(row)
        events_by_line[ln] = row

        if args.sleep > 0:
            time.sleep(args.sleep)

        if idx % 50 == 0:
            print(f"LLM summarized {idx}/{len(candidate_lines)} candidates...")

    # 8) Outputs
    write_timeline_csv(os.path.join(args.outdir, "timeline.csv"), timeline_rows)
    write_annotated_text(
        lines,
        hits_by_line,
        rel_by_line,
        events_by_line,
        os.path.join(args.outdir, "annotated_with_dates.txt")
    )

    print("\nDONE")
    print(f"Lines: {len(lines)}")
    print(f"Date-like hits: {len(hits)}")
    print(f"Relative estimates: {len(rel_est)}")
    print(f"Timeline rows: {len(timeline_rows)}")
    print(f"Wrote: {os.path.join(args.outdir, 'timeline.csv')}")
    print(f"Wrote: {os.path.join(args.outdir, 'annotated_with_dates.txt')}")


if __name__ == "__main__":
    main()


