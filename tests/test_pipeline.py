"""Runs the whole pipeline on the sample paper with a fake model (no API cost)."""

from pathlib import Path

import pymupdf

from freereport.backends import Usage
from freereport.paper import load
from freereport.pdf import page_texts, quote_match
from freereport.pipeline import Options, audit
from freereport.report import to_json, to_markdown, to_pdf
from freereport.schema import FindingList, Synthesis, strict_schema

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_paper.pdf"


class FakeUsage:
    input_tokens, output_tokens = 1000, 200
    cache_creation_input_tokens, cache_read_input_tokens = 0, 0


def finding(id, category, quote, problem):
    return {
        "id": id, "category": category, "page": 1, "source_line": "", "location": "x", "quote": quote,
        "problem": problem, "check": "c", "suggested_fix": "f", "severity": "major", "confidence": "high",
    }


class FakeBackend:
    model = "claude-opus-5-5"

    def __init__(self):
        self.usage = Usage()
        self.tasks = {}

    def ask(self, label, system, task, schema):
        self.usage.add(label, FakeUsage())
        self.tasks[label] = task
        label = label.removesuffix("_part1")  # --deepdive checks in two halves; the second finds nothing new
        if label.endswith("_part2"):
            return {"findings": [finding("M1", "math", "(a + b)^2 = a^2 + b^2", "Same id from the other half")]
                    if label.startswith("adversary") else []}
        if label == "adversary":
            return {"findings": [finding("M1", "math", "(a + b)^2 = a^2 + b^2", "Missing 2ab term")]}
        if label == "adversary_blindspot":
            return {"findings": [finding("M2", "logic", "Squaring the combined effect", "Assumes a and b are scalars")]}
        if label == "data_auditor_blindspot":
            return {"findings": []}
        if label.startswith("council_") and label != "council_executive":
            return {"points": [{"point": f"{label} point", "page": 0}]}
        if label == "council_executive":
            fit = "Good fit, shorten the intro." if "journal_fit: 2-4" in task else ""
            return {"verdict": "Fix the errors first.", "action_plan": [{"step": "Fix Eq. 3", "why": "critical"}],
                    "journal_fit": fit}
        if label == "data_auditor":
            return {"findings": [
                finding("D1", "data", "N = 412 students", "Table 1 totals 400, not 412"),
                finding("D2", "data", "this sentence is invented", "Hallucinated"),
                finding("D3", "data", "(a + b)^2 = a^2 + b^2", "Same as M1"),
            ]}
        return {
            "verdicts": [
                {"id": "M1", "keep": True, "reason": "ok", "severity": "critical", "confidence": "high", "duplicate_of": ""},
                {"id": "D1", "keep": True, "reason": "ok", "severity": "major", "confidence": "high", "duplicate_of": ""},
                {"id": "D2", "keep": False, "reason": "not in paper", "severity": "minor", "confidence": "low", "duplicate_of": ""},
                {"id": "D3", "keep": False, "reason": "dup", "severity": "major", "confidence": "high", "duplicate_of": "M1"},
            ],
            "summary": "Two real problems.",
        }


def test_audit_end_to_end():
    backend = FakeBackend()
    result = audit(load(SAMPLE), backend, log=lambda *_: None)
    assert [f.id for f, _ in result.kept()] == ["M1", "D1"]
    assert result.quote_scores["D1"] == 1.0
    assert result.quote_scores["D2"] < 0.8
    md = to_markdown(result, backend.usage)
    assert "Missing 2ab term" in md and "Rejected candidates" in md
    assert "**D3** → same as **issue 1**" in md and "D3** (" not in md  # merged, not listed as rejected
    assert '"cost_usd"' in to_json(result, backend.usage)


def test_quote_match_tolerates_spacing():
    pages = page_texts(SAMPLE)
    assert quote_match("Tutoring   raises exam\nscores", pages, 1) == 1.0


