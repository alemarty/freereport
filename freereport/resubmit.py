"""Resubmission check: original paper + referee reports + revised paper.

1. Compare the original and revised paper on this computer (free): LaTeX line
   by line when both are given as source, otherwise PDF text sentence by
   sentence. Each change gets an id (C1, C2, ...).
2. Split the referee reports into comments, or reuse them from an earlier
   `freereport respond` run (--previous), so the ids stay the same.
3. For each referee, mark every comment addressed / partly addressed / not
   addressed, from the changes and the revised paper, with a paragraph for
   the response letter.
4. Overview: is it ready, which changes no referee asked for, and the opening
   of the response letter.

The model only receives the revised paper plus the list of changes, so the
cost is close to a single-paper run.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from html import escape
from pathlib import Path

import pymupdf

from . import prompts
from .backends import Backend, Usage
from .paper import Paper
from .referee import PRIORITY_ORDER, _REFEREE_CSS, _find, _hex, read_report
from .schema import (
    CommentStatus,
    LetterCheck,
    RefereeComment,
    RefereeCommentList,
    ResubmitOverview,
    StatusList,
    strict_schema,
)

STATUS_ORDER = {"not_addressed": 0, "partly_addressed": 1, "addressed": 2}
STATUS_LABEL = {"addressed": "Addressed", "partly_addressed": "Partly addressed", "not_addressed": "Not addressed"}
STATUS_RGB = {
    "not_addressed": (0.86, 0.15, 0.15),
    "partly_addressed": (0.96, 0.55, 0.10),
    "addressed": (0.10, 0.62, 0.25),
}
CLAIM_ORDER = {"contradicted": 0, "not_found": 1, "partly": 2, "confirmed": 3}
CLAIM_LABEL = {"contradicted": "Contradicted", "not_found": "Not found", "partly": "Partly done", "confirmed": "Confirmed"}
CLAIM_RGB = {"contradicted": STATUS_RGB["not_addressed"], "not_found": STATUS_RGB["not_addressed"],
             "partly": STATUS_RGB["partly_addressed"], "confirmed": STATUS_RGB["addressed"]}
LIGHT_NOTE = ("Quick check: only the list of changes was sent, not the full paper, and no error check was run. "
              "Use it to double-check; run without --quick for the full review.")
MAX_SIDE_CHARS = 1500     # per old/new text of one change
MAX_CHANGES_CHARS = 120_000  # whole list sent to the model


# --- Comparing the two versions ---------------------------------------------

@dataclass
class Change:
    id: str
    kind: str        # "changed", "added" or "removed"
    old_where: str   # location in the original, e.g. "page 2" or "main.tex:40"
    new_where: str   # location in the revised version
    old: str
    new: str


def _tex_segments(paper: Paper) -> list[tuple[str, str]]:
    return [(f"{f}:{n}", text.strip()) for f, n, text in paper.tex_lines if text.strip()]


def _pdf_segments(paper: Paper) -> list[tuple[str, str]]:
    """Sentences per page, so that re-flowed lines do not count as changes."""
    out = []
    for i, page in enumerate(paper.pages, 1):
        text = re.sub(r"-\s*\n\s*", "", page)
        text = re.sub(r"\s+", " ", text).strip()
        out += [(f"page {i}", s) for s in re.split(r"(?<=[.!?;:])\s+", text) if s]
    return out


def _clip(text: str) -> str:
    return text if len(text) <= MAX_SIDE_CHARS else text[:MAX_SIDE_CHARS] + " [...]"


def compare(original: Paper, revised: Paper) -> tuple[list[Change], str]:
    """Changes from the original to the revised paper, and which text was compared."""
    if original.tex_lines and revised.tex_lines:
        a, b, basis = _tex_segments(original), _tex_segments(revised), "LaTeX source, line by line"
    elif original.pages and revised.pages:
        a, b, basis = _pdf_segments(original), _pdf_segments(revised), "PDF text, sentence by sentence"
    else:
        raise ValueError("give both versions in the same form: both with a PDF, or both as LaTeX")

    changes = []
    matcher = SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        kind = {"replace": "changed", "insert": "added", "delete": "removed"}[tag]
        old_where = a[i1][0] if i1 < i2 else (a[i1 - 1][0] if i1 else "start")
        new_where = b[j1][0] if j1 < j2 else (b[j1 - 1][0] if j1 else "start")
        changes.append(Change(
            id=f"C{len(changes) + 1}", kind=kind, old_where=old_where, new_where=new_where,
            old=_clip(" ".join(t for _, t in a[i1:i2])), new=_clip(" ".join(t for _, t in b[j1:j2])),
        ))
    return changes, basis


def changes_text(changes: list[Change]) -> str:
    """The change list as the model reads it, cut off if very long."""
    if not changes:
        return "(no differences found: the revised text is identical to the original)"
    out, size = [], 0
    for c in changes:
        block = f"[{c.id}] {c.kind} · original {c.old_where} -> revised {c.new_where}"
        if c.old:
            block += f"\n  old: {c.old}"
        if c.new:
            block += f"\n  new: {c.new}"
        if size + len(block) > MAX_CHANGES_CHARS:
            out.append(f"[list cut here: {len(changes) - len(out)} more change(s) not shown]")
            break
        out.append(block)
        size += len(block)
    return "\n".join(out)


# --- Pipeline ---------------------------------------------------------------

@dataclass
class ResubmitResult:
    paper: str
    original: str
    model: str
    basis: str
    changes: list[Change]
    comments: list[RefereeComment]
    statuses: dict[str, CommentStatus]
    overview: ResubmitOverview
    reused_previous: bool = False
    light: bool = False  # quick check: only the change list was sent, not the manuscript
    letter_check: LetterCheck | None = None
    check_summary: str = ""  # one line about the error check, filled in by the command line
    notes: list[str] = field(default_factory=list)

    def ranked(self) -> list[tuple[RefereeComment, CommentStatus]]:
        """Not addressed first, then by seriousness."""
        pairs = [(c, self.statuses[c.id]) for c in self.comments if c.id in self.statuses]
        return sorted(pairs, key=lambda cs: (STATUS_ORDER[cs[1].status], PRIORITY_ORDER[cs[1].priority], cs[0].id))

    def claims_ranked(self):
        """Letter claims, problems first."""
        if not self.letter_check:
            return []
        return sorted(self.letter_check.claims, key=lambda c: (CLAIM_ORDER[c.verdict], c.comment))

    def counts(self) -> dict[str, int]:
        return {s: sum(st.status == s for st in self.statuses.values()) for s in STATUS_ORDER}


def load_previous(path: Path) -> tuple[list[RefereeComment], dict[str, dict]]:
    """Comments and assessments from a `freereport respond` JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    comments = [RefereeComment.model_validate(c) for c in data["comments"]]
    earlier = {a["id"]: {k: a[k] for k in ("accuracy", "priority", "evidence", "suggested_response") if k in a}
               for a in data.get("assessments", [])}
    return comments, earlier


