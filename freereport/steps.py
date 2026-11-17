"""Saving each finished AI step, so a run that stops halfway can be started again
without paying twice for the steps that already succeeded."""

import hashlib
import json
import shutil
from pathlib import Path

from .backends import Usage, effort_for
from .paper import Paper

FOLDER = ".freereport-steps"


def _fingerprint(paper: Paper, model: str) -> str:
    h = hashlib.sha256(model.encode())
    h.update(paper.tex_numbered.encode())
    if paper.pdf_path:
        h.update(Path(paper.pdf_path).read_bytes())
    for name, _, data in paper.figures:
        h.update(name.encode() + data)
    return h.hexdigest()[:16]


def keep(backend, paper: Paper, out: Path) -> Path:
    """Save every answer under out/.freereport-steps/<paper>/. The same question about the same paper,
    model and effort is then answered from disk for free. Returns the folder, to remove with done()."""
    folder = Path(out) / FOLDER / _fingerprint(paper, getattr(backend, "model", ""))
    ask = getattr(backend, "ask", None)
    if ask is None:  # nothing to save
        return folder
    backend.earlier = Usage()  # what the reused steps cost when an earlier run paid for them

    def saved_ask(label: str, system: str, task: str, schema: dict) -> dict:
        question = json.dumps([label, effort_for(label, getattr(backend, "effort", "")), system, task, schema], sort_keys=True)
        path = folder / f"{label}-{hashlib.sha256(question.encode()).hexdigest()[:16]}.json"
        if hasattr(backend, "batch_ticket"):  # overnight: remember the queued request, so a rerun waits for it
            backend.batch_ticket = path.with_suffix(".queued")
        if path.is_file():
            print(f"  {label}: using the answer saved by an earlier run (already paid)")
            saved = json.loads(path.read_text(encoding="utf-8"))
            if set(saved) != {"answer", "usage"}:  # saved by a version that kept the answer only
                saved = {"answer": saved, "usage": []}
            for row in saved.get("usage", []):
                backend.earlier.add_counts(row["agent"], row["input"], row["output"], row["cache_write"],
                                           row["cache_read"], row.get("cache_write_1h", 0))
            return saved["answer"]
        usage = getattr(backend, "usage", None)
        before = len(usage.calls) if usage else 0
        answer = ask(label, system, task, schema)
        folder.mkdir(parents=True, exist_ok=True)
        rows = usage.calls[before:] if usage else []
        path.write_text(json.dumps({"answer": answer, "usage": rows}), encoding="utf-8")
        return answer

    backend.ask = saved_ask
    return folder


def earlier_cost(backend, model: str) -> float:
    """What the steps reused from earlier runs cost then (0 when none were reused)."""
    earlier = getattr(backend, "earlier", None)
    if not earlier or not earlier.calls:
        return 0.0
    earlier.discount = backend.usage.discount
    return earlier.cost_usd(model) or 0.0


def saved(folder: Path) -> int:
    return len(list(folder.glob("*.json"))) if folder.is_dir() else 0


def stopped(folder: Path) -> str:
    """What to tell the user after a step failed."""
    n = saved(folder)
    if not n:
        return "Nothing was saved: no step had finished."
    return (f"{n} finished step(s) are saved. Run the same command again to continue from here: "
            "those steps are not paid for twice.")


def done(folder: Path) -> None:
    """The report is written: the saved steps are no longer needed."""
    shutil.rmtree(folder, ignore_errors=True)
    if folder.parent.is_dir() and not any(folder.parent.iterdir()):
        folder.parent.rmdir()
