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
    assert "=== file: main.tex ===\n3| x = 1" in content[1]["text"] and content[3]["type"] == "input_image"
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
    assert "=== file: main.tex ===\n3| x = 1" in parts[1]["text"] and parts[-1]["text"] == "the task"
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


def test_light_steps_think_at_medium_and_never_above_the_users_choice():
    assert backends.effort_for("referee_split", "high") == "medium"
    assert backends.effort_for("resubmit_status_R1", "max") == "medium"
    assert backends.effort_for("resubmit_letter", "low") == "low"  # never raised
    assert backends.effort_for("adversary", "high") == "high"  # finding errors keeps the chosen level
    assert backends.effort_for("referee_assess_R1", "high") == "high"
    assert backends.effort_for("council_Methods Referee", "high") == "high"


def test_overnight_sends_each_call_as_a_half_price_batch(monkeypatch):
    import anthropic
    import httpx2

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    sent, polls = {}, []
    batch = {"id": "b1", "type": "message_batch", "created_at": "2026-10-29T00:00:00Z", "expires_at": "2026-10-30T00:00:00Z",
             "request_counts": {"processing": 1, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0},
             "cancel_initiated_at": None, "archived_at": None, "ended_at": None, "results_url": None}
    ended = {**batch, "processing_status": "ended", "results_url": "https://api.anthropic.com/v1/messages/batches/b1/results"}
    message = {"id": "m1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
               "content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}

    def server(request):
        if request.method == "POST":
            sent.update(json.loads(request.content))
            return httpx2.Response(200, json={**batch, "processing_status": "in_progress"})
        if request.url.path.endswith("/results"):
            line = {"custom_id": "adversary", "result": {"type": "succeeded", "message": message}}
            return httpx2.Response(200, content=(json.dumps(line) + "\n").encode())
        polls.append(1)
        return httpx2.Response(200, json=ended)

    b = backends.make_backend(_paper(), "claude-opus-5-5")
    b.client = anthropic.Anthropic(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(server)))
    b.overnight, b.poll_seconds, b.usage.discount = True, 0, 0.5
    assert b.ask("adversary", "system prompt", "the task", SCHEMA) == {"ok": True}

    params = sent["requests"][0]["params"]
    assert sent["requests"][0]["custom_id"] == "adversary" and "fallbacks" not in params
    assert params["messages"][0]["content"][-1]["text"] == "the task" and polls
    assert b.usage.cost_usd("claude-opus-5-5") == pytest.approx((100 * 4 + 1000 * 20) / 1e6 / 2)


def test_overnight_estimate_is_half_and_keeps_the_paper_for_an_hour():
    calls = estimate.plan_check(Options())
    day = estimate.estimate(272_000, calls, "claude-opus-5-5", long_cache=True)
    night = estimate.estimate(272_000, calls, "claude-opus-5-5", long_cache=True, overnight=True)
    assert night.high == pytest.approx(day.high / 2) and "overnight" in night.line()


def test_overnight_is_refused_for_gpt_before_anything_runs(capsys):
    from freereport import cli
    with pytest.raises(SystemExit):
        cli.main(["check", str(SAMPLE), "--model", "gpt-6.1-sol", "--overnight"])
    assert "Claude models" in capsys.readouterr().err


def test_every_step_name_is_accepted_by_the_batch_api():
    import re
    every = Options(deepdive=True, blindspot=True, council=True)
    labels = [c.label for plan in (estimate.plan_check(every), estimate.plan_respond(3),
                                   estimate.plan_resubmit(every, 3, True, False, False, False, 20_000)) for c in plan]
    from freereport.prompts import COUNCIL_ROLES
    labels += [f"council_{role}" for role in COUNCIL_ROLES]
    assert "council_First Principles" in labels
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", backends.batch_id(label)) for label in labels)