def check_revision(original: Paper, revised: Paper, reports: list[Path], backend: Backend,
                   previous: Path | None = None, letter: Path | None = None, light: bool = False,
                   log=print) -> ResubmitResult:
    """light=True: the backend was built without the manuscript, so judge from the change list only."""
    system = prompts.RESUBMIT_SYSTEM_LIGHT if light else prompts.RESUBMIT_SYSTEM
    changes, basis = compare(original, revised)
    log(f"Compared the two versions ({basis}): {len(changes)} change(s)")
    listing = changes_text(changes)

    earlier: dict[str, dict] = {}
    if previous:
        comments, earlier = load_previous(previous)
        log(f"Reusing {len(comments)} comment(s) from {Path(previous).name}")
    else:
        texts = "\n\n".join(
            f"===== REPORT {n} (file: {p.name}) =====\n{read_report(p)}" for n, p in enumerate(reports, 1)
        )
        log("Splitting the referee reports into comments...")
        comments = RefereeCommentList.model_validate(
            backend.ask("resubmit_split", system, prompts.REFEREE_SPLIT.format(reports=texts),
                        strict_schema(RefereeCommentList))
        ).comments
        log(f"  {len(comments)} comment(s)")

    statuses: dict[str, CommentStatus] = {}
    by_report: dict[str, list[RefereeComment]] = {}
    for c in comments:
        by_report.setdefault(c.id.split(".")[0], []).append(c)
    for report_id, group in by_report.items():
        reviewer = group[0].reviewer
        log(f"Checking {reviewer} ({len(group)} comment(s))...")
        items = [{**c.model_dump(), **({"earlier": earlier[c.id]} if c.id in earlier else {})} for c in group]
        task = prompts.RESUBMIT_ASSESS.format(
            reviewer=reviewer,
            previous_rule=prompts.RESUBMIT_PREVIOUS if earlier else prompts.RESUBMIT_NO_PREVIOUS,
            comments=json.dumps(items, indent=1),
            changes=listing,
        )
        result = StatusList.model_validate(
            backend.ask(f"resubmit_status_{report_id}", system, task, strict_schema(StatusList))
        )
        statuses.update({s.id: s for s in result.statuses})

    log("Writing the overview and the response letter opening...")
    joined = [{"comment": c.model_dump(), "status": statuses[c.id].model_dump()} for c in comments if c.id in statuses]
    overview = ResubmitOverview.model_validate(
        backend.ask("resubmit_overview", system,
                    prompts.RESUBMIT_OVERVIEW.format(statuses=json.dumps(joined, indent=1), changes=listing),
                    strict_schema(ResubmitOverview))
    )
    letter_check = None
    if letter:
        log(f"Checking your response letter ({Path(letter).name}) against the changes...")
        task = prompts.RESUBMIT_LETTER.format(
            comments=json.dumps([c.model_dump() for c in comments], indent=1),
            letter=read_report(Path(letter)),
            changes=listing,
        )
        letter_check = LetterCheck.model_validate(backend.ask("resubmit_letter", system, task, strict_schema(LetterCheck)))
    return ResubmitResult(revised.name, original.name, backend.model, basis, changes, comments, statuses,
                          overview, reused_previous=bool(previous), light=light, letter_check=letter_check)


