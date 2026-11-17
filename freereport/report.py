"""Turns an AuditResult into Markdown, PDF and JSON reports."""

import json
from datetime import date
from html import escape
from pathlib import Path

import pymupdf

from .backends import Usage
from .pipeline import QUOTE_THRESHOLD, AuditResult

_ORDER = {"critical": 0, "major": 1, "minor": 2}


def _sections(result: AuditResult):
    """Kept issues (sorted), merged duplicates and rejected candidates."""
    kept = sorted(result.kept(), key=lambda fv: (_ORDER[fv[1].severity], fv[0].page))
    by_id = {f.id: f for f in result.candidates}
    dropped = [(by_id.get(v.id), v) for v in result.synthesis.verdicts if not v.keep]
    merged = [(f, v) for f, v in dropped if v.duplicate_of]
    rejected = [(f, v) for f, v in dropped if not v.duplicate_of]
    return kept, merged, rejected


def _where(f) -> list[str]:
    return [x for x in (f"page {f.page}" if f.page else "", f.source_line, f.location) if x]


def _quote_ok(result: AuditResult, f) -> bool:
    return result.quote_scores.get(f.id, 0.0) >= QUOTE_THRESHOLD


def _subtitle(result: AuditResult, kept) -> str:
    return " · ".join([f"FreeReport audit of {result.paper}" if result.title else "FreeReport audit",
                       date.today().strftime("%d %B %Y"), f"{len(kept)} comment(s)"])


def _about(result: AuditResult, usage: Usage, kept, merged, rejected) -> str:
    """Run details for the end of the report."""
    cost = usage.cost_usd(result.model)
    parts = [
        f"Model {result.model}.",
        f"{len(kept)} comment(s) reported, {len(merged)} repeat(s) merged into them, {len(rejected)} candidate(s) rejected on re-checking.",
        *([f"Cost about ${cost:.2f}."] if cost is not None else []),
        "Each reviewer's own notes, the rejected candidates and the final checker's notes are in the .json file.",
    ]
    return " ".join(parts)


def _name(result: AuditResult) -> str:
    return result.title or result.paper


def _headline(f) -> str:
    return f.title or f.problem


def _numbers(kept, merged) -> dict[str, int]:
    """Comment number for each finding id, repeats included (they point at their kept twin)."""
    number = {f.id: n for n, (f, _) in enumerate(kept, 1)}
    for _, v in merged:
        if v.duplicate_of in number:
            number[v.id] = number[v.duplicate_of]
    return number


def _refs(ids: list[str], number: dict[str, int]) -> str:
    """' (see #3, #7)' for the ids that are in the report, else ''."""
    found = sorted({number[i] for i in ids if i in number})
    return f" (see {', '.join(f'#{n}' for n in found)})" if found else ""


def _also_at(f, merged) -> str:
    """Where else the same problem was found: 'page 21; page 27, Table 3'."""
    places = [", ".join(_where(d)) for d, v in merged if d and v.duplicate_of == f.id]
    return "; ".join(p for p in places if p)


def _meta(f, v, merged) -> str:
    also = _also_at(f, merged)
    return " · ".join([v.severity.upper(), f.category, *_where(f), *([f"also at {also}"] if also else [])])


_COUNCIL_NOTE = ("From the reviewer council: five reviewers with different angles, merged by a chair into one view. "
                 "These are opinions on the paper as a whole; the detailed comments below are checked errors.")


