"""GPT and Gemini backends, against a fake server (no key, no cost).

Each test checks the request the real SDK builds (paper, task, schema,
effort) and that the answer and token usage are read back correctly.
"""

import json
from pathlib import Path

import httpx
import pytest

from freereport import backends, estimate
from freereport.paper import Paper
from freereport.pipeline import Options

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_paper.pdf"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def _paper():
    return Paper(name="sample", pdf_path=SAMPLE, tex_lines=[("main.tex", 3, "x = 1")],
                 figures=[("fig.png", "image/png", b"\x89PNG")])


def _sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def test_provider_is_read_from_the_model_name():
    assert backends.provider("claude-opus-5-5") == "anthropic"
    assert backends.provider("gpt-6.1-sol") == "openai"
    assert backends.provider("gemini-4-argon") == "google"


def test_missing_key_says_which_one(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        backends.make_backend(_paper(), "gpt-6.1-sol")


def test_openai_request_and_usage(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    sent = {}

    def server(request):
        sent.update(json.loads(request.content))
        done = {"id": "r1", "object": "response", "created_at": 0, "model": "gpt-6.1-sol", "status": "completed",
                "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": '{"ok": true}', "annotations": []}]}],
                "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800},
                          "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 1050},
                "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse({"type": "response.created", "sequence_number": 0, "response": {**done, "status": "in_progress"}},
                                           {"type": "response.completed", "sequence_number": 1, "response": done}))

    b = backends.make_backend(_paper(), "gpt-6.1-sol", "max")
    b.client = openai.OpenAI(api_key="test", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(server)))
    assert b.ask("adversary", "system prompt", "the task", SCHEMA) == {"ok": True}

    content = sent["input"][0]["content"]
    assert content[0]["type"] == "input_file" and content[0]["file_data"].startswith("data:application/pdf;base64,")
    assert "main.tex:3| x = 1" in content[1]["text"] and content[3]["type"] == "input_image"
    assert content[-1]["text"] == "the task" and sent["instructions"] == "system prompt"
    assert sent["reasoning"]["effort"] == "max" and sent["text"]["format"]["schema"] == SCHEMA
    assert b.usage.calls[0] == {"agent": "adversary", "input": 200, "output": 50, "cache_write": 0, "cache_read": 800}
    assert b.usage.cost_usd("gpt-6.1-sol") == pytest.approx((200 * 2 + 50 * 10 + 800 * 0.10) / 1e6)


def test_gemini_request_and_usage(monkeypatch):
    from google import genai
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    sent = {}

    def server(request):
        sent.update(json.loads(request.content))
        chunk = {"candidates": [{"content": {"role": "model", "parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
                 "usageMetadata": {"promptTokenCount": 1000, "cachedContentTokenCount": 600,
                                   "candidatesTokenCount": 20, "thoughtsTokenCount": 30}}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(chunk))

    b = backends.make_backend(_paper(), "gemini-4-argon", "max")
    b.client = genai.Client(api_key="test", http_options=genai.types.HttpOptions(
        httpx_client=httpx.Client(transport=httpx.MockTransport(server))))
    assert b.ask("adversary", "system prompt", "the task", SCHEMA) == {"ok": True}

    parts = sent["contents"][0]["parts"]
    pdf = parts[0]["inlineData"]
    assert pdf.get("mimeType", pdf.get("mime_type")) == "application/pdf"
    assert "main.tex:3| x = 1" in parts[1]["text"] and parts[-1]["text"] == "the task"
    config = sent["generationConfig"]
    assert config["responseJsonSchema"] == SCHEMA
    thinking = config["thinkingConfig"]
    assert thinking.get("thinkingLevel", thinking.get("thinking_level")).lower() == "high"  # "max" maps to Gemini's highest level
    assert sent["systemInstruction"]["parts"][0]["text"] == "system prompt"
    assert b.usage.calls[0] == {"agent": "adversary", "input": 400, "output": 50, "cache_write": 0, "cache_read": 600}


def test_estimate_for_gpt_and_gemini_has_no_guaranteed_cache():
    calls = estimate.plan_check(Options())
    for model in ("gpt-6.1-sol", "gemini-4-argon"):
        est = estimate.estimate(272_000, calls, model)
        assert est.low < est.high
        p = backends.PRICES[model]
        assert est.high >= 272_000 * 3 * p["input"] / 1e6  # every call may pay full price for the paper