# --- Reports ----------------------------------------------------------------

def _where(s: CommentStatus) -> str:
    return ", ".join(x for x in (f"page {s.page}" if s.page else "", s.source_line) if x) or "whole paper"


def _subtitle(result: ResubmitResult, usage: Usage) -> str:
    counts = result.counts()
    parts = [
        date.today().isoformat(),
        f"original: {result.original}",
        f"{len(result.changes)} change(s) found",
        ", ".join(f"{n} {STATUS_LABEL[s].lower()}" for s, n in counts.items() if n) or "no comments",
    ]
    cost = usage.cost_usd(result.model)
    if cost is not None:
        parts.append(f"cost ≈ ${cost:.2f}")
    return " · ".join(parts)


def letter(result: ResubmitResult) -> str:
    """Draft response letter, in the referees' own order, as Markdown."""
    lines = ["# Response to the referees", "", result.overview.letter_opening, ""]
    reviewer = None
    for c in result.comments:
        s = result.statuses.get(c.id)
        if s is None:
            continue
        if c.reviewer != reviewer:
            reviewer = c.reviewer
            lines += [f"## {reviewer}", ""]
        lines += [f"**Comment {c.id}.** *{c.text.strip()}*", "", f"**Response.** {s.response}", ""]
    return "\n".join(lines)


_TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
                "<": r"\textless{}", ">": r"\textgreater{}",
                # common symbols pdfLaTeX (Overleaf's default) cannot read directly
                "≈": r"$\approx$", "≤": r"$\leq$", "≥": r"$\geq$", "±": r"$\pm$", "×": r"$\times$",
                "−": "-", "→": r"$\rightarrow$", "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$",
                "δ": r"$\delta$", "ε": r"$\varepsilon$", "μ": r"$\mu$", "σ": r"$\sigma$", "ρ": r"$\rho$",
                "χ": r"$\chi$", "²": r"$^2$", "√": r"$\surd$", "∞": r"$\infty$", "≠": r"$\neq$"}
_TODO = re.compile(r"\[TO DO[^\]]*\]")


def _tex(text: str) -> str:
    """Escape plain text for LaTeX, showing [TO DO ...] markers in red."""
    def esc(t: str) -> str:
        return "".join(_TEX_SPECIAL.get(ch, ch) for ch in t)
    out, last = [], 0
    for m in _TODO.finditer(text):
        out += [esc(text[last:m.start()]), r"\textcolor{red}{\textbf{" + esc(m.group()) + "}}"]
        last = m.end()
    return "".join(out) + esc(text[last:])