def test_strict_schema_requires_every_field():
    for model in (FindingList, Synthesis):
        schema = strict_schema(model)
        for node in [schema, *schema.get("$defs", {}).values()]:
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_optional_modes():
    backend = FakeBackend()
    paper = load(SAMPLE)
    paper.pages = paper.pages * 2  # two pages, so --deepdive can check it in two halves
    result = audit(paper, backend, Options(deepdive=True, blindspot=True, council=True), log=lambda *_: None)
    labels = [c["agent"] for c in backend.usage.calls]
    assert labels[:2] == ["adversary_part1", "adversary_part2"] and "adversary" not in labels
    assert "PART 2 OF 2" in backend.tasks["adversary_part2"] and "M1b" in [f.id for f in result.candidates]
    assert labels.count("adversary_blindspot") == 1
    assert sum(l.startswith("council_") for l in labels) == 6
    assert "M2" in [f.id for f in result.candidates]
    md = to_markdown(result, backend.usage)
    assert "## Verdict" in md and "Fix Eq. 3" in md and "*Outsider:* council_Outsider point" in md
    # The reviewers come as one list, not a section per role, and the verdict comes before the issues
    assert "### Outsider" not in md and md.count("## Reviewer notes") == 1
    assert md.index("## Verdict") < md.index("## Revision plan") < md.index("## Issues") < md.index("## Reviewer notes")


def test_pdf_report(tmp_path):
    backend = FakeBackend()
    result = audit(load(SAMPLE), backend, Options(council=True, blindspot=True), log=lambda *_: None)
    out = tmp_path / "report.pdf"
    to_pdf(result, backend.usage, out)
    text = "".join(p.get_text() for p in pymupdf.open(out))
    assert "sample_paper.pdf" in text and "FreeReport audit" in text
    assert "Missing 2ab term" in text and "Merged duplicates" in text and "Verdict" in text and "Reviewer notes" in text
    assert "page 1 of" in text


def test_quick_cannot_be_combined_with_extra_modes(capsys):
    import pytest
    from freereport import cli
    with pytest.raises(SystemExit):
        cli.main(["check", "x.pdf", "--quick", "--council"])
    assert "--quick is the baseline run" in capsys.readouterr().err


def test_journal_reaches_every_council_member_and_the_report():
    backend = FakeBackend()
    result = audit(load(SAMPLE), backend, Options(council=True, journal="Nature Communications"), log=lambda *_: None)
    council_tasks = [t for label, t in backend.tasks.items() if label.startswith("council_")]
    assert len(council_tasks) == 6 and all("Nature Communications" in t for t in council_tasks)
    md = to_markdown(result, backend.usage)
    assert "## Fit with Nature Communications\n\nGood fit, shorten the intro." in md
    assert md.index("## Fit with") < md.index("## Issues")


def test_journal_turns_on_the_council(monkeypatch):
    from freereport import cli
    seen = {}
    monkeypatch.setattr(cli, "load", lambda p: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(cli, "Options", lambda **kw: seen.update(kw) or Options(**kw))
    cli.main(["check", "x.pdf", "--journal", "Ecology Letters"])
    assert seen["council"] is True and seen["journal"] == "Ecology Letters"


def test_pdf_is_laid_out_again_when_text_runs_off_a_page(tmp_path, monkeypatch):
    """PyMuPDF can report a page done after running text past its bottom edge, and
    that text is lost; the report must notice and lay the pages out again."""
    from freereport import report
    real, heights = pymupdf.Story, []

    class Story(real):
        def place(self, where):
            more, filled = super().place(where)
            heights.append(where[3])
            if len(heights) == 1:  # first layout: pretend the text overflowed the page
                filled = (filled[0], filled[1], filled[2], 1035.0)
            return more, filled

    monkeypatch.setattr(report.pymupdf, "Story", Story)
    backend = FakeBackend()
    result = audit(load(SAMPLE), backend, Options(), log=lambda *_: None)
    to_pdf(result, backend.usage, tmp_path / "report.pdf")
    assert len(heights) == 2 and heights[1] < heights[0]
    assert "Missing 2ab term" in "".join(p.get_text() for p in pymupdf.open(tmp_path / "report.pdf"))
