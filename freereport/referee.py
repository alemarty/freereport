"""Referee response mode: check, rank and answer the comments in referee reports.

1. Split the reports into individual comments (R1.1, R1.2, ... R2.1, ...).
2. For each reviewer, assess every comment against the manuscript: is it
   accurate, how serious is it, where in the paper is it, and a suggested
   reply (plus a concrete LaTeX edit when the source is given).
3. Write an overview with an ordered revision plan.

Outputs: a colour-coded "journal response" report (PDF, Markdown, JSON) and,
when a PDF of the paper is available, a copy with each comment highlighted.
"""

import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

import pymupdf

from . import prompts
from .backends import Backend, Usage
from .paper import Paper
from .pdf import normalise
from .report import _CSS
from .schema import (
    AssessmentList,
    CommentAssessment,
    RefereeComment,
    RefereeCommentList,
    RefereeOverview,
    strict_schema,
)

PRIORITY_ORDER = {"fatal": 0, "major": 1, "minor": 2, "trivial": 3}
PRIORITY_RGB = {  # highlight colours in the annotated PDF, and badges in the report
    "fatal": (0.86, 0.15, 0.15),
    "major": (0.96, 0.55, 0.10),
    "minor": (0.98, 0.85, 0.20),
    "trivial": (0.78, 0.82, 0.86),
}
ACCURACY_LABEL = {
    "correct": "Referee is right",
    "partly_correct": "Partly right",
    "incorrect": "Referee is wrong",
    "unclear": "Unclear",
}
ACCURACY_HEX = {"correct": "#1a7f37", "partly_correct": "#b54708", "incorrect": "#b42318", "unclear": "#59636e"}


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


# --- Reading the referee reports --------------------------------------------

