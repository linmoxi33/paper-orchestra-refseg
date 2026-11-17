# Copyright (c) 2026 linmoxi33
# SPDX-License-Identifier: MIT
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "paper_orchestra_refseg.py"
SAMPLES = ROOT / "examples" / "in"
GOLDEN = ROOT / "examples" / "out"
sys.path.insert(0, str(ROOT))

import paper_orchestra_refseg as refseg  # noqa: E402


def run(path, *extra):
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path), *extra],
                          capture_output=True, text=True, cwd=str(ROOT))
    return proc


class CleanInputTest(unittest.TestCase):
    def test_clean_bracket_exits_zero(self):
        proc = run(SAMPLES / "01_cvpr_bracket.txt", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["clean"])

    def test_clean_mapping_is_identity(self):
        report = json.loads(run(SAMPLES / "01_cvpr_bracket.txt", "--json").stdout)
        self.assertEqual([(m["synthetic"], m["real"]) for m in report["mapping"]],
                         [(1, 1), (2, 2), (3, 3), (4, 4)])
        self.assertEqual(report["max_abs_drift"], 0)


class DriftTest(unittest.TestCase):
    def test_header_is_phantom_and_shifts_by_minus_one(self):
        proc = run(SAMPLES / "02_section_header.txt", "--json")
        report = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["phantom_segments"], ["References"])
        self.assertEqual([m["drift"] for m in report["mapping"]], [None, -1, -1, -1])

    def test_missing_marker_shifts_later_entries(self):
        report = refseg.audit("[1] Alpha beta gamma delta epsilon.\n[3] Zeta eta theta iota kappa.\n")
        self.assertEqual(report["verdict"], "misnumbered")
        self.assertEqual(report["mapping"][1]["real"], 3)
        self.assertEqual(report["mapping"][1]["drift"], 1)
        self.assertEqual(report["exit_code"], 1)

    def test_duplicate_marker_is_detected(self):
        report = refseg.audit("[1] Alpha beta gamma delta epsilon.\n[1] Zeta eta theta iota kappa.\n")
        self.assertEqual(report["mapping"][1]["drift"], -1)
        self.assertEqual(report["verdict"], "misnumbered")


class CollapseTest(unittest.TestCase):
    def test_numbered_dot_collapses_to_one_segment(self):
        proc = run(SAMPLES / "03_numbered_dot.txt", "--json")
        report = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(report["style"], "numbered-dot")
        self.assertEqual(report["upstream_segments"], 1)
        self.assertEqual(report["real_entries"], 3)
        self.assertEqual(report["verdict"], "collapsed")
        self.assertEqual(len(report["missed_entries"]), 2)

    def test_bibitem_is_never_split(self):
        report = refseg.audit((SAMPLES / "04_bibitem.tex").read_text())
        self.assertEqual(report["style"], "bibitem")
        self.assertEqual(report["upstream_segments"], 1)
        self.assertEqual(report["verdict"], "collapsed")

    def test_bibtex_entries_collapse_to_one_segment(self):
        raw = ("@String(CVPR = {CVPR})\n"
               "@article{he2016resnet, title={Deep residual learning}, year={2016}}\n"
               "@inproceedings{kingma2015adam, title={Adam}, year={2015}}\n")
        report = refseg.audit(raw)
        self.assertEqual(report["style"], "bibtex")
        self.assertEqual(report["real_entries"], 2)
        self.assertEqual(report["upstream_segments"], 1)
        self.assertEqual(report["verdict"], "collapsed")

    def test_bibitem_keys_are_extracted(self):
        _style, entries = refseg.parse_entries((SAMPLES / "04_bibitem.tex").read_text())
        self.assertEqual([e["key"] for e in entries], ["he2016resnet", "kingma2015adam"])


class ContractTest(unittest.TestCase):
    def test_json_parses_and_has_contract_fields(self):
        proc = run(SAMPLES / "03_numbered_dot.txt", "--json")
        data = json.loads(proc.stdout)  # must be valid JSON on stdout
        for key in ("style", "upstream_segments", "real_entries", "mapping",
                    "verdict", "clean", "exit_code", "max_abs_drift"):
            self.assertIn(key, data)
        self.assertEqual(data["exit_code"], proc.returncode)

    def test_missing_file_exits_two(self):
        proc = run("/tmp/definitely-not-a-refs-file-424242.txt")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no such file", proc.stderr)

    def test_empty_file_exits_two(self):
        empty = ROOT / "tests" / "_empty.txt"
        empty.write_text("   \n\n")
        try:
            proc = run(empty)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("empty", proc.stderr)
        finally:
            empty.unlink()

    def test_no_arguments_exits_two(self):
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


class FidelityTest(unittest.TestCase):
    def test_upstream_segmentation_reproduces_re_split(self):
        raw = "References\n[1] Alpha  beta\n gamma.\n[2] Zeta eta theta.\n"
        expected = [s.strip().replace("\n", " ").replace("  ", " ")
                    for s in re.split(r"\[\d+\]", raw) if s.strip()]
        self.assertEqual(refseg.upstream_segments(raw), expected)

    def test_segments_do_not_drop_inline_numbers(self):
        raw = "[1] Alpha beta gamma delta epsilon.\n[2] Zeta eta theta iota kappa.\n"
        self.assertEqual(len(refseg.upstream_segments(raw)), 2)

    def test_golden_outputs_match_tool(self):
        for src in sorted(SAMPLES.glob("*")):
            golden_path = GOLDEN / (src.stem.split(".")[0] + ".json")
            expected = json.loads(golden_path.read_text())
            actual = refseg.audit(src.read_text(), str(src))
            for key in ("style", "upstream_segments", "real_entries",
                        "verdict", "clean", "max_abs_drift", "exit_code"):
                self.assertEqual(actual[key], expected[key], "%s/%s" % (src.name, key))


if __name__ == "__main__":
    unittest.main()
