"""Referee response mode, end to end with a fake model (no API cost)."""

from pathlib import Path

import pymupdf

from freereport import referee
from freereport.backends import Usage
from freereport.paper import load

ROOT = Path(__file__).parents[1] / "examples"
REPORTS = sorted((ROOT / "referee_reports").glob("*.txt"))


class FakeUsage:
    input_tokens, output_tokens = 1000, 200
    cache_creation_input_tokens, cache_read_input_tokens = 0, 0


def assessment(id, accuracy, priority, quote, related=()):
    return {
        "id": id, "accuracy": accuracy, "evidence": f"evidence {id}", "priority": priority, "effort": "small",
        "page": 1 if quote else 0, "source_line": "", "quote": quote, "related": list(related),
        "suggested_response": f"We thank the referee ({id}).", "suggested_edit": "",
    }


class FakeBackend:
    model = "claude-opus-5-5"

    def __init__(self):
        self.usage = Usage()
        self.tasks = {}

    def ask(self, label, system, task, schema):
        self.usage.add(label, FakeUsage())
        self.tasks[label] = task
        if label == "referee_split":
            return {"comments": [
                {"id": "R1.1", "reviewer": "Reviewer 1", "text": "Equation (3) is wrong."},
                {"id": "R1.2", "reviewer": "Reviewer 1", "text": "Typo in Table 2."},
                {"id": "R2.1", "reviewer": "Report 2", "text": "0.43 vs 0.34."},
                {"id": "R2.2", "reviewer": "Report 2", "text": "Never states random assignment."},
            ]}
        if label == "referee_assess_R1":
            return {"assessments": [
                assessment("R1.1", "correct", "fatal", "(a + b)^2 = a^2 + b^2"),
                assessment("R1.2", "correct", "trivial", ""),
            ]}
        if label == "referee_assess_R2":
            return {"assessments": [
                assessment("R2.1", "partly_correct", "major", "The estimated effect of 0.34 is large and significant."),
                assessment("R2.2", "incorrect", "minor", "We study N = 412 students randomly assigned", related=["R1.1"]),
            ]}
        return {"summary": "Fix Eq. 3 first.", "plan": [{"step": "Correct Equation (3)", "why": "R1.1, fatal"}]}


def test_respond_end_to_end(tmp_path):
    paper = load(ROOT / "sample_paper.pdf")
    backend = FakeBackend()
    result = referee.respond(paper, REPORTS, backend, log=lambda *_: None)

    # both reports reach the splitter; each reviewer is assessed separately and sees the other's comments
    assert "Reviewer 1" in backend.tasks["referee_split"] and "Report on" in backend.tasks["referee_split"]
    assert "R2.1" in backend.tasks["referee_assess_R1"] and "R1.1" not in backend.tasks["referee_assess_R1"].split("FOR CROSS-CHECKING")[1]
    assert "leave empty (no LaTeX" in backend.tasks["referee_assess_R1"]

    # ranked fatal -> trivial
    assert [c.id for c, _ in result.ranked()] == ["R1.1", "R2.1", "R2.2", "R1.2"]

    md = referee.to_markdown(result, backend.usage)
    assert md.index("R1.1 · FATAL") < md.index("R1.2 · TRIVIAL")
    assert "Referee is wrong" in md

    out = tmp_path / "resp.pdf"
    referee.to_pdf(result, backend.usage, out)
    text = "".join(p.get_text() for p in pymupdf.open(out))
    assert "Referee response: sample_paper.pdf" in text and "Revision plan" in text

    marked = tmp_path / "highlighted.pdf"
    placed = referee.highlight_pdf(result, paper.pdf_path, marked)
    assert placed == 3  # R1.2 has no quote -> sticky note instead
    doc = pymupdf.open(marked)
    notes = [(a.type[1], a.info["content"]) for p in doc for a in p.annots()]
    assert len(notes) == 4
    assert ("Highlight", ) == tuple({t for t, c in notes if c.startswith("R1.1")})
    assert any(t == "Text" and c.startswith("R1.2") for t, c in notes)


def test_read_report_docx(tmp_path):
    import zipfile
    docx = tmp_path / "r.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Comment one.</w:t></w:r></w:p>"
                                          "<w:p><w:r><w:t>Comment two.</w:t></w:r></w:p></w:body></w:document>")
    assert referee.read_report(docx).split() == ["Comment", "one.", "Comment", "two."]
