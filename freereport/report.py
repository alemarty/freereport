"""Turns an AuditResult into a Markdown report and a JSON file."""

import json
from datetime import date

from .backends import Usage
from .pipeline import QUOTE_THRESHOLD, AuditResult

_ORDER = {"critical": 0, "major": 1, "minor": 2}


def to_markdown(result: AuditResult, usage: Usage) -> str:
    kept = sorted(result.kept(), key=lambda fv: (_ORDER[fv[1].severity], fv[0].page))
    rejected = [v for v in result.synthesis.verdicts if not v.keep]
    cost = usage.cost_usd(result.model)

    lines = [
        f"# FreeReport audit: {result.paper}",
        "",
        f"*{date.today().isoformat()} · model `{result.model}` · "
        f"{len(kept)} issue(s) reported, {len(rejected)} candidate(s) rejected"
        + (f" · cost ≈ ${cost:.2f}" if cost is not None else "")
        + "*",
        "",
        "> Every issue below cites a page and a quote so you can check it yourself. "
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
        score = result.quote_scores.get(f.id, 0.0)
        flag = "" if score >= QUOTE_THRESHOLD else " ⚠️ quote not found in extracted text, check location by hand"
        lines += [
            f"### {n}. {f.problem}",
            "",
            f"**{v.severity.title()}** · confidence {v.confidence} · {f.category} · page {f.page}, {f.location}{flag}",
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
    if rejected:
        lines += ["## Rejected candidates", "", "Raised by a reviewer agent, then rejected on re-checking:", ""]
        by_id = {f.id: f for f in result.candidates}
        for v in rejected:
            f = by_id.get(v.id)
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