def to_markdown(result: AuditResult, usage: Usage) -> str:
    kept, merged, rejected = _sections(result)
    number = _numbers(kept, merged)
    lines = [f"# {_name(result)}", "", f"*{_subtitle(result, kept)}*", "", result.synthesis.summary, ""]
    if result.decision:
        d = result.decision
        lines += ["## Overall feedback", "", f"*{_COUNCIL_NOTE}*", "", f"**Verdict.** {d.verdict}", ""]
        if d.journal_fit:
            lines += [f"### Fit with {result.options.journal or 'the target journal'}", "", d.journal_fit, ""]
        for c in d.main_concerns:
            lines += [f"### {c.title or 'Concern'}", "", c.point + _refs(c.issues, number), ""]
            lines += [f"*Raised by {c.support}*", ""] if c.support else []
        if d.disagreements:
            lines += ["### Where the reviewers disagreed", ""]
            lines += [f"- {f'**{c.title}.** ' if c.title else ''}{c.point}{_refs(c.issues, number)}" for c in d.disagreements]
            lines.append("")
        if d.side_notes:
            lines += ["### Raised, but not a main concern", ""]
            lines += [f"- {c.point}{_refs(c.issues, number)}" for c in d.side_notes]
            lines.append("")
        lines += ["### Revision plan", ""]
        lines += [f"{n}. {s.step}{_refs(s.issues, number)}" for n, s in enumerate(d.action_plan, 1)]
        lines.append("")
    lines += ["## Detailed comments", "", "*Each comment quotes the paper so you can check it yourself. "
              "Treat them as leads to verify, not verdicts.*", ""]
    if not kept:
        lines += ["No issues survived the final check.", ""]
    for n, (f, v) in enumerate(kept, 1):
        flag = "" if _quote_ok(result, f) else " · ⚠️ quote not found in the paper's text, check the location by hand"
        lines += [
            f"### #{n} {_headline(f)}",
            "",
            f"*{_meta(f, v, merged)}*{flag}",
            "",
            f"> {f.quote}",
            "",
            *([f.problem, ""] if f.title else []),
            f"**Check:** {f.check}",
            "",
            f"**Suggested fix:** {f.suggested_fix}",
            "",
        ]
    lines += ["## About this report", "", _about(result, usage, kept, merged, rejected), ""]
    return "\n".join(lines)


# --- PDF -------------------------------------------------------------------

_CSS = """
* { font-family: sans-serif; }
body { font-size: 10pt; line-height: 1.4; color: #1f2328; }
h1 { font-size: 18pt; margin: 0 0 2pt 0; color: #0b3d5c; }
h2 { font-size: 13pt; margin: 16pt 0 6pt 0; color: #0b3d5c; border-bottom: 1px solid #c8d3dc; }
h3 { font-size: 11pt; margin: 12pt 0 3pt 0; }
p { margin: 3pt 0; }
.sub { color: #59636e; font-size: 9pt; margin-bottom: 8pt; }
.note { color: #59636e; font-size: 9pt; font-style: italic; }
.meta { font-size: 9pt; color: #59636e; }
.critical { color: #b42318; font-weight: bold; }
.major { color: #b54708; font-weight: bold; }
.minor { color: #3b6e2f; font-weight: bold; }
.warn { color: #b42318; font-size: 9pt; }
.quote { background-color: #f3f5f7; border-left: 3px solid #9aa9b6; padding: 4pt 6pt; margin: 4pt 0;
         font-family: monospace; font-size: 9pt; }
.label { font-weight: bold; }
.item { margin: 2pt 0 2pt 10pt; }
"""

# The audit report's own look: calm, roomy, close to a referee report.
_AUDIT_CSS = _CSS + """
body { font-size: 10.5pt; line-height: 1.5; }
h1 { font-size: 20pt; color: #111827; margin-bottom: 4pt; }
h2 { font-size: 14pt; color: #111827; border-bottom: none; margin: 22pt 0 6pt 0; }
h3 { font-size: 11.5pt; color: #111827; margin: 14pt 0 3pt 0; }
.lead { margin: 8pt 0 4pt 0; }
.panel { border: 1px solid #e5e7eb; padding: 8pt 12pt; margin: 6pt 0; }
.comment { border-top: 1px solid #e5e7eb; padding-top: 2pt; margin-top: 12pt; }
.excerpt { font-style: italic; color: #374151; border-left: 3px solid #d1d5db; padding: 2pt 8pt; margin: 5pt 0; }
.small { font-size: 9pt; color: #4b5563; }
.support { font-size: 8.5pt; color: #6b7280; margin-top: 1pt; }
"""


def _p(label: str, text: str) -> str:
    return f'<p><span class="label">{escape(label)}</span> {escape(text)}</p>'


