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


def _same_as(v, kept) -> str:
    """'issue 2' when the duplicate's twin is in the report, else the raw id."""
    number = {f.id: n for n, (f, _) in enumerate(kept, 1)}
    return f"issue {number[v.duplicate_of]}" if v.duplicate_of in number else v.duplicate_of


def _where(f) -> list[str]:
    return [x for x in (f"page {f.page}" if f.page else "", f.source_line, f.location) if x]


def _quote_ok(result: AuditResult, f) -> bool:
    return result.quote_scores.get(f.id, 0.0) >= QUOTE_THRESHOLD


def _subtitle(result: AuditResult, usage: Usage, kept, merged, rejected) -> str:
    cost = usage.cost_usd(result.model)
    parts = [
        date.today().isoformat(),
        f"model {result.model}",
        f"{len(kept)} issue(s) reported",
        f"{len(merged)} duplicate(s) merged",
        f"{len(rejected)} candidate(s) rejected",
    ]
    if cost is not None:
        parts.append(f"cost ≈ ${cost:.2f}")
    return " · ".join(parts)


def to_markdown(result: AuditResult, usage: Usage) -> str:
    kept, merged, rejected = _sections(result)
    lines = [
        f"# FreeReport audit: {result.paper}",
        "",
        f"*{_subtitle(result, usage, kept, merged, rejected)}*",
        "",
        "> Every issue below cites where it is and quotes the paper so you can check it yourself. "
        "Treat them as leads to verify, not verdicts.",
        "",
        "## Summary",
        "",
        result.synthesis.summary,
        "",
        "## Issues",
        "",
    ]
    if not kept:
        lines += ["No issues survived the final check.", ""]
    for n, (f, v) in enumerate(kept, 1):
        where = ", ".join(f"`{x}`" if x == f.source_line else x for x in _where(f))
        flag = "" if _quote_ok(result, f) else " ⚠️ quote not found in the paper's text, check location by hand"
        lines += [
            f"### {n}. {f.problem}",
            "",
            f"**{v.severity.title()}** · confidence {v.confidence} · {f.category} · {where}{flag}",
            "",
            f"> {f.quote}",
            "",
            f"**Check:** {f.check}",
            "",
            f"**Suggested fix:** {f.suggested_fix}",
            "",
            f"*Final reviewer note:* {v.reason}",
            "",
        ]
    if merged:
        lines += ["## Merged duplicates", "", "Found by more than one checker; reported once above:", ""]
        lines += [f"- **{v.id}** → same as **{_same_as(v, kept)}**: {f.problem if f else ''}" for f, v in merged]
        lines.append("")
    if rejected:
        lines += ["## Rejected candidates", "", "Raised by a checker, then rejected on re-checking:", ""]
        for f, v in rejected:
            lines.append(f"- **{v.id}** ({f.location if f else '?'}): {f.problem if f else ''} *Rejected because:* {v.reason}")
        lines.append("")
    if result.decision:
        lines += [
            "## Council review",
            "",
            "> Opinions from five reviewer roles on the paper as a whole. Unlike the issues above, these are not verified errors.",
            "",
            "### Executive verdict",
            "",
            result.decision.verdict,
            "",
            "**Revision plan:**",
            "",
        ]
        lines += [f"{n}. {s.step} *({s.why})*" for n, s in enumerate(result.decision.action_plan, 1)]
        lines.append("")
        for role, view in result.council.items():
            lines += [f"### {role}", ""]
            lines += [f"- {p.point}" + (f" (p. {p.page})" if p.page else "") for p in view.points]
            lines.append("")
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
li { margin: 2pt 0; }
"""


def _p(label: str, text: str) -> str:
    return f'<p><span class="label">{escape(label)}</span> {escape(text)}</p>'


def to_html(result: AuditResult, usage: Usage) -> str:
    kept, merged, rejected = _sections(result)
    out = [
        f"<h1>FreeReport audit: {escape(result.paper)}</h1>",
        f'<p class="sub">{escape(_subtitle(result, usage, kept, merged, rejected))}</p>',
        '<p class="note">Every issue cites where it is and quotes the paper so you can check it yourself. '
        "Treat them as leads to verify, not verdicts.</p>",
        "<h2>Summary</h2>",
        f"<p>{escape(result.synthesis.summary)}</p>",
        "<h2>Issues</h2>",
    ]
    if not kept:
        out.append("<p>No issues survived the final check.</p>")
    for n, (f, v) in enumerate(kept, 1):
        out += [
            f"<h3>{n}. {escape(f.problem)}</h3>",
            f'<p class="meta"><span class="{v.severity}">{v.severity.upper()}</span> · confidence {v.confidence}'
            f" · {f.category} · {escape(', '.join(_where(f)))}</p>",
        ]
        if not _quote_ok(result, f):
            out.append('<p class="warn">Quote not found in the paper\'s text: check the location by hand.</p>')
        out += [
            f'<p class="quote">{escape(f.quote)}</p>',
            _p("Check:", f.check),
            _p("Suggested fix:", f.suggested_fix),
            f'<p class="note">Final reviewer note: {escape(v.reason)}</p>',
        ]
    if merged:
        out += ["<h2>Merged duplicates</h2>", '<p class="note">Found by more than one checker; reported once above.</p>', "<ul>"]
        out += [f"<li><b>{escape(v.id)}</b> → same as <b>{escape(_same_as(v, kept))}</b>: {escape(f.problem if f else '')}</li>" for f, v in merged]
        out.append("</ul>")
    if rejected:
        out += ["<h2>Rejected candidates</h2>", '<p class="note">Raised by a checker, then rejected on re-checking.</p>', "<ul>"]
        out += [
            f"<li><b>{escape(v.id)}</b> ({escape(f.location if f else '?')}): {escape(f.problem if f else '')} "
            f"<i>Rejected because:</i> {escape(v.reason)}</li>"
            for f, v in rejected
        ]
        out.append("</ul>")
    if result.decision:
        out += [
            "<h2>Council review</h2>",
            '<p class="note">Opinions from five reviewer roles on the paper as a whole. Unlike the issues above, these are not verified errors.</p>',
            "<h3>Executive verdict</h3>",
            f"<p>{escape(result.decision.verdict)}</p>",
            '<p class="label">Revision plan:</p>',
            "<ol>",
            *[f"<li>{escape(s.step)} <i>({escape(s.why)})</i></li>" for s in result.decision.action_plan],
            "</ol>",
        ]
        for role, view in result.council.items():
            out += [f"<h3>{escape(role)}</h3>", "<ul>"]
            out += [f"<li>{escape(p.point)}" + (f" (p. {p.page})" if p.page else "") + "</li>" for p in view.points]
            out.append("</ul>")
    return "<body>" + "\n".join(out) + "</body>"


def to_pdf(result: AuditResult, usage: Usage, path: Path) -> None:
    """Lay out the HTML report on A4 pages with page numbers."""
    story = pymupdf.Story(html=to_html(result, usage), user_css=_CSS)
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

    # Footer with page numbers, added after layout so the total is known.
    with pymupdf.open(path) as doc:
        for i, pg in enumerate(doc, 1):
            pg.insert_text((54, page.height - 30), f"FreeReport · {result.paper} · page {i} of {doc.page_count}",
                           fontsize=8, color=(0.45, 0.45, 0.45))
        doc.saveIncr()


def to_json(result: AuditResult, usage: Usage) -> str:
    return json.dumps(
        {
            "paper": result.paper,
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
