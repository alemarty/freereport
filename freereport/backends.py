"""Model backends.

Each backend answers one question: "given the paper and a task, return JSON
matching this schema". Step 3 of the plan adds an OpenAI-compatible backend
for vLLM / Ollama on a university cluster, behind the same interface.
"""

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import anthropic

# US dollars per million tokens, Claude Opus 5.5 list prices.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
}


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    calls: list[dict] = field(default_factory=list)

    def add(self, label: str, u) -> None:
        row = {
            "agent": label,
            "input": u.input_tokens,
            "output": u.output_tokens,
            "cache_write": u.cache_creation_input_tokens or 0,
            "cache_read": u.cache_read_input_tokens or 0,
        }
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
            + self.cache_read * p["cache_read"]
        ) / 1_000_000


class RefusedError(RuntimeError):
    pass


class Backend(Protocol):
    model: str
    usage: Usage

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict: ...


class ClaudeBackend:
    """Sends the whole PDF to Claude as a document block.

    The system prompt and the PDF form a fixed prefix, cached after the
    first call, so agents 2 and 3 pay about a tenth of the price for it.
    """

    def __init__(self, pdf_path: Path, model: str = "claude-opus-5-5", effort: str = "high"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.usage = Usage()
        self._pdf_b64 = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode()

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict:
        extra_body, extra_headers = {}, {}
        if self.model.startswith(("claude-opus", "claude-fable", "claude-sonnet-5-5")):
            # On a safety decline, retry server-side on Anthropic's recommended model.
            extra_body["fallbacks"] = "default"
            extra_headers["anthropic-beta"] = "server-side-fallback-2026-07-01"

        with self.client.messages.stream(
            model=self.model,
            max_tokens=64000,
            system=system,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": self._pdf_b64},
                        "title": "Manuscript",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": task},
                ],
            }],
            extra_body=extra_body or None,
            extra_headers=extra_headers or None,
        ) as stream:
            message = stream.get_final_message()

        self.usage.add(label, message.usage)
        if message.stop_reason == "refusal":
            raise RefusedError(f"{label}: model declined ({message.stop_details})")
        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"{label}: output was cut off at max_tokens")
        text = next(b.text for b in message.content if b.type == "text")
        return json.loads(text)
