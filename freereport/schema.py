"""Data shapes shared by every agent and backend."""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["math", "logic", "data", "consistency"]
Severity = Literal["critical", "major", "minor"]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """One suspected error, written by Agent 1 or Agent 2."""

    id: str = Field(description="Short id, e.g. 'M1' for math or 'D3' for data.")
    title: str = Field("", description="Headline of 4-10 words naming the problem, e.g. 'Sample sizes in Figure 14 do not add up'.")
    category: Category
    page: int = Field(description="1-based page number of the PDF where the problem is, or 0 if no PDF was given.")
    source_line: str = Field(description="'file:line' in the LaTeX source, e.g. 'main.tex:212', or '' if no LaTeX source was given.")
    location: str = Field(description="Where in the paper, e.g. 'Equation (7)', 'Table 2, row 3', 'Proof of Lemma 1, line 4'.")
    quote: str = Field(description="Exact text copied from the paper (PDF text or LaTeX source) at that spot, at most ~40 words.")
    problem: str = Field(description="One or two sentences: what is wrong.")
    check: str = Field(description="The step-by-step working that shows it is wrong, so a human can verify it.")
    suggested_fix: str
    how_checked: str = Field("", description="One short line on how you verified it, e.g. 'recomputed from Table 3', "
                             "'re-derived Eq. (7) by hand', 'read from the bars of Figure 5 (approximate)'.")
    severity: Severity
    confidence: Confidence


class FindingList(BaseModel):
    findings: list[Finding]


class Verdict(BaseModel):
    """Agent 3's decision on one candidate finding."""

    id: str
    keep: bool
    reason: str = Field(description="Why the finding was kept or rejected.")
    duplicate_of: str = Field(description="If this candidate reports the same problem as another candidate, or another place where "
                              "the same underlying error shows up, that candidate's id (and keep must be false); otherwise ''.")
    severity: Severity
    confidence: Confidence


class Synthesis(BaseModel):
    verdicts: list[Verdict]
    summary: str = Field(description="3-6 sentence overall assessment of the paper's correctness.")
    checked_correct: list[str] = Field([], description="Up to 10 important equations, derivations or numbers that were "
                                       "re-checked and hold, each in one short line, e.g. 'the step from (29) to (30)'.")


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema in the strict form structured outputs require
    (every property required, no extra properties)."""
    schema = model.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)

    fix(schema)
    return schema


class CouncilPoint(BaseModel):
    point: str = Field(description="One concrete observation, in one to three sentences.")
    page: int = Field(description="1-based page it refers to, or 0 if it is about the whole paper.")


class CouncilView(BaseModel):
    """One council member's opinion on the paper as a whole."""

    points: list[CouncilPoint]


class RevisionStep(BaseModel):
    step: str
    why: str


class CouncilConclusion(BaseModel):
    """One point of the council's joint report."""

    title: str = Field("", description="Headline of 3-8 words, e.g. 'Exposure measurement and shock timing'.")
    point: str = Field(description="One short paragraph in the council's own words: what the problem is and what to do about it.")
    support: str = Field(description="Who raised or backs it, e.g. '4 of 5: Contrarian, Methods Referee, Outsider, First Principles'.")
    issues: list[str] = Field(description="Ids of verified findings this point relies on (e.g. ['M1', 'D4']); [] if none. Cite them instead of restating them.")


class CouncilStep(BaseModel):
    step: str = Field(description="One revision step in one or two sentences.")
    issues: list[str] = Field(description="Ids of verified findings this step fixes; [] if none.")


class CouncilDecision(BaseModel):
    """The chair's single council report, merging the five members' opinions."""

    verdict: str = Field(description="2-4 sentences: is the paper ready to submit, and what matters most.")
    journal_fit: str = Field(description="Fit with the target journal, if one was given; otherwise ''.")
    main_concerns: list[CouncilConclusion] = Field(description="What the council agrees matters most, most important first. At most 6.")
    disagreements: list[CouncilConclusion] = Field(description="Where members contradict each other: state both sides and the "
                                                    "council's ruling (by majority and by the evidence). [] if none.")
    side_notes: list[CouncilConclusion] = Field(description="Raised by a member but not a main concern, one sentence each. At most 6.")
    action_plan: list[CouncilStep] = Field(description="Ordered revision steps, most important first. At most 8.")


# --- Referee response mode -----------------------------------------------

Accuracy = Literal["correct", "partly_correct", "incorrect", "unclear"]
Priority = Literal["fatal", "major", "minor", "trivial"]


class RefereeComment(BaseModel):
    id: str = Field(description="'R<report number>.<comment number>', e.g. 'R1.3'.")
    reviewer: str = Field(description="How the report names its author, e.g. 'Reviewer 2', or 'Report 1' if unnamed.")
    text: str = Field(description="The comment, copied verbatim from the report.")


