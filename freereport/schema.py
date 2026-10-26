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
    page: int = Field(description="1-based page number of the PDF where the problem is.")
    location: str = Field(description="Where in the paper, e.g. 'Equation (7)', 'Table 2, row 3', 'Proof of Lemma 1, line 4'.")
    quote: str = Field(description="Exact text copied from the paper at that spot (at most ~40 words).")
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