def latex_letter(result: ResubmitResult) -> str:
    """The draft response letter as a LaTeX file that compiles as is (e.g. upload to Overleaf)."""
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[margin=2.5cm]{geometry}",
        r"\usepackage{xcolor}",
        r"\usepackage[most]{tcolorbox}",
        r"\newtcolorbox{referee}{colback=gray!8, colframe=gray!45, boxrule=0.4pt, arc=1pt, breakable}",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{6pt}",
        "",
        r"\begin{document}",
        "",
        r"\section*{Response to the referees}",
        r"\textit{Manuscript: " + _tex(result.paper) + "}",
        "",
        _tex(result.overview.letter_opening),
        "",
    ]
    reviewer = None
    for c in result.comments:
        s = result.statuses.get(c.id)
        if s is None:
            continue
        if c.reviewer != reviewer:
            reviewer = c.reviewer
            lines += [r"\subsection*{" + _tex(reviewer) + "}", ""]
        lines += [
            r"\begin{referee}",
            r"\textbf{Comment " + _tex(c.id) + r".} \textit{" + _tex(c.text.strip()) + "}",
            r"\end{referee}",
            r"\textbf{Response.} " + _tex(s.response),
            "",
        ]
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def to_markdown(result: ResubmitResult, usage: Usage) -> str:
    lines = [
        f"# Resubmission check: {result.paper}",
        "",
        f"*{_subtitle(result, usage)}*",
        "",
        "## Overview",
        "",
        result.overview.summary,
        "",
    ]
    if result.light:
        lines += [f"*{LIGHT_NOTE}*", ""]
    if result.check_summary:
        lines += [f"**Error check of the revised paper:** {result.check_summary}", ""]
    if result.letter_check:
        lines += ["## Your response letter, checked against the changes", ""]
        for cl in result.claims_ranked():
            lines += [f"- **{CLAIM_LABEL[cl.verdict].upper()}** ({cl.comment or 'general'}): \"{cl.claim}\" {cl.evidence}"]
        if result.letter_check.unanswered:
            lines += [f"- **NOT ANSWERED in your letter:** {', '.join(result.letter_check.unanswered)}"]
        lines.append("")
    lines += ["## Referee comments, not addressed first", ""]
    for c, s in result.ranked():
        lines += [
            f"### {c.id} · {STATUS_LABEL[s.status].upper()} · {s.priority}",
            "",
            f"*{c.reviewer}, {_where(s)}*",
            "",
            *[f"> {line}" for line in c.text.splitlines() if line.strip()],
            "",
            f"**What changed:** {s.evidence}",
            "",
        ]
        if s.quote:
            lines += [f"**In the revised paper:** \"{s.quote}\"", ""]
        if s.remaining:
            lines += [f"**Still to do:** {s.remaining}", ""]
    if result.overview.unrequested:
        lines += ["## Changes no referee asked for", ""]
        lines += [f"- **{u.change}**: {u.what} *{u.note}*" for u in result.overview.unrequested]
        lines.append("")
    lines += [f"*Versions compared using the {result.basis}. Full change list in the JSON file.*", ""]
    return "\n".join(lines)


def to_html(result: ResubmitResult, usage: Usage) -> str:
    legend = " &#160; ".join(
        f'<span style="color:{_hex(rgb)}; font-weight:bold">■ {STATUS_LABEL[s].upper()}</span>' for s, rgb in STATUS_RGB.items()
    )
    out = [
        f"<h1>Resubmission check: {escape(result.paper)}</h1>",
        f'<p class="sub">{escape(_subtitle(result, usage))}</p>',
        f'<p class="meta">Status: {legend}</p>',
        "<h2>Overview</h2>",
        f"<p>{escape(result.overview.summary)}</p>",
    ]
    if result.light:
        out.append(f'<p class="note">{escape(LIGHT_NOTE)}</p>')
    if result.check_summary:
        out.append(f'<p><span class="label">Error check of the revised paper:</span> {escape(result.check_summary)}</p>')
    if result.letter_check:
        out += ["<h2>Your response letter, checked against the changes</h2>",
                '<p class="note">Every claim in your letter about what was changed, problems first.</p>']
        for cl in result.claims_ranked():
            colour = _hex(CLAIM_RGB[cl.verdict])
            out += [f'<p style="border-left: 5px solid {colour}; padding-left: 5pt"><b style="color:{colour}">'
                    f'{CLAIM_LABEL[cl.verdict].upper()}</b> · {escape(cl.comment or "general")}'
                    + (f" · page {cl.page}" if cl.page else "") + "</p>",
                    f'<p class="ref">{escape(cl.claim)}</p>',
                    f"<p>{escape(cl.evidence)}</p>"]
        if result.letter_check.unanswered:
            out.append(f'<p><b style="color:{_hex(STATUS_RGB["not_addressed"])}">Not answered in your letter:</b> '
                       f'{escape(", ".join(result.letter_check.unanswered))}</p>')
    out.append("<h2>Referee comments, not addressed first</h2>")
    for c, s in result.ranked():
        colour = _hex(STATUS_RGB[s.status])
        out += [
            f'<h3 style="border-left: 5px solid {colour}; padding-left: 5pt">{escape(c.id)} '
            f'<span style="color:{colour}">{STATUS_LABEL[s.status].upper()}</span> · {s.priority}</h3>',
            f'<p class="meta">{escape(c.reviewer)} · {escape(_where(s))}</p>',
            f'<p class="ref">{escape(c.text)}</p>',
            f'<p><span class="label">What changed:</span> {escape(s.evidence)}</p>',
        ]
        if s.quote:
            out.append(f'<p class="quote">{escape(s.quote)}</p>')
        if s.remaining:
            out.append(f'<p><span class="label" style="color:{_hex(STATUS_RGB["not_addressed"])}">Still to do:</span> {escape(s.remaining)}</p>')
    if result.overview.unrequested:
        out += ["<h2>Changes no referee asked for</h2>",
                '<p class="note">Editors often ask about these. Mention them in the response letter.</p>', "<ul>"]
        out += [f"<li><b>{escape(u.change)}</b>: {escape(u.what)} <i>{escape(u.note)}</i></li>" for u in result.overview.unrequested]
        out.append("</ul>")
    out += ["<h2>Draft response letter</h2>", f"<p>{escape(result.overview.letter_opening)}</p>"]
    for c in result.comments:
        s = result.statuses.get(c.id)
        if s:
            out += [f'<p class="ref"><b>Comment {escape(c.id)}.</b> {escape(c.text)}</p>',
                    f"<p><b>Response.</b> {escape(s.response)}</p>"]
    out.append(f'<p class="note">Versions compared using the {escape(result.basis)}.</p>')
    return "<body>" + "\n".join(out) + "</body>"


