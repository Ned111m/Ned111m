"""AI-ism ("slop") check for anything published under the owner's name: VO scripts, titles, descriptions, posts.

Lists come from the public slop-forensics / antislop projects behind EQ-Bench's Slop Score (downloaded to a local
folder, path in crew/local/paths.json key "slop_dir"). Word and phrase lists are English; for other languages only the
language-independent checks (em dash, repeated rhetorical patterns) apply. Output is deterministic: the exact hits,
hits per 1000 words and a verdict."""
from __future__ import annotations

import json, re
from pathlib import Path

_CACHE: dict = {}

# Language-independent patterns that read as machine-written in Nedim's outward text (his standing rules).
PATTERNS = {
    "em dash": r"—",
    "not X but Y": r"\b(not|isn't|wasn't) (just|only|merely) [^.,;]{1,40}(, but| but)\b",
    "rule of three buzz": r"\b(\w+), (\w+),? and (\w+)\b(?= [^.]{0,20}\.)",
}


def _lists(slop_dir: str) -> dict:
    if slop_dir in _CACHE:
        return _CACHE[slop_dir]
    d = Path(slop_dir)
    words = {w[0].lower() for w in json.loads((d / "slop_list.json").read_text(encoding="utf-8")) if w}
    phrases = {p[0].lower(): float(p[1]) for p in json.loads((d / "slop_phrase_prob_adjustments.json").read_text(encoding="utf-8"))}
    for f in ("slop_list_bigrams.json", "slop_list_trigrams.json"):
        for p in json.loads((d / f).read_text(encoding="utf-8")):
            phrases.setdefault(" ".join(p).lower() if isinstance(p, list) else str(p).lower(), 0.5)
    _CACHE[slop_dir] = {"words": words, "phrases": phrases}
    return _CACHE[slop_dir]


def check(text: str, slop_dir: str, language: str = "en") -> dict:
    t = text.lower()
    n_words = max(1, len(re.findall(r"\w+", t)))
    hits = []
    for name, rx in PATTERNS.items():
        for m in re.finditer(rx, text, flags=re.I):
            if name == "rule of three buzz" and language != "en":
                continue
            hits.append({"kind": "pattern", "what": name, "text": m.group(0)[:60]})
    if language == "en":
        L = _lists(slop_dir)
        for ph, prob in L["phrases"].items():
            if " " in ph or len(ph) > 3:
                for m in re.finditer(r"\b" + re.escape(ph) + r"\b", t):
                    hits.append({"kind": "phrase", "what": ph, "weight": round(1 - prob, 2)})
        for w in re.findall(r"[a-z']+", t):
            if w in L["words"] and not any(h["what"] == w for h in hits):
                hits.append({"kind": "word", "what": w})
    per_k = round(1000 * len(hits) / n_words, 1)
    em = any(h["what"] == "em dash" for h in hits)
    verdict = "FAIL" if em or per_k >= 8 else ("WARN" if hits else "PASS")
    return {"words": n_words, "hits": hits[:80], "hits_per_1000_words": per_k, "verdict": verdict,
            "rule": "Rewrite every hit in plain, specific words (the em dash is always a FAIL: use a comma). "
                    "Word/phrase lists are English-only; for Bosnian only the pattern checks run."}
