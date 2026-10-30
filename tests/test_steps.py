"""A run that stops halfway keeps its finished steps, so running it again does not pay twice."""

from pathlib import Path

from freereport import cli, steps

from test_pipeline import FakeBackend

SAMPLE = Path(__file__).parents[1] / "examples" / "sample_paper.pdf"


def test_a_failed_run_is_resumed_without_paying_again(monkeypatch, tmp_path, capsys):
    asked, fail = [], {"synthesis": True}

    class Backend(FakeBackend):
        effort = "high"

        def __init__(self, paper, model, effort):
            super().__init__()

        def count_tokens(self, system, extra=""):
            return 2_000

        def ask(self, label, system, task, schema):
            asked.append(label)
            if fail.get(label):
                raise RuntimeError(f"{label}: output was cut off at max_tokens")
            return super().ask(label, system, task, schema)

    monkeypatch.setattr(cli, "make_backend", Backend)
    args = ["check", str(SAMPLE), "--yes", "--out", str(tmp_path)]
    assert cli.main(args) == 1
    assert asked == ["adversary", "data_auditor", "synthesis"]
    assert "2 finished step(s) are saved" in capsys.readouterr().err

    fail["synthesis"], asked[:] = False, []
    assert cli.main(args) == 0
    assert asked == ["synthesis"]  # the two finished steps came from disk
    assert "already paid" in capsys.readouterr().out
    assert (tmp_path / "sample_paper.json").is_file() and not (tmp_path / steps.FOLDER).exists()


def test_a_changed_paper_is_not_answered_from_an_old_run(tmp_path):
    from freereport.paper import Paper
    b1, b2 = FakeBackend(), FakeBackend()
    b1.effort = b2.effort = "high"
    f1 = steps.keep(b1, Paper(name="p", tex_lines=[("main.tex", 1, "x = 1")]), tmp_path)
    f2 = steps.keep(b2, Paper(name="p", tex_lines=[("main.tex", 1, "x = 2")]), tmp_path)
    assert f1 != f2
    b1.ask("adversary", "s", "t", {})
    assert steps.saved(f1) == 1 and steps.saved(f2) == 0


def test_nothing_is_paid_when_the_ai_service_cannot_be_reached(monkeypatch, tmp_path, capsys):
    asked = []

    class Offline(FakeBackend):
        effort = "high"

        def __init__(self, paper, model, effort):
            super().__init__()

        def count_tokens(self, system, extra=""):
            raise RuntimeError("invalid x-api-key")

        def ask(self, *a):
            asked.append(a)

    monkeypatch.setattr(cli, "make_backend", Offline)
    assert cli.main(["check", str(SAMPLE), "--yes", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "invalid x-api-key" in err and "Nothing was spent" in err and asked == []


def test_nothing_is_paid_when_the_reports_cannot_be_written(monkeypatch, tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")

    class Backend(FakeBackend):
        effort = "high"

        def __init__(self, paper, model, effort):
            super().__init__()

        def count_tokens(self, system, extra=""):
            return 2_000

    monkeypatch.setattr(cli, "make_backend", Backend)
    assert cli.main(["check", str(SAMPLE), "--yes", "--out", str(blocked / "reports")]) == 1
    assert "Cannot write the reports" in capsys.readouterr().err


def test_every_planned_step_fits_in_one_answer():
    from freereport import estimate
    from freereport.backends import ANSWER_LIMIT
    from freereport.pipeline import Options
    every = Options(deepdive=True, blindspot=True, council=True)
    plans = [estimate.plan_check(every), estimate.plan_respond(3),
             estimate.plan_resubmit(every, 3, True, False, False, False, 20_000)]
    assert all(c.out[1] <= ANSWER_LIMIT for plan in plans for c in plan)
