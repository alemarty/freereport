"""Resubmission check, end to end with a fake model (no API cost)."""

import json
from pathlib import Path

import pymupdf

from freereport import resubmit
from freereport.backends import Usage
from freereport.paper import load

ROOT = Path(__file__).parents[1] / "examples"
REPORTS = sorted((ROOT / "referee_reports").glob("*.txt"))


class FakeUsage:
    input_tokens, output_tokens = 1000, 200
    cache_creation_input_tokens, cache_read_input_tokens = 0, 0


def status(id, st, priority, quote, remaining=""):
    return {"id": id, "status": st, "priority": priority, "evidence": f"evidence {id}", "page": 1 if quote else 0,
            "source_line": "", "quote": quote, "remaining": remaining, "response": f"We thank the referee ({id})."}


class FakeBackend:
    model = "claude-opus-5-5"

    def __init__(self):
        self.usage = Usage()
        self.tasks = {}

    def ask(self, label, system, task, schema):
        self.usage.add(label, FakeUsage())
        self.tasks[label] = task
        if label == "resubmit_split":
            return {"comments": [
                {"id": "R1.1", "reviewer": "Reviewer 1", "text": "Equation (3) is wrong."},
                {"id": "R1.2", "reviewer": "Reviewer 1", "text": "Cluster the standard errors."},
                {"id": "R2.1", "reviewer": "Report 2", "text": "0.43 vs 0.34."},
            ]}
        if label == "resubmit_status_R1":
            return {"statuses": [
                status("R1.1", "addressed", "fatal", "(a + b)^2 = a^2 + 2ab + b^2"),
                status("R1.2", "not_addressed", "major", "", remaining="Cluster at school level."),
            ]}
        if label == "resubmit_status_R2":
            return {"statuses": [status("R2.1", "partly_addressed", "major", "The estimated effect of 0.43 is large")]}
        return {"summary": "Not ready: R1.2 is open.", "letter_opening": "Dear Editor, thank you.",
                "unrequested": [{"change": "C3", "what": "Standard error changed from 0.08 to 0.09.", "note": "Explain why."}]}


def test_compare_pdf_ignores_unchanged_text():
    changes, basis = resubmit.compare(load(ROOT / "sample_paper.pdf"), load(ROOT / "sample_paper_revised.pdf"))
    assert basis.startswith("PDF")
    joined = resubmit.changes_text(changes)
    assert "N = 412" in joined and "N = 400" in joined and "2ab" in joined and "0.09" in joined
    assert "Tutoring 210" not in joined  # Table 1 did not change
    assert resubmit.changes_text([]).startswith("(no differences")


def test_compare_tex_line_by_line(tmp_path):
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    (tmp_path / "a" / "main.tex").write_text("\\documentclass{article}\nN = 412.\nSame line.\nOld remark. % note\n")
    (tmp_path / "b" / "main.tex").write_text("\\documentclass{article}\nN = 400.\nSame line.\nNew paragraph.\n")
    changes, basis = resubmit.compare(load(tmp_path / "a" / "main.tex"), load(tmp_path / "b" / "main.tex"))
    assert basis.startswith("LaTeX")
    assert [(c.old_where, c.new_where) for c in changes] == [("main.tex:2", "main.tex:2"), ("main.tex:4", "main.tex:4")]
    assert changes[1].old == "Old remark."  # comments stripped


def test_resubmit_end_to_end(tmp_path):
    original, revised = load(ROOT / "sample_paper.pdf"), load(ROOT / "sample_paper_revised.pdf")
    backend = FakeBackend()
    result = resubmit.check_revision(original, revised, REPORTS, backend, log=lambda *_: None)

    # the model sees the change list, not the original paper
    assert "N = 412" in backend.tasks["resubmit_status_R1"] and "[C1]" in backend.tasks["resubmit_overview"]
    # not addressed first, then partly, then addressed
    assert [c.id for c, _ in result.ranked()] == ["R1.2", "R2.1", "R1.1"]
    assert result.counts() == {"not_addressed": 1, "partly_addressed": 1, "addressed": 1}

    md = resubmit.to_markdown(result, backend.usage)
    assert md.index("R1.2 · NOT ADDRESSED") < md.index("R1.1 · ADDRESSED")
    assert "Changes no referee asked for" in md and "Still to do:** Cluster" in md

    letter = resubmit.letter(result)
    assert letter.index("## Reviewer 1") < letter.index("## Report 2")
    assert letter.index("Comment R1.1") < letter.index("Comment R1.2")  # referees' own order

    out = tmp_path / "resub.pdf"
    resubmit.to_pdf(result, backend.usage, out)
    text = "".join(p.get_text() for p in pymupdf.open(out))
    assert "Resubmission check" in text and "Draft response letter" in text

    marked = tmp_path / "marked.pdf"
    assert resubmit.highlight_pdf(result, revised.pdf_path, marked) == 2  # R1.2 has no quote -> sticky note
    doc = pymupdf.open(marked)
    assert len([a for p in doc for a in p.annots()]) == 3


def test_resubmit_reuses_previous_ids(tmp_path):
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps({
        "comments": [{"id": "R1.1", "reviewer": "Reviewer 1", "text": "Equation (3) is wrong."}],
        "assessments": [{"id": "R1.1", "accuracy": "correct", "priority": "fatal", "evidence": "e",
                         "suggested_response": "We fixed it."}],
    }))
    backend = FakeBackend()
    result = resubmit.check_revision(load(ROOT / "sample_paper.pdf"), load(ROOT / "sample_paper_revised.pdf"),
                                     [], backend, previous=previous, log=lambda *_: None)
    assert "resubmit_split" not in backend.tasks  # no new split: same ids as before
    assert '"earlier"' in backend.tasks["resubmit_status_R1"] and "We fixed it." in backend.tasks["resubmit_status_R1"]
    assert result.reused_previous and [c.id for c in result.comments] == ["R1.1"]


def test_latex_letter_escapes_and_marks_todo():
    backend = FakeBackend()
    result = resubmit.check_revision(load(ROOT / "sample_paper.pdf"), load(ROOT / "sample_paper_revised.pdf"),
                                     REPORTS, backend, log=lambda *_: None)
    result.statuses["R1.2"].response = "Clustered SEs: 50% & p < 0.001, β ≈ 0.4. [TO DO: re-run table_2]"
    tex = resubmit.latex_letter(result)
    assert tex.startswith("\\documentclass") and tex.rstrip().endswith("\\end{document}")
    assert r"50\% \& p \textless{} 0.001, $\beta$ $\approx$ 0.4." in tex
    assert r"\textcolor{red}{\textbf{[TO DO: re-run table\_2]}}" in tex
    assert tex.index(r"\subsection*{Reviewer 1}") < tex.index(r"\subsection*{Report 2}")
