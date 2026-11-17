"""Model backends.

Each backend answers one question: "given the paper and a task, return JSON
matching this schema". Claude, GPT (OpenAI) and Gemini (Google) are supported;
the company is chosen from the model name. Each user brings their own key.
"""

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import anthropic

from .paper import Paper

# US dollars per million tokens, list prices checked October 2026.
# implicit_cache: the provider caches the paper on its own, with no guarantee,
# so the high end of the estimate assumes every call pays full price for it.
PRICES = {
    # cache_write keeps the paper ready for 5 minutes, cache_write_1h for an hour
    "claude-fable-5-2": {"input": 8.00, "output": 40.00, "cache_write": 10.00, "cache_write_1h": 16.00, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_write_1h": 8.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20},
    "gpt-6-astra": {"input": 10.00, "output": 50.00, "cache_write": 10.00, "cache_read": 1.00, "implicit_cache": True},
    "gpt-6.1-sol": {"input": 2.00, "output": 10.00, "cache_write": 2.00, "cache_read": 0.10, "implicit_cache": True},
    # Google doubles these from 1 December 2026 (to $4 / $20 / $0.20)
    "gemini-4-argon": {"input": 2.00, "output": 10.00, "cache_write": 2.00, "cache_read": 0.10, "implicit_cache": True},
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cache_write": 0.75, "cache_read": 0.075, "implicit_cache": True},
}

ANSWER_LIMIT = 128_000  # the longest answer (thinking included) one step may give: Claude's maximum
CONTEXT = 1_000_000  # the most the model can read in one step: the paper, the task and the answer together

KEYS = {"anthropic": ["ANTHROPIC_API_KEY"], "openai": ["OPENAI_API_KEY"], "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]}


# Steps that sort, summarise or match text think at medium effort; finding and
# verifying errors, judging referee claims and the council keep the chosen level.
LIGHT_STEPS = ("referee_split", "referee_overview", "resubmit_split", "resubmit_status", "resubmit_overview", "resubmit_letter")
_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def effort_for(label: str, effort: str) -> str:
    """The effort for one step: never above medium for the light steps, never above what the user chose."""
    if label.startswith(LIGHT_STEPS) and effort in _LEVELS and _LEVELS.index(effort) > _LEVELS.index("medium"):
        return "medium"
    return effort


def provider(model: str) -> str:
    """Which company serves this model, from its name."""
    if model.startswith("gpt-") or model[:2] in {"o1", "o3", "o4"}:
        return "openai"
    if model.startswith("gemini-"):
        return "google"
    return "anthropic"


def make_backend(paper: Paper, model: str = "claude-opus-5-5", effort: str = "high"):
    """The backend for this model's company. Fails early, with what to do, if its key is missing."""
    company = provider(model)
    if not any(os.environ.get(k) for k in KEYS[company]):
        raise RuntimeError(f"{model} needs an API key from {company.capitalize()}: set {KEYS[company][0]}"
                           " (see the README, 'Set your API key').")
    if company == "openai":
        return OpenAIBackend(paper, model, effort)
    if company == "google":
        return GeminiBackend(paper, model, effort)
    return ClaudeBackend(paper, model, effort)


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    calls: list[dict] = field(default_factory=list)
    discount: float = 1.0  # 0.5 in overnight (batch) mode: every token costs half

    def add(self, label: str, u) -> None:
        """Add a Claude usage object."""
        written = u.cache_creation_input_tokens or 0
        hour = getattr(getattr(u, "cache_creation", None), "ephemeral_1h_input_tokens", 0) or 0
        self.add_counts(label, u.input_tokens, u.output_tokens, written - hour, u.cache_read_input_tokens or 0, hour)

    def add_counts(self, label: str, input: int, output: int, cache_write: int = 0, cache_read: int = 0,
                   cache_write_1h: int = 0) -> None:
        row = {"agent": label, "input": input, "output": output, "cache_write": cache_write, "cache_read": cache_read}
        if cache_write_1h:
            row["cache_write_1h"] = cache_write_1h
        self.cache_write_1h += cache_write_1h
        self.calls.append(row)
        self.input += row["input"]
        self.output += row["output"]
        self.cache_write += row["cache_write"]
        self.cache_read += row["cache_read"]

    def cost_usd(self, model: str) -> float | None:
        p = PRICES.get(model)
        if p is None:
            return None
        return (
            self.input * p["input"]
            + self.output * p["output"]
            + self.cache_write * p["cache_write"]
            + self.cache_write_1h * p.get("cache_write_1h", p["cache_write"])
            + self.cache_read * p["cache_read"]
        ) * self.discount / 1_000_000


class RefusedError(RuntimeError):
    pass


class Backend(Protocol):
    model: str
    usage: Usage

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict: ...


class ClaudeBackend:
    """Sends the whole paper to Claude as document blocks: the PDF, the
    LaTeX source, or both.

    The system prompt and the paper form a fixed prefix, cached after the
    first call, so later agents pay about a tenth of the price for it.
    """

    def __init__(self, paper: Paper, model: str = "claude-opus-5-5", effort: str = "high"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.usage = Usage()
        self._documents = []
        if paper.pdf_path:
            pdf_b64 = base64.standard_b64encode(paper.pdf_path.read_bytes()).decode()
            self._documents.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                "title": "Manuscript (PDF)",
            })
        if paper.tex_lines:
            self._documents.append({
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": paper.tex_numbered},
                "title": "Manuscript (LaTeX source, 'line|' prefixes under '=== file: name ===' headers)",
            })
        for name, media_type, data in paper.figures:
            self._documents.append({"type": "text", "text": f"Figure file: {name}"})
            self._documents.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()},
            })
        self.cache_ttl = "5m"  # "1h" for runs that re-read the paper many times (set by the cost estimate)
        self.overnight = False  # True: send each call through the Batch API at half price; answers take minutes to hours
        self.poll_seconds = 60
        self.batch_ticket = None  # where the queued request's id is kept, set per step by steps.keep
        if self._documents:  # nothing to cache in a light check that sends no manuscript
            self._documents[-1]["cache_control"] = {"type": "ephemeral"}

    def count_tokens(self, system: str, extra: str = "") -> int:
        """Exact input size of the paper (plus `extra`), from the API's free token counter."""
        content = [*self._documents, {"type": "text", "text": extra or "."}]
        return self.client.messages.count_tokens(
            model=self.model, system=system, messages=[{"role": "user", "content": content}]
        ).input_tokens

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict:
        extra_body, extra_headers = {}, {}
        if self.model.startswith(("claude-opus", "claude-fable", "claude-sonnet-5-5")):
            # On a safety decline, retry server-side on Anthropic's recommended model.
            extra_body["fallbacks"] = "default"
            extra_headers["anthropic-beta"] = "server-side-fallback-2026-07-01"

        if self._documents:
            self._documents[-1]["cache_control"] = {"type": "ephemeral", "ttl": self.cache_ttl}
        params = dict(
            model=self.model,
            max_tokens=ANSWER_LIMIT,
            system=system,
            output_config={"effort": effort_for(label, self.effort), "format": {"type": "json_schema", "schema": schema}},
            messages=[{
                "role": "user",
                "content": [*self._documents, {"type": "text", "text": task}],
            }],
        )
        if self.overnight:
            message = self._batch(label, params)  # the Batch API does not take the safety fallback
        else:
            with self.client.messages.stream(**params, extra_body=extra_body or None,
                                             extra_headers=extra_headers or None) as stream:
                message = stream.get_final_message()

        self.usage.add(label, message.usage)
        if message.stop_reason == "refusal":
            raise RefusedError(f"{label}: model declined ({message.stop_details})")
        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"{label}: output was cut off at max_tokens ({message.usage.output_tokens:,} tokens)")
        text = next(b.text for b in message.content if b.type == "text")
        return json.loads(text)


    def _batch(self, label: str, params: dict):
        """One call through the Batch API: half price, answered within 24 hours (usually under one)."""
        from anthropic.types.messages.batch_create_params import Request

        # A request already in the queue (from a run that stopped while waiting) is picked up, not sent again.
        ticket = self.batch_ticket
        if ticket is not None and ticket.is_file():
            batch_id_ = ticket.read_text(encoding="utf-8").strip()
        else:
            batch_id_ = self.client.messages.batches.create(requests=[Request(custom_id=batch_id(label), params=params)]).id
            if ticket is not None:
                ticket.parent.mkdir(parents=True, exist_ok=True)
                ticket.write_text(batch_id_, encoding="utf-8")
        status, result = "", None
        while result is None:
            try:  # the wait can last hours: an internet drop only means asking again later
                if status != "ended":
                    status = self.client.messages.batches.retrieve(batch_id_).processing_status
                if status == "ended":
                    result = next(iter(self.client.messages.batches.results(batch_id_))).result
                    break
            except (anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.RateLimitError):
                pass
            time.sleep(self.poll_seconds)
        if ticket is not None:
            ticket.unlink(missing_ok=True)
        if result.type != "succeeded":
            detail = getattr(getattr(result, "error", None), "error", None) or result.type
            raise RuntimeError(f"{label}: overnight request did not finish ({detail})")
        return result.message


