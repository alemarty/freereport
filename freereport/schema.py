"""Data shapes shared by every agent and backend."""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["math", "logic", "data", "consistency"]
Severity = Literal["critical", "major", "minor"]
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """One suspected error, written by Agent 1 or Agent 2."""

    id: str = Field(description="Short id, e.g. 'M1' for math or 'D3' for data.")
    category: Category
    page: int = Field(description="1-based page number of the PDF where the problem is, or 0 if no PDF was given.")
    source_line: str = Field(description="'file:line' in the LaTeX source, e.g. 'main.tex:212', or '' if no LaTeX source was given.")
    location: str = Field(description="Where in the paper, e.g. 'Equation (7)', 'Table 2, row 3', 'Proof of Lemma 1, line 4'.")
    quote: str = Field(description="Exact text copied from the paper (PDF text or LaTeX source) at that spot, at most ~40 words.")
    problem: str = Field(description="One or two sentences: what is wrong.")
    check: str = Field(description="The step-by-step working that shows it is wrong, so a human can verify it.")
    suggested_fix: str
    severity: Severity
    confidence: Confidence


class FindingList(BaseModel):
    findings: list[Finding]


class Verdict(BaseModel):
    """Agent 3's decision on one candidate finding."""

    id: str
    keep: bool
    reason: str = Field(description="Why the finding was kept or rejected.")
    duplicate_of: str = Field(description="If this candidate reports the same problem as another candidate, that candidate's id (and keep must be false); otherwise ''.")
    severity: Severity
    confidence: Confidence


class Synthesis(BaseModel):
    verdicts: list[Verdict]
    summary: str = Field(description="3-6 sentence overall assessment of the paper's correctness.")


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


class CouncilDecision(BaseModel):
    verdict: str = Field(description="2-4 sentences: is the paper ready to submit, and what matters most.")
    action_plan: list[RevisionStep] = Field(description="Ordered revision steps, most important first.")


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
