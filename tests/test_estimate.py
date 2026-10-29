"""Cost estimate before a run (no API calls, no cost)."""

from pathlib import Path

from freereport import cli, estimate
from freereport.pipeline import Options

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_paper.pdf"


def test_more_modes_cost_more_and_the_range_is_ordered():
    base = estimate.estimate(271_579, estimate.plan_check(Options()), "claude-opus-5-5")
    full = estimate.estimate(271_579, estimate.plan_check(Options(deepdive=True, blindspot=True, council=True)),
                             "claude-opus-5-5")
    assert 0 < base.low < base.high < full.high and base.low < full.low
    assert len(base.calls) == 3 and len(full.calls) == 10
    # a real 272k-token paper cost $3.70 with the baseline: the range must contain it
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


def _fake_backend(tokens):
    class Fake:
        model, usage = "claude-opus-5-5", None

        def __init__(self, paper, model, effort):
            pass

        def count_tokens(self, system, extra=""):
            return tokens
    return Fake


def test_too_large_paper_is_refused_before_any_call(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "ClaudeBackend", _fake_backend(500_000))

    def audit(*a, **k):  # record the paid run, then stop before writing reports
        calls.append(a)
        raise RuntimeError("stop")
    monkeypatch.setattr(cli, "audit", audit)
    assert cli.main(["check", str(SAMPLE), "--yes"]) == 1
    err = capsys.readouterr().err
    assert "Paper too large: 500k tokens" in err and "limit is 400k" in err and "pages" in err
    assert calls == []
    cli.main(["check", str(SAMPLE), "--yes", "--max-tokens", "600000", "--max-cost", "100"])  # raised limits: runs
    assert len(calls) == 1


def test_price_cap_refuses_expensive_runs(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "ClaudeBackend", _fake_backend(300_000))
    monkeypatch.setattr(cli, "audit", lambda *a, **k: calls.append(a))
    assert cli.main(["check", str(SAMPLE), "--yes", "--council", "--max-cost", "10"]) == 1
    assert "above the $10 cap" in capsys.readouterr().err and calls == []


def test_limits_apply_per_paper_not_to_a_batch_total(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "ClaudeBackend", _fake_backend(300_000))
    monkeypatch.setattr(cli, "audit", lambda *a, **k: calls.append(a))
    cli.main(["check", str(SAMPLE), str(SAMPLE), "--yes", "--estimate"])  # 600k in total, but each is 300k
    assert calls == []  # --estimate: nothing runs, and no limit error either


def test_size_split_names_figures():
    from freereport.paper import Paper
    paper = Paper(name="x", pages=["p"] * 10, figures=[("f.png", "image/png", b"")] * 40)
    paper.pdf_path = Path("x.pdf")
    est = estimate.estimate(500_000, estimate.plan_check(Options()), "claude-opus-5-5")
    msg = estimate.limit_problem(paper, est, 400_000, 15)
    assert "40 figures" in msg and "removing some figures" in msg
