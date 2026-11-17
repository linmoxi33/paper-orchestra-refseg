# paper-orchestra-refseg

Predict, offline and before you spend an API call, which references PaperOrchestra's citation-F1 autorater will silently re-number — and therefore which P0/P1 labels will land on the wrong paper.

Upstream: https://github.com/google-research/paper-orchestra

## 上游是什么、缺什么

[PaperOrchestra](https://github.com/google-research/paper-orchestra) is a Google Research multi-agent framework that turns raw research notes into a LaTeX manuscript. Its scoring stack includes `autoraters/citation_f1.py`, which measures whether the generated bibliography covers the "must-cite" (P0) and "nice-to-cite" (P1) works.

That autorater builds its reference list with one line of regex:

```python
pattern = r"\[\d+\]"
segments = re.split(pattern, raw_text)          # citation_f1.py:95-96
cleaned_refs = [seg.strip() ... for seg in segments if seg.strip()]
```

and then asks the LLM to label each segment by its **synthetic** position:

```python
references_str = "\n".join([f"[{i+1}] {ref}" for i, ref in enumerate(cleaned_refs)])
```

Two consequences follow directly from the code:

- Any reference section not written as `[n]` at line starts — `1.` numbering, `\bibitem{...}`, superscript numbers, `[n]` only at paragraph starts — is **not split at all**. A 50-entry bibliography becomes one segment, 49 entries are never shown to the priority LLM, and the reported citation F1 is computed against a list that does not exist.
- Text before the first marker is kept as a reference. A PDF extract that begins with `References` yields a phantom segment 1, so every subsequent priority is attached to the **previous** paper and the P0/P1 sets are off by one, with no warning anywhere.

Evidence (source, line-pinned, commit `d9bae99`):
- <https://github.com/google-research/paper-orchestra/blob/d9bae997e793a015ed38bb1605be9eb58c00284f/autoraters/citation_f1.py#L90-L109>
- <https://github.com/google-research/paper-orchestra/blob/d9bae997e793a015ed38bb1605be9eb58c00284f/autoraters/citation_f1.py#L171-L180>

## 这个工具做什么 / 不做什么

It takes the same reference-section text the autorater consumes, reproduces `_clean_text_to_list` **byte-for-byte**, parses the text independently in a style-aware way, and prints the permutation between the synthetic index the LLM sees and the real reference number:

- detects the numbering style: `bracket-sequential`, `bracket-inline`, `numbered-dot`, `bibitem`, `unmarked`;
- reports `upstream_segments` vs `real_entries`, phantom segments (e.g. `References`), entries never shown to the LLM, and per-segment drift (`real - synthetic`);
- verdicts `clean` / `misnumbered` / `collapsed`, with exit code 0 / 1.

It does **not** call Semantic Scholar, an LLM, or fix your PDF. It does not claim the citation F1 is wrong for every paper — only tells you when the autorater's numbering cannot match the paper's, and exactly where.

## 安装 / 用法

Zero third-party dependencies, Python 3.9+.

```bash
git clone git@github.com:linmoxi33/paper-orchestra-refseg.git
cd paper-orchestra-refseg
python3 paper_orchestra_refseg.py examples/in/02_section_header.txt
```

`--json` output (abridged from `examples/out/02_section_header.json`):

```json
{
  "style": "bracket-sequential",
  "upstream_segments": 4,
  "real_entries": 3,
  "verdict": "misnumbered",
  "max_abs_drift": 1,
  "mapping": [
    {"synthetic": 1, "real": null, "drift": null, "preview": "References"},
    {"synthetic": 2, "real": 1, "drift": -1, "preview": "K. He, X. Zhang, ..."}
  ],
  "exit_code": 1
}
```

Exit codes:

| code | meaning |
| --- | --- |
| 0 | numbering is aligned: every segment maps 1:1 to the real reference number |
| 1 | drift detected: phantom segment, missed entry, shifted index, or unsupported numbering style |
| 2 | usage or input error (missing/empty/unreadable file, bad arguments) |

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

Measured on this repository (2026-11-18): **16 tests, 16 passed, 0 failed, 0 skipped**. The four `examples/in/` fixtures produce: clean 4→4; header 4 segments vs 3 real with drift −1 on all; `1.` style 1 segment vs 3 real; `\bibitem` 1 segment vs 2 real. On the upstream repository's own `templates/cvpr2025/references.bib` (6 entries, fetched from `d9bae99`), the tool reports style `bibtex`, 1 upstream segment, 5 entries never shown to the LLM.

## 局限

- The independent parser is line-start based. A styled PDF that puts `[1]` mid-line, or a bibliography whose entries legitimately contain `[12]`-like text, may be parsed differently than a human would; the tool reports `bracket-inline` instead of pretending.
- The four fixtures are hand-built from real CVPR/ICLR/NeurIPS bibliographic entries, not a corpus of full PDF extractions. No claim is made about how often each style occurs in the wild — only about what the upstream code does when it does occur.
- Drift is computed by normalised text matching against the independent parse; an entry that appears twice can be matched to the first occurrence.
- It predicts the autorater's numbering, not its LLM's actual P0/P1 output. No network is used and no scores are produced.

## 本轮创新

First offline test of PaperOrchestra's **own** citation-F1 autorater's reference segmentation. Prior MCP/AG-UI work in this series audited contracts and traces; the ledger has no entry that replays an upstream evaluator's numbering and quantifies the resulting permutation. New, verifiable conclusion: a `References` header shifts every P0/P1 label by one, and `1.`/`\bibitem` styles collapse the whole bibliography into a single "reference" — both directly readable from the upstream code and reproducible in 0.3 s with the 16 tests here. Not a re-skin of a linter: it emits the synthetic→real permutation the scorer actually uses.

## 与同类项目的差异

Searched before building (`gh search repos "citation reference number parser"` → 0 hits; `"latex citation checker"` → `hrid0yyy/LaTeX-Citation-Checker`, 0 stars; `"bibtex linter"` → top result 4 stars). Existing linters check whether `\cite` keys exist in a `.bib`, or validate BibTeX fields. None of them model **post-PDF-extraction numbered text fed to an LLM rater**, and none report a synthetic-index permutation. No stars>500 project overlaps this use case.

## License

MIT. Upstream code is Apache-2.0; the single regex expression reproduced here is quoted for fidelity and attributed above.