def batch_id(label: str) -> str:
    """A step name the Batch API accepts: letters, digits, _ and - only, at most 64 characters
    (council roles have spaces, e.g. "council_First Principles")."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", label)[:64]


def _need(module: str, extra: str):
    try:
        return __import__(module, fromlist=["_"])
    except ImportError:
        raise RuntimeError(f"This model needs an extra package: pip install -e \".[{extra}]\"") from None


class OpenAIBackend:
    """GPT models through OpenAI's Responses API. The PDF is sent as a file
    (OpenAI reads its text and page images); OpenAI caches the repeated paper
    on its own, and cached reads cost a tenth.
    """

    def __init__(self, paper: Paper, model: str, effort: str = "high"):
        openai = _need("openai", "openai")
        self.client = openai.OpenAI(timeout=1800)
        self.model = model
        self.effort = effort
        self.usage = Usage()
        self._documents = []
        if paper.pdf_path:
            pdf_b64 = base64.standard_b64encode(paper.pdf_path.read_bytes()).decode()
            self._documents.append({"type": "input_file", "filename": paper.pdf_path.name,
                                    "file_data": f"data:application/pdf;base64,{pdf_b64}"})
        if paper.tex_lines:
            self._documents.append({"type": "input_text",
                                    "text": "Manuscript (LaTeX source, 'line|' prefixes under '=== file: name ===' headers):\n" + paper.tex_numbered})
        for name, media_type, data in paper.figures:
            self._documents.append({"type": "input_text", "text": f"Figure file: {name}"})
            self._documents.append({"type": "input_image",
                                    "image_url": f"data:{media_type};base64,{base64.standard_b64encode(data).decode()}"})
        self._cache_key = f"freereport-{paper.name}"[:64]

    def _input(self, text: str) -> list[dict]:
        return [{"role": "user", "content": [*self._documents, {"type": "input_text", "text": text}]}]

    def count_tokens(self, system: str, extra: str = "") -> int:
        return self.client.responses.input_tokens.count(
            model=self.model, instructions=system, input=self._input(extra or ".")).input_tokens

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict:
        with self.client.responses.stream(
            model=self.model,
            instructions=system,
            input=self._input(task),
            reasoning={"effort": effort_for(label, self.effort)},
            text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}},
            max_output_tokens=128_000,
            prompt_cache_key=self._cache_key,
        ) as stream:
            response = stream.get_final_response()

        u = response.usage
        cached = u.input_tokens_details.cached_tokens or 0
        self.usage.add_counts(label, u.input_tokens - cached, u.output_tokens, 0, cached)
        if response.status == "incomplete":
            raise RuntimeError(f"{label}: output was cut off ({response.incomplete_details.reason})")
        for item in response.output:
            for part in getattr(item, "content", None) or []:
                if part.type == "refusal":
                    raise RefusedError(f"{label}: model declined ({part.refusal})")
        return json.loads(response.output_text)


class GeminiBackend:
    """Gemini models through Google's generateContent API. The PDF is sent
    as a document; Google caches the repeated paper on its own.
    """

    LEVELS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

    def __init__(self, paper: Paper, model: str, effort: str = "high"):
        genai = _need("google.genai", "gemini")
        self.types = genai.types
        self.client = genai.Client(http_options=self.types.HttpOptions(timeout=1_800_000))
        self.model = model
        self.effort = effort
        self.usage = Usage()
        t = self.types
        self._documents = []
        if paper.pdf_path:
            self._documents.append(t.Part.from_bytes(data=paper.pdf_path.read_bytes(), mime_type="application/pdf"))
        if paper.tex_lines:
            self._documents.append(t.Part.from_text(
                text="Manuscript (LaTeX source, 'line|' prefixes under '=== file: name ===' headers):\n" + paper.tex_numbered))
        for name, media_type, data in paper.figures:
            self._documents.append(t.Part.from_text(text=f"Figure file: {name}"))
            self._documents.append(t.Part.from_bytes(data=data, mime_type=media_type))

    def _contents(self, text: str):
        return [self.types.Content(role="user", parts=[*self._documents, self.types.Part.from_text(text=text)])]

    def count_tokens(self, system: str, extra: str = "") -> int:
        return self.client.models.count_tokens(model=self.model, contents=self._contents(system + "\n" + (extra or "."))).total_tokens

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict:
        t = self.types
        config = t.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=t.ThinkingConfig(thinking_level=self.LEVELS.get(effort_for(label, self.effort), "high")),
            max_output_tokens=65_536,
        )
        text, last = [], None
        for chunk in self.client.models.generate_content_stream(model=self.model, contents=self._contents(task), config=config):
            last = chunk
            if chunk.text:
                text.append(chunk.text)

        m = last.usage_metadata if last else None
        if m:
            cached = m.cached_content_token_count or 0
            self.usage.add_counts(label, (m.prompt_token_count or 0) - cached,
                                  (m.candidates_token_count or 0) + (m.thoughts_token_count or 0), 0, cached)
        if last is None or (last.prompt_feedback and last.prompt_feedback.block_reason):
            raise RefusedError(f"{label}: model declined")
        reason = last.candidates[0].finish_reason if last.candidates else None
        if reason == t.FinishReason.MAX_TOKENS:
            raise RuntimeError(f"{label}: output was cut off at max_output_tokens")
        if reason in (t.FinishReason.SAFETY, t.FinishReason.PROHIBITED_CONTENT):
            raise RefusedError(f"{label}: model declined ({reason})")
        return json.loads("".join(text))