def read_report(path: Path) -> str:
    """Plain text of a referee report: .pdf, .docx, .txt or .md."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    if suffix == ".docx":
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if suffix in {".txt", ".md", ".text", ""}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"{path.name}: referee reports must be .pdf, .docx, .txt or .md")


# --- Pipeline ---------------------------------------------------------------

@dataclass
class RefereeResult:
    paper: str
    model: str
    has_tex: bool
    comments: list[RefereeComment]
    assessments: dict[str, CommentAssessment]
    overview: RefereeOverview

    def ranked(self) -> list[tuple[RefereeComment, CommentAssessment]]:
        pairs = [(c, self.assessments[c.id]) for c in self.comments if c.id in self.assessments]
        return sorted(pairs, key=lambda ca: (PRIORITY_ORDER[ca[1].priority], ca[0].id))


def respond(paper: Paper, reports: list[Path], backend: Backend, log=print) -> RefereeResult:
    texts = "\n\n".join(
        f"===== REPORT {n} (file: {p.name}) =====\n{read_report(p)}" for n, p in enumerate(reports, 1)
    )

    log("Splitting the referee reports into comments...")
    comments = RefereeCommentList.model_validate(
        backend.ask("referee_split", prompts.REFEREE_SYSTEM, prompts.REFEREE_SPLIT.format(reports=texts),
                    strict_schema(RefereeCommentList))
    ).comments
    log(f"  {len(comments)} comment(s)")

    edit_rule = prompts.REFEREE_EDIT_TEX if paper.tex_lines else prompts.REFEREE_EDIT_NONE
    assessments: dict[str, CommentAssessment] = {}
    by_report: dict[str, list[RefereeComment]] = {}
    for c in comments:
        by_report.setdefault(c.id.split(".")[0], []).append(c)
    for report_id, group in by_report.items():
        reviewer = group[0].reviewer
        log(f"Assessing {reviewer} ({len(group)} comment(s))...")
        others = [c.model_dump() for c in comments if c.id.split(".")[0] != report_id]
        task = prompts.REFEREE_ASSESS.format(
            reviewer=reviewer,
            edit_rule=edit_rule,
            comments=json.dumps([c.model_dump() for c in group], indent=1),
            others=json.dumps(others, indent=1),
        )
        result = AssessmentList.model_validate(
            backend.ask(f"referee_assess_{report_id}", prompts.REFEREE_SYSTEM, task, strict_schema(AssessmentList))
        )
        assessments.update({a.id: a for a in result.assessments})

    log("Writing the overview and revision plan...")
    joined = [{"comment": c.model_dump(), "assessment": assessments[c.id].model_dump()}
              for c in comments if c.id in assessments]
    overview = RefereeOverview.model_validate(
        backend.ask("referee_overview", prompts.REFEREE_SYSTEM,
                    prompts.REFEREE_OVERVIEW.format(assessments=json.dumps(joined, indent=1)),
                    strict_schema(RefereeOverview))
    )
    return RefereeResult(paper.name, backend.model, bool(paper.tex_lines), comments, assessments, overview)


# --- Reports ----------------------------------------------------------------

def _counts(result: RefereeResult) -> str:
    ranked = result.ranked()
    by_priority = {p: sum(a.priority == p for _, a in ranked) for p in PRIORITY_ORDER}
    return ", ".join(f"{n} {p}" for p, n in by_priority.items() if n)


def _where(a: CommentAssessment) -> str:
    return ", ".join(x for x in (f"page {a.page}" if a.page else "", a.source_line) if x) or "whole paper"


def to_markdown(result: RefereeResult, usage: Usage) -> str:
    cost = usage.cost_usd(result.model)
    lines = [
        f"# Referee response: {result.paper}",
        "",
        f"*{date.today().isoformat()} · {len(result.comments)} comment(s): {_counts(result)}"
        + (f" · cost ≈ ${cost:.2f}" if cost is not None else "") + "*",
        "",
        "## Overview",
        "",
        result.overview.summary,
        "",
        "## Revision plan",
        "",
        *[f"{n}. {s.step} *({s.why})*" for n, s in enumerate(result.overview.plan, 1)],
        "",
        "## Comments, most serious first",
        "",
    ]
    for c, a in result.ranked():
        lines += [
            f"### {c.id} · {a.priority.upper()} · {ACCURACY_LABEL[a.accuracy]} · effort {a.effort}",
            "",
            f"*{c.reviewer}, about {_where(a)}*",
            "",
            *[f"> {line}" for line in c.text.splitlines() if line.strip()],
            "",
            f"**Assessment:** {a.evidence}",
            "",
        ]
        if a.quote:
            lines += [f"**In the paper:** \"{a.quote}\"", ""]
        if a.related:
            lines += [f"**Related comments:** {', '.join(a.related)}", ""]
        lines += [f"**Suggested response:** {a.suggested_response}", ""]
        if a.suggested_edit:
            lines += [f"**Suggested edit:** `{a.suggested_edit}`", ""]
    return "\n".join(lines)


_REFEREE_CSS = _CSS + """
.badge { font-weight: bold; padding: 1pt 4pt; }
.ref { background-color: #f7f7f9; border-left: 3px solid #9aa9b6; padding: 4pt 6pt; margin: 4pt 0; font-size: 9pt; }
.edit { font-family: monospace; font-size: 8.5pt; background-color: #eef6ee; padding: 3pt 5pt; }
"""


def to_html(result: RefereeResult, usage: Usage) -> str:
    cost = usage.cost_usd(result.model)
    sub = f"{date.today().isoformat()} · {len(result.comments)} comment(s): {_counts(result)}"
    if cost is not None:
        sub += f" · cost ≈ ${cost:.2f}"
    legend = " &#160; ".join(
        f'<span style="color:{_hex(rgb)}; font-weight:bold">■ {p.upper()}</span>' for p, rgb in PRIORITY_RGB.items()
    )
    out = [
        f"<h1>Referee response: {escape(result.paper)}</h1>",
        f'<p class="sub">{escape(sub)}</p>',
        f'<p class="meta">Priority: {legend}</p>',
        "<h2>Overview</h2>",
        f"<p>{escape(result.overview.summary)}</p>",
        "<h2>Revision plan</h2>",
        "<ol>",
        *[f"<li>{escape(s.step)} <i>({escape(s.why)})</i></li>" for s in result.overview.plan],
        "</ol>",
        "<h2>Comments, most serious first</h2>",
    ]
    for c, a in result.ranked():
        colour = _hex(PRIORITY_RGB[a.priority])
        out += [
            f'<h3 style="border-left: 5px solid {colour}; padding-left: 5pt">{escape(c.id)} '
            f'<span style="color:{colour}">{a.priority.upper()}</span> · '
            f'<span style="color:{ACCURACY_HEX[a.accuracy]}">{ACCURACY_LABEL[a.accuracy]}</span></h3>',
            f'<p class="meta">{escape(c.reviewer)} · about {escape(_where(a))} · effort {a.effort}</p>',
            f'<p class="ref">{escape(c.text)}</p>',
            f'<p><span class="label">Assessment:</span> {escape(a.evidence)}</p>',
        ]
        if a.quote:
            out.append(f'<p class="quote">{escape(a.quote)}</p>')
        if a.related:
            out.append(f'<p class="meta">Related comments: {escape(", ".join(a.related))}</p>')
        out.append(f'<p><span class="label">Suggested response:</span> {escape(a.suggested_response)}</p>')
        if a.suggested_edit:
            out.append(f'<p class="edit">{escape(a.suggested_edit)}</p>')
    return "<body>" + "\n".join(out) + "</body>"


def to_pdf(result: RefereeResult, usage: Usage, path: Path) -> None:
    story = pymupdf.Story(html=to_html(result, usage), user_css=_REFEREE_CSS)
    page = pymupdf.paper_rect("a4")
    body = page + (54, 54, -54, -60)
    writer = pymupdf.DocumentWriter(str(path))
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(body)
        story.draw(device)
        writer.end_page()
    writer.close()
    with pymupdf.open(path) as doc:
        for i, pg in enumerate(doc, 1):
            pg.insert_text((54, page.height - 30), f"FreeReport · referee response · {result.paper} · page {i} of {doc.page_count}",
                           fontsize=8, color=(0.45, 0.45, 0.45))
        doc.saveIncr()


def to_json(result: RefereeResult, usage: Usage) -> str:
    return json.dumps(
        {
            "paper": result.paper,
            "model": result.model,
            "comments": [c.model_dump() for c in result.comments],
            "assessments": [a.model_dump() for a in result.assessments.values()],
            "overview": result.overview.model_dump(),
            "usage": usage.calls,
            "cost_usd": usage.cost_usd(result.model),
        },
        indent=2,
    )


# --- Highlighted copy of the paper ------------------------------------------

def _find(page: pymupdf.Page, quote: str) -> list:
    """Locate a quote on a page: the whole quote, else its first and last 6 words."""
    hits = page.search_for(quote, quads=True)
    if hits:
        return hits
    words = normalise(quote).split()
    for chunk in (words[:6], words[-6:]):
        if len(chunk) >= 3:
            hits = page.search_for(" ".join(chunk), quads=True)
            if hits:
                return hits
    return []


def highlight_pdf(result: RefereeResult, pdf_path: Path, out_path: Path) -> int:
    """Copy of the paper with each located comment highlighted in its priority colour,
    with the comment, assessment and suggested response in the note. Returns how many were placed."""
    placed = 0
    with pymupdf.open(pdf_path) as doc:
        unplaced = []
        for c, a in result.ranked():
            pages = [a.page - 1] if 0 < a.page <= doc.page_count else []
            pages += [i for i in range(doc.page_count) if i not in pages]  # fall back to searching everywhere
            hits, where = [], None
            if a.quote:
                for i in pages:
                    hits = _find(doc[i], a.quote)
                    if hits:
                        where = doc[i]
                        break
            note = (f"{c.id} [{a.priority.upper()} · {ACCURACY_LABEL[a.accuracy]}]\n\n{c.reviewer}: {c.text}\n\n"
                    f"Assessment: {a.evidence}\n\nSuggested response: {a.suggested_response}")
            if where is None:
                unplaced.append((c, a, note))
                continue
            annot = where.add_highlight_annot(hits)
            annot.set_colors(stroke=PRIORITY_RGB[a.priority])
            annot.set_info(title=f"{c.id} · {a.priority}", content=note)
            annot.update()
            placed += 1
        # General comments (or quotes not found) go as sticky notes in the margin of their page or page 1.
        for n, (c, a, note) in enumerate(unplaced):
            pg = doc[a.page - 1] if 0 < a.page <= doc.page_count else doc[0]
            annot = pg.add_text_annot((pg.rect.width - 24, 30 + 22 * (n % 30)), note, icon="Comment")
            annot.set_colors(stroke=PRIORITY_RGB[a.priority])
            annot.set_info(title=f"{c.id} · {a.priority}")
            annot.update()
        doc.save(out_path)
    return placed
