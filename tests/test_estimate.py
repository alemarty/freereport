"""Cost estimate before a run (no API calls, no cost)."""

from pathlib import Path

from freereport import cli, estimate
from freereport.pipeline import Options

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_paper.pdf"


def test_more_modes_cost_more_and_the_range_is_ordered():
    base = estimate.estimate(145_000, estimate.plan_check(Options()), "claude-opus-5-5")
    full = estimate.estimate(145_000, estimate.plan_check(Options(deepdive=True, blindspot=True, council=True)),
                             "claude-opus-5-5")
    assert 0 < base.low < base.high < full.high and base.low < full.low
    assert len(base.calls) == 3 and len(full.calls) == 10
    # a real 145k-token paper cost $3.70 with the baseline: the range must contain it
    assert base.low < 3.70 < base.high


def test_quick_resubmit_does_not_pay_for_the_paper():
    quick = estimate.plan_resubmit(Options(), 2, False, quick=True, no_check=False, reuse_comments=False, changes_tokens=5_000)
    full = estimate.plan_resubmit(Options(), 2, False, quick=False, no_check=False, reuse_comments=False, changes_tokens=5_000)
    assert not any(c.reads_paper for c in quick)
    assert estimate.estimate(145_000, quick, "claude-opus-5-5").high < estimate.estimate(145_000, full, "claude-opus-5-5").low


def test_unknown_model_has_no_price():
    assert "unknown" in estimate.estimate(1000, [estimate.Call("x", (1, 2))], "some-local-model").line()


def test_answering_no_spends_nothing(monkeypatch, capsys):
    calls = []

    class Fake:
        model, usage = "claude-opus-5-5", None

        def __init__(self, paper, model, effort):
            pass

        def count_tokens(self, system, extra=""):
            return 2_000

    monkeypatch.setattr(cli, "ClaudeBackend", Fake)
    monkeypatch.setattr(cli, "audit", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.main(["check", str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "Estimated cost: $" in out and "paper 2k tokens" in out and "Nothing was spent" in out
    assert calls == []

    cli.main(["check", str(SAMPLE), "--estimate"])  # only the price, never asks
    assert calls == []


def test_confirm_without_keyboard_says_no():
    est = estimate.estimate(1000, [estimate.Call("x", (1, 2))], "claude-opus-5-5")

    def no_keyboard(prompt):
        raise EOFError

    assert estimate.confirm(est, assume_yes=False, ask=no_keyboard) is False
    assert estimate.confirm(est, assume_yes=True, ask=no_keyboard) is True
