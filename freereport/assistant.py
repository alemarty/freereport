"""Running a check through an AI assistant (Codex with a ChatGPT plan, Claude Code with a
Claude plan) instead of an API key.

The assistant does the reviewers' work; this code hands out one task at a time and builds
the report. Each run of `freereport assistant` replays the answers saved so far and stops at
the first step that has none: it writes that step's task to a file and says where to save the
answer. Once every step has an answer, it writes the report exactly as `freereport check` does.
"""

import json
import shutil
from pathlib import Path

from pydantic import ValidationError

from .backends import Usage, batch_id
from .paper import Paper
from .pipeline import Options, audit

MODEL = "Codex or Claude Code"


class NeedAnswer(Exception):
    """The step `label` has no saved answer yet."""

    def __init__(self, label: str, task_file: Path, answer_file: Path):
        super().__init__(label)
        self.label, self.task_file, self.answer_file = label, task_file, answer_file


class BadAnswer(Exception):
    pass


def write_paper(paper: Paper, work: Path) -> list[str]:
    """Put the paper where the assistant can read it: the numbered LaTeX, the PDF, the figures.
    Returns one line per file for the task, e.g. '`work/paper.pdf` (...)'."""
    work.mkdir(parents=True, exist_ok=True)
    files = []
    if paper.tex_lines:
        (work / "paper_source.txt").write_text(paper.tex_numbered, encoding="utf-8")
        files.append(f"`{work / 'paper_source.txt'}`")
    if paper.pages:  # the PDF's text by page, for assistants that cannot open a PDF
        text = "\n\n".join(f"=== PDF page {n} ===\n{t}" for n, t in enumerate(paper.pages, 1))
        (work / "paper_pages.txt").write_text(text, encoding="utf-8")
        files.append(f"`{work / 'paper_pages.txt'}` (the PDF's text, page by page)")
    if paper.pdf_path:
        shutil.copyfile(paper.pdf_path, work / "paper.pdf")
        files.append(f"`{work / 'paper.pdf'}` (the same pages as typeset, if you can open PDFs)")
    if paper.figures:
        (work / "figures").mkdir(exist_ok=True)
        for name, media_type, data in paper.figures:
            ext = "." + media_type.split("/")[-1].replace("jpeg", "jpg")
            out = work / "figures" / (Path(name).stem + ext)
            out.write_bytes(data)
        files.append(f"`{work / 'figures'}/` (the figure images)")
    return files


class AssistantBackend:
    """Answers each step from work/answers/<step>.json, or stops and writes the task for it."""

    def __init__(self, work: Path, paper_files: list[str]):
        self.model = MODEL
        self.usage = Usage()
        self.work = work
        self.paper_files = paper_files
        self.done: list[str] = []

    def ask(self, label: str, system: str, task: str, schema: dict) -> dict:
        name = batch_id(label)  # a safe file name, e.g. council_First_Principles
        answer = self.work / "answers" / f"{name}.json"
        if not answer.exists():
            (self.work / "tasks").mkdir(parents=True, exist_ok=True)
            (self.work / "answers").mkdir(parents=True, exist_ok=True)
            task_file = self.work / "tasks" / f"{name}.md"
            task_file.write_text(self._task_text(label, system, task, schema, answer), encoding="utf-8")
            raise NeedAnswer(label, task_file, answer)
        try:
            data = json.loads(answer.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BadAnswer(f"{answer} is not valid JSON ({exc}). Fix it, or delete it to redo the step.") from exc
        self.done.append(name)
        return data

    def _task_text(self, label: str, system: str, task: str, schema: dict, answer: Path) -> str:
        papers = "\n".join(f"- {f}" for f in self.paper_files)
        return f"""# FreeReport step: {label}

## The manuscript

Read the whole manuscript from these files before answering:
{papers}

## Ground rules

{system}

## Your task

{task}

## Your answer

Save your answer as JSON, and nothing else, to:

    {answer}

It must follow this JSON schema exactly (every field is required):

```json
{json.dumps(schema, indent=1)}
```
"""


def run(paper: Paper, opts: Options, work: Path):
    """Replay the saved answers. Returns (result, backend) when every step is answered;
    raises NeedAnswer for the first step without one, or BadAnswer for an answer that does not fit."""
    backend = AssistantBackend(work, write_paper(paper, work))
    try:
        result = audit(paper, backend, opts, log=lambda *_: None)
    except ValidationError as exc:
        step = backend.done[-1] if backend.done else "?"
        raise BadAnswer(f"{work / 'answers' / (step + '.json')} does not follow the schema in "
                        f"{work / 'tasks' / (step + '.md')}:\n{exc}\nFix it, or delete it to redo the step.") from exc
    return result, backend