def to_html(result: AuditResult, usage: Usage) -> str:
    kept, merged, rejected = _sections(result)
    number = _numbers(kept, merged)
    out = [
        f"<h1>{escape(_name(result))}</h1>",
        f'<p class="sub">{escape(_subtitle(result, kept))}</p>',
        f'<p class="lead">{escape(result.synthesis.summary)}</p>',
    ]
    if result.decision:
        d = result.decision
        out += ["<h2>Overall feedback</h2>", f'<p class="note">{escape(_COUNCIL_NOTE)}</p>',
                f'<p class="lead"><b>Verdict.</b> {escape(d.verdict)}</p>']
        if d.journal_fit:
            out += [f"<h3>Fit with {escape(result.options.journal or 'the target journal')}</h3>", f"<p>{escape(d.journal_fit)}</p>"]
        for c in d.main_concerns:
            out += [f"<h3>{escape(c.title or 'Concern')}</h3>", f"<p>{escape(c.point + _refs(c.issues, number))}</p>"]
            out += [f'<p class="support">Raised by {escape(c.support)}</p>'] if c.support else []
        if d.disagreements:
            out.append("<h3>Where the reviewers disagreed</h3>")
            out += [f'<p class="item">• {f"<b>{escape(c.title)}.</b> " if c.title else ""}{escape(c.point + _refs(c.issues, number))}</p>'
                    for c in d.disagreements]
        if d.side_notes:
            out.append("<h3>Raised, but not a main concern</h3>")
            out += [f'<p class="item">• {escape(c.point + _refs(c.issues, number))}</p>' for c in d.side_notes]
        out.append("<h3>Revision plan</h3>")
        out += [f'<p class="item">{n}. {escape(s.step + _refs(s.issues, number))}</p>' for n, s in enumerate(d.action_plan, 1)]
    out += ["<h2>Detailed comments</h2>",
            '<p class="note">Each comment quotes the paper so you can check it yourself. Treat them as leads to verify, not verdicts.</p>']
    if not kept:
        out.append("<p>No issues survived the final check.</p>")
    for n, (f, v) in enumerate(kept, 1):
        out += [
            f'<h3 class="comment">#{n} {escape(_headline(f))}</h3>',
            f'<p class="meta"><span class="{v.severity}">{v.severity.upper()}</span>{escape(_meta(f, v, merged)[len(v.severity):])}</p>',
        ]
        if not _quote_ok(result, f):
            out.append('<p class="warn">Quote not found in the paper\'s text: check the location by hand.</p>')
        out.append(f'<p class="excerpt">{escape(f.quote)}</p>')
        if f.title:
            out.append(f"<p>{escape(f.problem)}</p>")
        out += [f'<p class="small"><b>Check:</b> {escape(f.check)}</p>', _p("Suggested fix:", f.suggested_fix)]
    out += ["<h2>About this report</h2>", f'<p class="small">{escape(_about(result, usage, kept, merged, rejected))}</p>']
    return "<body>" + "\n".join(out) + "</body>"


def write_pdf(html: str, css: str, path: Path, footer: str) -> None:
    """Lay out HTML on A4 pages with a footer and page numbers.

    PyMuPDF's layout sometimes says a page is finished after running text past its
    bottom edge, and that text is then lost (seen with long lists and on the last
    page). A placed area taller than the page shows this, so lay out again with a
    point less of page height until no page overflows.
    """
    page = pymupdf.paper_rect("a4")
    for shrink in range(8):
        body = page + (54, 54, -54, -60 - shrink)
        story = pymupdf.Story(html=html, user_css=css)
        writer = pymupdf.DocumentWriter(str(path))
        more, overflow = True, False
        while more:
            device = writer.begin_page(page)
            more, filled = story.place(body)
            overflow |= filled[3] > page.height - 30
            story.draw(device)
            writer.end_page()
        writer.close()
        if not overflow:
            break

    # Footer with page numbers, added after layout so the total is known.
    with pymupdf.open(path) as doc:
        for i, pg in enumerate(doc, 1):
            pg.insert_text((54, page.height - 30), f"{footer} · page {i} of {doc.page_count}",
                           fontsize=8, color=(0.45, 0.45, 0.45))
        doc.saveIncr()


def to_pdf(result: AuditResult, usage: Usage, path: Path) -> None:
    """The audit report as a PDF. The HTML uses no <ul>/<ol>, which PyMuPDF can
    lose at page breaks: list items are written as paragraphs."""
    write_pdf(to_html(result, usage), _AUDIT_CSS, path, f"FreeReport · {_name(result)[:80]}")


def to_json(result: AuditResult, usage: Usage) -> str:
    return json.dumps(
        {
            "paper": result.paper,
            "title": result.title,
            "model": result.model,
            "candidates": [f.model_dump() for f in result.candidates],
            "verdicts": [v.model_dump() for v in result.synthesis.verdicts],
            "summary": result.synthesis.summary,
            "quote_scores": result.quote_scores,
            "options": vars(result.options),
            "council": {r: v.model_dump() for r, v in result.council.items()},
            "decision": result.decision.model_dump() if result.decision else None,
            "usage": usage.calls,
            "cost_usd": usage.cost_usd(result.model),
        },
        indent=2,
    )
