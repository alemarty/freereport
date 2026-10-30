"""The three-agent audit: Adversary + Data Auditor find, Synthesis Critic filters.

Optional modes:
- deepdive:  slower, more exhaustive checking by the finder agents.
- blindspot: each finder re-scans its own draft for missed assumptions and gaps.
- council:   six-role opinion review of the whole paper, reported separately.
"""

import json
from dataclasses import dataclass, field

from . import prompts
from .backends import Backend
from .paper import Paper
from .pdf import quote_match
from .schema import (
    CouncilDecision,
    CouncilView,
    Finding,
    FindingList,
    Synthesis,
    Verdict,
    strict_schema,
)

# Below this share of the quote found in the extracted text, the quote is
# flagged as unconfirmed in the report.
QUOTE_THRESHOLD = 0.8


@dataclass
class Options:
    deepdive: bool = False
    blindspot: bool = False
    council: bool = False
    journal: str = ""  # target journal, used by the council


@dataclass
class AuditResult:
    paper: str
    model: str
    candidates: list[Finding]
    synthesis: Synthesis
    quote_scores: dict[str, float]
    options: Options = field(default_factory=Options)
    council: dict[str, CouncilView] = field(default_factory=dict)
    decision: CouncilDecision | None = None
    title: str = ""  # the paper's own title, when the source has one

    def kept(self) -> list[tuple[Finding, Verdict]]:
        by_id = {f.id: f for f in self.candidates}
        return [(by_id[v.id], v) for v in self.synthesis.verdicts if v.keep and v.id in by_id]


def _halves(paper: Paper) -> list[str]:
    """The paper cut in two, as instructions naming each half, so a deep check fits in one answer.
    Empty when the paper cannot be cut (one page, or no text)."""
    numbered = [(f, n) for f, n, text in paper.tex_lines if text.strip()]
    if len(numbered) >= 2:
        f, n = numbered[len(numbered) // 2]
        return [f"\n\nPART 1 OF 2: check only the LaTeX source from its start up to, but not including, {f}:{n}.",
                f"\n\nPART 2 OF 2: check only the LaTeX source from {f}:{n} to the end, including all appendices."]
    if len(paper.pages) >= 2:
        k = len(paper.pages) // 2
        return [f"\n\nPART 1 OF 2: check only PDF pages 1 to {k}.",
                f"\n\nPART 2 OF 2: check only PDF pages {k + 1} to {len(paper.pages)}, including all appendices."]
    return []


def _find(backend: Backend, paper: Paper, label: str, task: str, opts: Options, log) -> list[Finding]:
    schema = strict_schema(FindingList)
    if opts.deepdive:
        task += prompts.DEEPDIVE
    parts = _halves(paper) if opts.deepdive else []
    if parts:  # a deep check of a whole paper can think past the longest answer allowed: do it in two halves
        found = []
        for i, part in enumerate(parts, 1):
            more = FindingList.model_validate(backend.ask(f"{label}_part{i}", prompts.SYSTEM, task + part, schema)).findings
            seen = {f.id for f in found}
            for f in more:
                if f.id in seen:  # both halves numbered from 1
                    f.id += "b"
            found += more
    else:
        found = FindingList.model_validate(backend.ask(label, prompts.SYSTEM, task, schema)).findings
    log(f"  {len(found)} candidate(s)")
    if opts.blindspot:
        draft = json.dumps([f.model_dump() for f in found], indent=1)
        extra = FindingList.model_validate(
            backend.ask(f"{label}_blindspot", prompts.SYSTEM, prompts.BLINDSPOT.format(task=task, draft=draft), schema)
        ).findings
        seen = {f.id for f in found}
        extra = [f for f in extra if f.id not in seen]
        log(f"  blind-spot check added {len(extra)}")
        found += extra
    return found


def _council(backend: Backend, kept: list[tuple[Finding, Verdict]], log, journal: str = ""):
    views = {}
    journal_note = prompts.COUNCIL_JOURNAL.format(journal=journal) if journal else ""
    for role, brief in prompts.COUNCIL_ROLES.items():
        log(f"Council: {role}...")
        views[role] = CouncilView.model_validate(
            backend.ask(f"council_{role}", prompts.SYSTEM, prompts.COUNCIL_INTRO + brief + journal_note, strict_schema(CouncilView))
        )
    log("Council: Executive...")
    task = prompts.COUNCIL_EXECUTIVE.format(
        journal_rule=prompts.COUNCIL_FIT_RULE.format(journal=journal) if journal else prompts.COUNCIL_NO_JOURNAL_RULE,
        findings=json.dumps([f.model_dump() for f, _ in kept], indent=1),
        opinions=json.dumps({r: v.model_dump() for r, v in views.items()}, indent=1),
    )
    decision = CouncilDecision.model_validate(
        backend.ask("council_executive", prompts.SYSTEM, task, strict_schema(CouncilDecision))
    )
    return views, decision


def audit(paper: Paper, backend: Backend, opts: Options | None = None, log=print) -> AuditResult:
    opts = opts or Options()

    log("Agent 1 (Academic Adversary): checking equations and proofs...")
    math = _find(backend, paper, "adversary", prompts.ADVERSARY, opts, log)
    log("Agent 2 (Data Auditor): checking numbers against tables...")
    data = _find(backend, paper, "data_auditor", prompts.DATA_AUDITOR, opts, log)

    candidates = math + data
    quote_scores = {f.id: quote_match(f.quote, paper.pages, f.page, paper.tex_text) for f in candidates}

    if candidates:
        log("Agent 3 (Synthesis Critic): re-checking every candidate...")
        task = prompts.SYNTHESIS.format(candidates=json.dumps([f.model_dump() for f in candidates], indent=1))
        synthesis = Synthesis.model_validate(backend.ask("synthesis", prompts.SYSTEM, task, strict_schema(Synthesis)))
    else:
        synthesis = Synthesis(verdicts=[], summary="No candidate errors were found by either reviewer.")

    result = AuditResult(
        paper=paper.name,
        model=backend.model,
        candidates=candidates,
        synthesis=synthesis,
        quote_scores=quote_scores,
        options=opts,
        title=paper.title,
    )
    if opts.council:
        result.council, result.decision = _council(backend, result.kept(), log, opts.journal)
    return result
