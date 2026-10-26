"""Command line.

  freereport check paper.pdf [more.pdf ...]
  freereport respond paper.pdf --reviews report1.pdf report2.txt
  freereport resubmit revised.pdf --original paper.pdf --reviews report1.pdf report2.txt
"""

import argparse
import sys
from pathlib import Path

from .backends import ClaudeBackend
from . import referee, resubmit
from .paper import Paper, load
from .pipeline import Options, audit
from .report import to_json, to_markdown, to_pdf


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tgz", ".tar", ".zip", ".pdf", ".tex"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _add_modes(p: argparse.ArgumentParser, quick_help: str = "Baseline only: the 3 core agents, no extra modes (the cheapest run; same as giving no mode).") -> None:
    """Options for the error check, shared by 'check' and 'resubmit'."""
    p.add_argument("--quick", action="store_true", help=quick_help)
    p.add_argument("--deepdive", action="store_true", help="Slower, exhaustive checking (uses maximum effort).")
    p.add_argument("--blindspot", action="store_true", help="Each checker re-scans its own draft for missed assumptions and gaps.")
    p.add_argument("--council", action="store_true", help="Add a 5-role opinion review of the whole paper.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freereport", description="Check an academic paper for math and data errors.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Audit one or more papers (.pdf or .tex).")
    check.add_argument("papers", nargs="+", type=Path, help="PDF, LaTeX (.tex) or arXiv-style source bundle (.zip / .tar.gz). For a .tex file, a PDF with the same name next to it is used too.")
    check.add_argument("--out", type=Path, default=Path("reports"), help="Folder for the reports (default: reports/).")
    check.add_argument("--model", default="claude-opus-5-5")
    check.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    _add_modes(check)

    resp = sub.add_parser("respond", help="Check, rank and answer referee reports for a paper.")
    resp.add_argument("paper", type=Path, help="The paper: PDF, .tex or source bundle. With LaTeX you also get concrete edits.")
    resp.add_argument("--reviews", nargs="+", type=Path, required=True, help="Referee reports (.pdf, .docx, .txt or .md), one file per referee.")
    resp.add_argument("--out", type=Path, default=Path("reports"))
    resp.add_argument("--model", default="claude-opus-5-5")
    resp.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    resub = sub.add_parser("resubmit", help="Before resubmitting: check the revised paper against the original and the referee reports.")
    resub.add_argument("paper", type=Path, help="The REVISED paper: PDF, .tex or source bundle.")
    resub.add_argument("--original", type=Path, required=True, help="The version the referees read, in the same form (both PDF, or both LaTeX).")
    resub.add_argument("--reviews", nargs="+", type=Path, default=[], help="Referee reports (.pdf, .docx, .txt or .md), one file per referee.")
    resub.add_argument("--previous", type=Path, help="Optional: the _referee_response.json from 'freereport respond', to keep the same comment ids and plan.")
    resub.add_argument("--letter", type=Path, help="Your own response letter (.pdf, .docx, .tex, .txt or .md): every claim in it is checked against the real changes.")
    resub.add_argument("--no-check", action="store_true", help="Skip the error check of the revised paper.")
    _add_modes(resub, quick_help="Cheap double-check: sends only the list of changes (not the whole paper) and skips the error check.")
    resub.add_argument("--out", type=Path, default=Path("reports"))
    resub.add_argument("--model", default="claude-opus-5-5")
    resub.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    args = parser.parse_args(argv)
    if args.command == "respond":
        return _respond(args)
    if args.quick and (args.deepdive or args.blindspot or args.council):
        parser.error("--quick is the baseline run: leave out --quick to add --deepdive, --blindspot or --council")
    opts = Options(deepdive=args.deepdive, blindspot=args.blindspot, council=args.council)
    if args.command == "resubmit":
        if not args.reviews and not args.previous:
            parser.error("resubmit needs --reviews (the referee reports) or --previous")
        if args.no_check and (args.deepdive or args.blindspot or args.council):
            parser.error("--deepdive, --blindspot and --council change the error check: leave out --no-check to use them")
        return _resubmit(args, opts)
    effort = "max" if args.deepdive else args.effort

    args.out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in args.papers:
        print(f"\n== {path.name}")
        try:
            paper = load(path)
            for note in paper.notes:
                print(f"  {note}")
            backend = ClaudeBackend(paper, model=args.model, effort=effort)
            result = audit(paper, backend, opts)
        except Exception as exc:  # keep going through a batch of papers
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1
            continue
        stem = args.out / _stem(path)
        stem.with_suffix(".md").write_text(to_markdown(result, backend.usage), encoding="utf-8")
        stem.with_suffix(".json").write_text(to_json(result, backend.usage), encoding="utf-8")
        to_pdf(result, backend.usage, stem.with_suffix(".pdf"))
        cost = backend.usage.cost_usd(args.model)
        print(f"  {len(result.kept())} issue(s) · report: {stem.with_suffix('.pdf')}"
              + (f" · cost ≈ ${cost:.2f}" if cost is not None else ""))
    return 1 if failures else 0