class RefereeCommentList(BaseModel):
    comments: list[RefereeComment]


class CommentAssessment(BaseModel):
    id: str = Field(description="The comment id, e.g. 'R1.3'.")
    accuracy: Accuracy = Field(description="Is the referee right about the manuscript?")
    evidence: str = Field(description="What the manuscript actually says or does, and why that makes the comment correct, partly correct or incorrect.")
    priority: Priority = Field(description="fatal: undermines the main result; major: needs new analysis or substantial rewriting; minor: local fix; trivial: typo or wording.")
    effort: Literal["small", "medium", "large"]
    page: int = Field(description="1-based PDF page the comment is about, or 0 if general / no PDF.")
    source_line: str = Field(description="'file:line' in the LaTeX source the comment is about, or ''.")
    quote: str = Field(description="Exact short passage (5-25 words) of the manuscript the comment is about, copied verbatim, or '' if the comment is general.")
    related: list[str] = Field(description="Ids of comments from OTHER reports that raise the same or a conflicting point.")
    suggested_response: str = Field(description="A polite, specific reply to the referee, ready to paste into the response letter.")
    suggested_edit: str = Field(description="When LaTeX source is given: the concrete change, as 'file:line: <old text> -> <new text>' or a new paragraph to insert; otherwise ''.")


class AssessmentList(BaseModel):
    assessments: list[CommentAssessment]


class RefereeOverview(BaseModel):
    summary: str = Field(description="3-6 sentences: what the referees agree on, where they conflict, and what matters most for acceptance.")
    plan: list[RevisionStep] = Field(description="Ordered revision plan, most important first, citing comment ids.")


# --- Resubmission check ---------------------------------------------------

Status = Literal["addressed", "partly_addressed", "not_addressed"]


class CommentStatus(BaseModel):
    id: str = Field(description="The referee comment id, e.g. 'R1.3'.")
    status: Status = Field(description="Did the revision deal with the comment? A change counts only if it appears in the CHANGES list or is visible in the revised manuscript.")
    priority: Priority = Field(description="fatal / major / minor / trivial, as for the original comment.")
    evidence: str = Field(description="What changed (citing change ids such as 'C4') or what is still the same, and why that makes the comment addressed or not.")
    page: int = Field(description="1-based page of the REVISED PDF where the change (or the unchanged problem) is, or 0.")
    source_line: str = Field(description="'file:line' in the REVISED LaTeX source, or ''.")
    quote: str = Field(description="Exact short passage (5-25 words) of the REVISED manuscript showing the change or the problem, or ''.")
    remaining: str = Field(description="What still needs doing before resubmission; '' if fully addressed.")
    response: str = Field(description="Paragraph for the response letter: thank the referee briefly, say what was changed and where (section, page, equation). If not addressed, write the reply the authors would need and mark the missing part with [TO DO].")


class StatusList(BaseModel):
    statuses: list[CommentStatus]


class UnrequestedChange(BaseModel):
    change: str = Field(description="Change id(s) from the CHANGES list, e.g. 'C7' or 'C7, C8'.")
    what: str = Field(description="One sentence: what was changed.")
    note: str = Field(description="Why the editor or referees may ask about it, or 'harmless' for wording and typo fixes.")


class ResubmitOverview(BaseModel):
    summary: str = Field(description="3-6 sentences: is the revision ready to resubmit, and what must still be done first.")
    unrequested: list[UnrequestedChange] = Field(description="Substantive changes in the CHANGES list that no referee comment asked for. Skip pure typo or wording fixes.")
    letter_opening: str = Field(description="Opening paragraph of the response letter to the editor: thanks, and a 2-4 sentence summary of the main changes.")


ClaimVerdict = Literal["confirmed", "partly", "not_found", "contradicted"]


class LetterClaim(BaseModel):
    comment: str = Field(description="Referee comment id the claim answers, e.g. 'R1.3', or '' if general.")
    claim: str = Field(description="What the authors' letter says was done, quoted or closely paraphrased.")
    verdict: ClaimVerdict = Field(description="confirmed: the change is there; partly: only some of it; not_found: no such change in the CHANGES list or the revised text; contradicted: the revised text says something different.")
    evidence: str = Field(description="Change ids (e.g. 'C4') and what they show, or what the revised text actually says.")
    page: int = Field(description="1-based page of the REVISED PDF where the claimed change is (or should be), or 0.")
    quote: str = Field(description="Exact short passage (5-25 words) of the REVISED manuscript showing it, or ''.")


class LetterCheck(BaseModel):
    claims: list[LetterClaim]
    unanswered: list[str] = Field(description="Ids of referee comments the letter does not answer at all.")
