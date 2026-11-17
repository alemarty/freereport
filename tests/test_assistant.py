import json

from freereport import cli
from test_pipeline import SAMPLE, FakeBackend


def test_assistant_mode_hands_out_tasks_then_writes_the_report(tmp_path, capsys):
    """Each run names one task; once every answer is saved, the report is written."""
    work, out, fake = tmp_path / "work", tmp_path / "reports", FakeBackend()
    argv = ["assistant", str(SAMPLE), "--work", str(work), "--out", str(out), "--council", "--journal", "JIE"]
    steps = []
    while (code := cli.main(argv)) == 3:
        label = capsys.readouterr().out.split("): ")[1].split("\n")[0]
        name = label.replace(" ", "_")
        task = (work / "tasks" / f"{name}.md").read_text()
        assert "## Your task" in task and str(work / "answers" / f"{name}.json") in task and "paper.pdf" in task
        (work / "answers" / f"{name}.json").write_text(json.dumps(fake.ask(label, "", task, {})))
        steps.append(label)
        assert len(steps) < 20
    assert code == 0 and steps[:3] == ["adversary", "data_auditor", "synthesis"] and steps[-1] == "council_executive"
    assert "DONE. 2 issue(s) found." in capsys.readouterr().out
    assert (out / "sample_paper.pdf").exists() and "Missing 2ab term" in (out / "sample_paper.md").read_text()


def test_assistant_mode_explains_a_bad_answer(tmp_path, capsys):
    work = tmp_path / "work"
    argv = ["assistant", str(SAMPLE), "--work", str(work), "--out", str(tmp_path)]
    assert cli.main(argv) == 3
    (work / "answers" / "adversary.json").write_text('{"findings": [{"id": "M1"}]}')
    assert cli.main(argv) == 2
    assert "does not follow the schema" in capsys.readouterr().err