def _respond(args) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    paper = load(args.paper)
    for note in paper.notes:
        print(f"  {note}")
    backend = ClaudeBackend(paper, model=args.model, effort=args.effort)
    result = referee.respond(paper, args.reviews, backend)

    stem = args.out / f"{_stem(args.paper)}_referee_response"
    stem.with_suffix(".md").write_text(referee.to_markdown(result, backend.usage), encoding="utf-8")
    stem.with_suffix(".json").write_text(referee.to_json(result, backend.usage), encoding="utf-8")
    referee.to_pdf(result, backend.usage, stem.with_suffix(".pdf"))
    print(f"  Journal response report: {stem.with_suffix('.pdf')}")
    if paper.pdf_path:
        marked = args.out / f"{_stem(args.paper)}_highlighted.pdf"
        placed = referee.highlight_pdf(result, paper.pdf_path, marked)
        print(f"  Highlighted paper: {marked} ({placed} of {len(result.assessments)} comments placed on the text)")
    cost = backend.usage.cost_usd(args.model)
    if cost is not None:
        print(f"  cost ≈ ${cost:.2f}")
    return 0


def _resubmit(args, opts: Options) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    original, revised = load(args.original), load(args.paper)
    for note in revised.notes:
        print(f"  revised: {note}")
    for note in original.notes:
        print(f"  original: {note}")
    # --quick: send only the change list, not the paper, so the run costs a fraction of a full one
    backend = ClaudeBackend(Paper(name=revised.name) if args.quick else revised, model=args.model, effort=args.effort)
    stem = _stem(args.paper)

    check_summary = ""
    if not args.no_check and not args.quick:
        print("Error check of the revised paper (edits often add new mistakes)...")
        if opts.deepdive:  # maximum effort for the error check only, not the comment-by-comment check
            backend.effort = "max"
        audit_result = audit(revised, backend, opts)
        backend.effort = args.effort
        report = args.out / stem
        report.with_suffix(".md").write_text(to_markdown(audit_result, backend.usage), encoding="utf-8")
        report.with_suffix(".json").write_text(to_json(audit_result, backend.usage), encoding="utf-8")
        to_pdf(audit_result, backend.usage, report.with_suffix(".pdf"))
        check_summary = f"{len(audit_result.kept())} issue(s) found, see {report.with_suffix('.pdf').name}."
        print(f"  Error report: {report.with_suffix('.pdf')}")

    result = resubmit.check_revision(original, revised, args.reviews, backend, previous=args.previous,
                                     letter=args.letter, light=args.quick)
    result.check_summary = check_summary
    out = args.out / f"{stem}_resubmission"
    out.with_suffix(".md").write_text(resubmit.to_markdown(result, backend.usage), encoding="utf-8")
    out.with_suffix(".json").write_text(resubmit.to_json(result, backend.usage), encoding="utf-8")
    resubmit.to_pdf(result, backend.usage, out.with_suffix(".pdf"))
    letter = args.out / f"{stem}_response_letter.md"
    letter.write_text(resubmit.letter(result), encoding="utf-8")
    letter.with_suffix(".tex").write_text(resubmit.latex_letter(result), encoding="utf-8")
    print(f"  Resubmission report: {out.with_suffix('.pdf')}")
    print(f"  Draft response letter: {letter} and {letter.with_suffix('.tex').name} (LaTeX, e.g. for Overleaf)")
    if revised.pdf_path:
        marked = args.out / f"{stem}_status_highlighted.pdf"
        placed = resubmit.highlight_pdf(result, revised.pdf_path, marked)
        print(f"  Highlighted revised paper: {marked} ({placed} of {len(result.statuses)} comments placed on the text)")
    if result.letter_check:
        bad = sum(cl.verdict != "confirmed" for cl in result.letter_check.claims)
        print(f"  Your letter: {len(result.letter_check.claims)} claim(s) checked, {bad} not confirmed, "
              f"{len(result.letter_check.unanswered)} comment(s) not answered")
    counts = result.counts()
    print("  " + ", ".join(f"{n} {resubmit.STATUS_LABEL[s].lower()}" for s, n in counts.items()))
    cost = backend.usage.cost_usd(args.model)
    if cost is not None:
        print(f"  cost ≈ ${cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
