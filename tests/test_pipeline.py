"""Runs the whole pipeline on the sample paper with a fake model (no API cost)."""

from pathlib import Path

from freereport.backends import Usage
from freereport.paper import load
from freereport.pdf import page_texts, quote_match
from freereport.pipeline import Options, audit
from freereport.report import to_json, to_markdown
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

    def ask(self, label, system, task, schema):
        self.usage.add(label, FakeUsage())
        if label == "adversary":
            return {"findings": [finding("M1", "math", "(a + b)^2 = a^2 + b^2", "Missing 2ab term")]}
        if label == "adversary_blindspot":
            return {"findings": [finding("M2", "logic", "Squaring the combined effect", "Assumes a and b are scalars")]}
        if label == "data_auditor_blindspot":
            return {"findings": []}
        if label.startswith("council_") and label != "council_executive":
            return {"points": [{"point": f"{label} point", "page": 0}]}
        if label == "council_executive":
            return {"verdict": "Fix the errors first.", "action_plan": [{"step": "Fix Eq. 3", "why": "critical"}]}
        if label == "data_auditor":
            return {"findings": [
                finding("D1", "data", "N = 412 students", "Table 1 totals 400, not 412"),
                finding("D2", "data", "this sentence is invented", "Hallucinated"),
            ]}
        return {
            "verdicts": [
                {"id": "M1", "keep": True, "reason": "ok", "severity": "critical", "confidence": "high"},
                {"id": "D1", "keep": True, "reason": "ok", "severity": "major", "confidence": "high"},
                {"id": "D2", "keep": False, "reason": "not in paper", "severity": "minor", "confidence": "low"},
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
    result = audit(load(SAMPLE), backend, Options(deepdive=True, blindspot=True, council=True), log=lambda *_: None)
    labels = [c["agent"] for c in backend.usage.calls]
    assert labels.count("adversary_blindspot") == 1
    assert sum(l.startswith("council_") for l in labels) == 5
    assert "M2" in [f.id for f in result.candidates]
    md = to_markdown(result, backend.usage)
    assert "Council review" in md and "Fix Eq. 3" in md and "council_Outsider point" in md