def to_pdf(result: ResubmitResult, usage: Usage, path: Path) -> None:
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
            pg.insert_text((54, page.height - 30), f"FreeReport · resubmission check · {result.paper} · page {i} of {doc.page_count}",
                           fontsize=8, color=(0.45, 0.45, 0.45))
        doc.saveIncr()


def to_json(result: ResubmitResult, usage: Usage) -> str:
    return json.dumps(
        {
            "paper": result.paper,
            "original": result.original,
            "model": result.model,
            "compared": result.basis,
            "changes": [vars(c) for c in result.changes],
            "comments": [c.model_dump() for c in result.comments],
            "statuses": [s.model_dump() for s in result.statuses.values()],
            "overview": result.overview.model_dump(),
            "letter_check": result.letter_check.model_dump() if result.letter_check else None,
            "light": result.light,
            "usage": usage.calls,
            "cost_usd": usage.cost_usd(result.model),
        },
        indent=2,
    )


def highlight_pdf(result: ResubmitResult, pdf_path: Path, out_path: Path) -> int:
    """Copy of the revised paper with each comment's spot highlighted by status
    (green addressed, orange partly, red not). Returns how many were placed on the text."""
    placed = 0
    with pymupdf.open(pdf_path) as doc:
        unplaced = []
        for c, s in result.ranked():
            pages = [s.page - 1] if 0 < s.page <= doc.page_count else []
            pages += [i for i in range(doc.page_count) if i not in pages]
            hits, where = [], None
            if s.quote:
                for i in pages:
                    hits = _find(doc[i], s.quote)
                    if hits:
                        where = doc[i]
                        break
            note = f"{c.id} [{STATUS_LABEL[s.status].upper()} · {s.priority}]\n\n{c.reviewer}: {c.text}\n\nWhat changed: {s.evidence}"
            if s.remaining:
                note += f"\n\nStill to do: {s.remaining}"
            if where is None:
                unplaced.append((c, s, note))
                continue
            annot = where.add_highlight_annot(hits)
            annot.set_colors(stroke=STATUS_RGB[s.status])
            annot.set_info(title=f"{c.id} · {s.status}", content=note)
            annot.update()
            placed += 1
        for n, (c, s, note) in enumerate(unplaced):
            pg = doc[s.page - 1] if 0 < s.page <= doc.page_count else doc[0]
            annot = pg.add_text_annot((pg.rect.width - 24, 30 + 22 * (n % 30)), note, icon="Comment")
            annot.set_colors(stroke=STATUS_RGB[s.status])
            annot.set_info(title=f"{c.id} · {s.status}")
            annot.update()
        doc.save(out_path)
    return placed
