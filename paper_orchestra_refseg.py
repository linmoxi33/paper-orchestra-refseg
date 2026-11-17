#!/usr/bin/env python3
# Copyright (c) 2026 linmoxi33
# SPDX-License-Identifier: MIT
"""Predict reference re-numbering drift inside PaperOrchestra's citation-F1 autorater.

The autorater (autoraters/citation_f1.py) turns a PDF reference section into a
list by literally calling ``re.split(r"\\[\\d+\\]", raw_text)`` and then asking an
LLM to label the i-th surviving segment as P0/P1 with the synthetic number i+1.
If the PDF numbers references as ``1.`` / ``\\bibitem`` / superscripts, or if the
section starts with a "References" header, that synthetic numbering no longer
matches the paper's real reference numbers.  This tool reproduces the exact
upstream segmentation, parses the same text independently, and reports the
resulting permutation -- offline, without an API key.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Verbatim from autoraters/citation_f1.py (line 95).
UPSTREAM_PATTERN = r"\[\d+\]"

BRACKET_LINE = re.compile(r"(?m)^\s*\[\s*(\d{1,3})\s*\]")
DOT_LINE = re.compile(r"(?m)^\s*(\d{1,3})[.)]\s+")
BIBITEM = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}")
BRACKET_ANY = re.compile(r"\[\s*(\d{1,3})\s*\]")


def upstream_segments(raw):
    """Byte-for-byte reproduction of ReferenceF1V1Evaluator._clean_text_to_list."""
    segments = re.split(UPSTREAM_PATTERN, raw)
    cleaned = []
    for seg in segments:
        text = seg.strip().replace("\n", " ").replace("  ", " ")
        if text:
            cleaned.append(text)
    return cleaned


def detect_style(raw):
    if BIBITEM.search(raw):
        return "bibitem"
    bracket_lines = BRACKET_LINE.findall(raw)
    if bracket_lines:
        return "bracket-sequential"
    if BRACKET_ANY.search(raw):
        return "bracket-inline"
    if DOT_LINE.search(raw):
        return "numbered-dot"
    return "unmarked"


def _slice(raw, matches, num_of, key_of=None):
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        entries.append({
            "num": num_of(i, m),
            "body": raw[m.end():end].strip(),
            "key": key_of(m) if key_of else None,
        })
    return entries


def parse_entries(raw):
    """Independent, style-aware parse of the same reference section."""
    style = detect_style(raw)
    if style in ("bracket-sequential", "bracket-inline"):
        pat = BRACKET_LINE if style == "bracket-sequential" else BRACKET_ANY
        ms = list(pat.finditer(raw))
        return style, _slice(raw, ms, lambda i, m: int(m.group(1)))
    if style == "numbered-dot":
        ms = list(DOT_LINE.finditer(raw))
        return style, _slice(raw, ms, lambda i, m: int(m.group(1)))
    if style == "bibitem":
        ms = list(BIBITEM.finditer(raw))
        return style, _slice(raw, ms, lambda i, m: i + 1, lambda m: m.group(1))
    return style, [{"num": 1, "body": raw.strip(), "key": None}]


def _norm(text):
    text = re.sub(r"^\s*\d{1,3}[.)]\s+", "", text)
    text = text.lower().replace("\n", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match(seg_norm, entries_norm):
    best_score, best_idx = 0, -1
    for idx, body in enumerate(entries_norm):
        if not seg_norm or not body:
            continue
        common = 0
        for a, b in zip(seg_norm, body):
            if a != b:
                break
            common += 1
        if (seg_norm in body or body in seg_norm) and min(len(seg_norm), len(body)) >= 15:
            score = max(len(seg_norm), len(body)) + 100
        else:
            score = common
        if score > best_score:
            best_score, best_idx = score, idx
    return best_idx if best_score >= 15 else -1


def audit(raw, name="<stdin>"):
    style, entries = parse_entries(raw)
    segs = upstream_segments(raw)
    entries_norm = [_norm(e["body"]) for e in entries]
    mapping, used = [], set()
    for synth, seg in enumerate(segs, start=1):
        idx = _match(_norm(seg), entries_norm)
        if idx >= 0 and idx not in used:
            used.add(idx)
            real = entries[idx]["num"]
            mapping.append({"synthetic": synth, "real": real, "drift": real - synth,
                            "preview": seg[:100]})
        else:
            mapping.append({"synthetic": synth, "real": None, "drift": None,
                            "preview": seg[:100]})
    phantom = [m["preview"] for m in mapping if m["real"] is None]
    missed = [{"real": entries[j]["num"], "preview": entries[j]["body"][:100]}
              for j in range(len(entries)) if j not in used]
    drifts = [m["drift"] for m in mapping if m["drift"] not in (None, 0)]
    clean = (style == "bracket-sequential" and len(segs) == len(entries)
             and not phantom and not missed and not drifts
             and all(m["real"] == m["synthetic"] for m in mapping))
    if clean:
        verdict = "clean"
    elif len(segs) <= 1:
        verdict = "collapsed"
    else:
        verdict = "misnumbered"
    notes = []
    if style != "bracket-sequential":
        notes.append(
            "upstream only splits on '[' + digits + ']'; style '%s' is not split, "
            "so %d real entries become %d segment(s)" % (style, len(entries), len(segs)))
    if phantom:
        notes.append("%d upstream segment(s) match no real entry (phantom, e.g. section header)"
                     % len(phantom))
    if missed:
        notes.append("%d real entries are never shown to the priority LLM" % len(missed))
    if drifts:
        notes.append("synthetic index -> real reference number drifts by %s"
                     % sorted(set(drifts)))
    return {
        "file": name,
        "style": style,
        "upstream_segments": len(segs),
        "real_entries": len(entries),
        "mapping": mapping,
        "phantom_segments": phantom,
        "missed_entries": missed,
        "max_abs_drift": max([abs(d) for d in drifts], default=0),
        "verdict": verdict,
        "clean": clean,
        "exit_code": 0 if clean else 1,
        "notes": notes,
    }


def render_text(report):
    lines = [
        "file: %s" % report["file"],
        "style: %s | upstream segments: %d | real entries: %d"
        % (report["style"], report["upstream_segments"], report["real_entries"]),
        "verdict: %s (max |drift| = %d)" % (report["verdict"], report["max_abs_drift"]),
    ]
    for m in report["mapping"]:
        real = "PHANTOM" if m["real"] is None else str(m["real"])
        drift = "" if m["drift"] in (None, 0) else "  drift=%+d" % m["drift"]
        lines.append("  synth[%d] -> real %s%s  %s"
                     % (m["synthetic"], real, drift, m["preview"][:70]))
    for note in report["notes"]:
        lines.append("note: " + note)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="paper-orchestra-refseg",
        description="Predict reference re-numbering drift in PaperOrchestra's citation F1 autorater.")
    parser.add_argument("refs", help="text file holding the paper's reference section")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    args = parser.parse_args(argv)  # argparse exits 2 on usage errors

    path = Path(args.refs)
    if not path.is_file():
        print("error: no such file: %s" % path, file=sys.stderr)
        return 2
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read %s: %s" % (path, exc), file=sys.stderr)
        return 2
    if not raw.strip():
        print("error: %s is empty" % path, file=sys.stderr)
        return 2

    report = audit(raw, str(path))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
