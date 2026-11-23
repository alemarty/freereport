"""Command line.

  freereport check paper.pdf [more.pdf ...]
  freereport respond paper.pdf --reviews report1.pdf report2.txt
  freereport resubmit revised.pdf --original paper.pdf --reviews report1.pdf report2.txt
"""

import argparse
import json
import sys
from pathlib import Path

from .backends import make_backend, provider
from . import estimate, prompts, referee, resubmit, steps
from .paper import Paper, load
from .pipeline import Options, audit
from .report import to_json, to_markdown, to_pdf


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tgz", ".tar", ".zip", ".pdf", ".tex"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _add_cost(p: argparse.ArgumentParser) -> None:
    p.add_argument("--estimate", action="store_true", help="Only show the estimated cost; do not run (free).")
    p.add_argument("--yes", "-y", action="store_true", help="Do not ask before spending; run straight after showing the estimate.")
    p.add_argument("--overnight", action="store_true",
                   help="Half price: the AI answers each step within hours instead of minutes (Claude models). "
                        "Start it in the evening, read the report in the morning.")
    p.add_argument("--max-tokens", type=int, default=estimate.MAX_TOKENS,
                   help=f"Refuse papers larger than this many tokens, before any paid call (default {estimate.MAX_TOKENS:,}).")
    p.add_argument("--max-cost", type=float, default=estimate.MAX_COST,
                   help=f"Refuse runs whose upper estimate is above this many dollars (default {estimate.MAX_COST:.0f}).")


def _paper_tokens(backend, paper: Paper, system: str) -> tuple[int, bool]:
    """Exact size from the free token counter. If that fails, a rough offline guess for the estimate;
    a paid run then does not start (see _go), since the same key, model or network would fail mid-run."""
    try:
        return backend.count_tokens(system), True
    except Exception as exc:
        print(f"  Could not reach the AI service: {exc}", file=sys.stderr)
        return estimate.rough_tokens(paper), False


def _cost(backend, tokens: int, calls: list, model: str, exact: bool, overnight: bool = False) -> "estimate.Estimate":
    """Estimate, choosing how long the paper is kept ready: an hour when it is re-read many times,
    and always overnight, where calls wait in a queue."""
    long_cache = (overnight or estimate.wants_long_cache(calls)) and provider(model) == "anthropic"
    backend.cache_ttl = "1h" if long_cache else "5m"
    if overnight:
        backend.overnight, backend.usage.discount = True, 0.5
    return estimate.estimate(tokens, calls, model, exact, long_cache, overnight)


def _preflight(est: "estimate.Estimate", args) -> str:
    """Everything a paid run needs, checked before the first paid step so it cannot fail later for these
    reasons; empty when all is ready. (Paper size, step length and the price cap: estimate.limit_problem.)"""
    if not est.exact_count:
        return ("The AI service did not answer the free size check (see the reason above), so a paid run would fail too. "
                "Check the API key, the model name and the internet connection.")
    try:  # the reports must be writable, or the answers would be paid for and then lost
        args.out.mkdir(parents=True, exist_ok=True)
        probe = args.out / ".freereport-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"Cannot write the reports to {args.out}: {exc}."
    return ""


def _cost_line(backend, model: str) -> str:
    """The real cost, counting the steps reused from earlier runs of the same check."""
    now = backend.usage.cost_usd(model) if backend.usage else None
    if now is None:
        return ""
    earlier = steps.earlier_cost(backend, model)
    if earlier:
        return (f"\n  Cost: ${now + earlier:.2f} in total = ${now:.2f} this run"
                f" + ${earlier:.2f} paid by earlier runs for the steps reused")
    return f"\n  Cost: ${now:.2f}"


def _go(est: "estimate.Estimate", args, paper: Paper | None = None, limits: bool = True) -> bool:
    """True when the run should go ahead (checks the limits, prints the estimate first)."""
    args.refused = False  # True when a check stops the run (a plain 'no' from the user is not an error)
    problem = estimate.limit_problem(paper, est, args.max_tokens, args.max_cost) if limits else ""
    if problem:
        print(problem, file=sys.stderr)
        args.refused = True
        return False
    if args.estimate:
        print(est.line())
        return False
    stop = _preflight(est, args)
    if stop:
        print(f"{est.line()}\n  {stop} Nothing was spent.", file=sys.stderr)
        args.refused = True
        return False
    if not estimate.confirm(est, args.yes):
        print("Stopped before any paid call. Nothing was spent.")
        return False
    return True


def _add_modes(p: argparse.ArgumentParser, quick_help: str = "Baseline only: the 3 core agents, no extra modes (the cheapest run; same as giving no mode).") -> None:
    """Options for the error check, shared by 'check' and 'resubmit'."""
    p.add_argument("--quick", action="store_true", help=quick_help)
    p.add_argument("--deepdive", action="store_true", help="Slower, exhaustive checking: each checker goes through the paper in two halves, at extra-high effort.")
    p.add_argument("--blindspot", action="store_true", help="Each checker re-scans its own draft for missed assumptions and gaps.")
    p.add_argument("--council", action="store_true", help="Add a 6-role opinion review of the whole paper, including a methods (identification and statistics) referee.")
    p.add_argument("--journal", default="", help='Target journal, e.g. --journal "Nature Communications": the council also judges fit, tone and style for it (turns on --council).')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freereport", description="Check an academic paper for math and data errors.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Audit one or more papers (.pdf or .tex).")
    check.add_argument("papers", nargs="+", type=Path, help="PDF, LaTeX (.tex) or arXiv-style source bundle (.zip / .tar.gz). For a .tex file, a PDF with the same name next to it is used too.")
    check.add_argument("--out", type=Path, default=Path("reports"), help="Folder for the reports (default: reports/).")
    check.add_argument("--model", default="claude-opus-5-5", help="Claude (default), GPT or Gemini model, e.g. gpt-6.1-sol or gemini-4-argon.")
    check.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    _add_modes(check)
    _add_cost(check)

    resp = sub.add_parser("respond", help="Check, rank and answer referee reports for a paper.")
    resp.add_argument("paper", type=Path, help="The paper: PDF, .tex or source bundle. With LaTeX you also get concrete edits.")
    resp.add_argument("--reviews", nargs="+", type=Path, required=True, help="Referee reports (.pdf, .docx, .txt or .md), one file per referee.")
    resp.add_argument("--out", type=Path, default=Path("reports"))
    resp.add_argument("--model", default="claude-opus-5-5", help="Claude (default), GPT or Gemini model, e.g. gpt-6.1-sol or gemini-4-argon.")
    resp.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    _add_cost(resp)

    resub = sub.add_parser("resubmit", help="Before resubmitting: check the revised paper against the original and the referee reports.")
    resub.add_argument("paper", type=Path, help="The REVISED paper: PDF, .tex or source bundle.")
    resub.add_argument("--original", type=Path, required=True, help="The version the referees read, in the same form (both PDF, or both LaTeX).")
    resub.add_argument("--reviews", nargs="+", type=Path, default=[], help="Referee reports (.pdf, .docx, .txt or .md), one file per referee.")
    resub.add_argument("--previous", type=Path, help="Optional: the _referee_response.json from 'freereport respond', to keep the same comment ids and plan.")
    resub.add_argument("--letter", type=Path, help="Your own response letter (.pdf, .docx, .tex, .txt or .md): every claim in it is checked against the real changes.")
    resub.add_argument("--no-check", action="store_true", help="Skip the error check of the revised paper.")
    _add_modes(resub, quick_help="Cheap double-check: sends only the list of changes (not the whole paper) and skips the error check.")
    resub.add_argument("--out", type=Path, default=Path("reports"))
    resub.add_argument("--model", default="claude-opus-5-5", help="Claude (default), GPT or Gemini model, e.g. gpt-6.1-sol or gemini-4-argon.")
    resub.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    _add_cost(resub)

    helper = sub.add_parser("assistant", help="Check a paper with an AI assistant (Codex on a ChatGPT plan, Claude Code on a "
                            "Claude plan) instead of an API key: run it, do the task it names, run it again.")
    helper.add_argument("paper", type=Path, help="PDF, LaTeX (.tex) or source bundle (.zip / .tar.gz).")
    helper.add_argument("--work", type=Path, help="Folder for the tasks and answers (default: freereport-work/<paper name>/).")
    helper.add_argument("--out", type=Path, default=Path("reports"), help="Folder for the report (default: reports/).")
    helper.add_argument("--deepdive", action="store_true", help="Slower, exhaustive checking: each checker goes through the paper in two halves.")
    helper.add_argument("--blindspot", action="store_true", help="Each checker re-scans its own draft for missed assumptions and gaps.")
    helper.add_argument("--council", action="store_true", help="Add the council's opinion of the paper as a whole (6 more steps).")
    helper.add_argument("--journal", default="", help='Target journal, e.g. --journal "Nature Communications": the council also judges fit (turns on --council).')

    args = parser.parse_args(argv)
    if args.command == "assistant":
        return _assistant(args)
    if args.overnight and provider(args.model) != "anthropic":
        parser.error("--overnight works with Claude models for now; leave it out for GPT or Gemini")
    if args.command == "respond":
        return _respond(args)
    if args.quick and (args.deepdive or args.blindspot or args.council or args.journal):
        parser.error("--quick is the baseline run: leave out --quick to add --deepdive, --blindspot, --council or --journal")
    if args.journal and not args.council:
        print(f"--journal given: turning on the council to judge fit with {args.journal}")
    opts = Options(deepdive=args.deepdive, blindspot=args.blindspot, council=args.council or bool(args.journal),
                   journal=args.journal)
    if args.command == "resubmit":
        if not args.reviews and not args.previous:
            parser.error("resubmit needs --reviews (the referee reports) or --previous")
        if args.no_check and (opts.deepdive or opts.blindspot or opts.council):
            parser.error("--deepdive, --blindspot, --council and --journal change the error check: leave out --no-check to use them")
        return _resubmit(args, opts)
    effort = "xhigh" if args.deepdive else args.effort  # "max" can think past the longest answer allowed

    failures, ready = 0, []
    for path in args.papers:
        print(f"\n== {path.name}")
        try:
            paper = load(path)
            for note in paper.notes:
                print(f"  {note}")
            backend = make_backend(paper, model=args.model, effort=effort)
        except Exception as exc:  # keep going through a batch of papers
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1
            continue
        tokens, exact = _paper_tokens(backend, paper, prompts.SYSTEM)
        est = _cost(backend, tokens, estimate.plan_check(opts), args.model, exact, args.overnight)
        too_big = estimate.limit_problem(paper, est, args.max_tokens, args.max_cost)  # limits apply per paper
        if too_big:
            print(f"  {too_big}", file=sys.stderr)
            failures += 1
            continue
        if len(args.papers) > 1:
            print(f"  {est.line()}")
        ready.append((path, paper, backend, est))
    if not ready:
        return 1
    if len(ready) > 1:
        print(f"\nAll {len(ready)} papers together:")
    total = estimate.Estimate(sum(e.paper_tokens for *_, e in ready), [c for *_, e in ready for c in e.calls],
                              None if ready[0][3].low is None else sum(e.low for *_, e in ready),
                              None if ready[0][3].low is None else sum(e.high for *_, e in ready),
                              all(e.exact_count for *_, e in ready))
    if not _go(total, args, limits=False):
        return 1 if failures or args.refused else 0

    args.out.mkdir(parents=True, exist_ok=True)
    for path, paper, backend, _ in ready:
        print(f"\n== {path.name}")
        saved = steps.keep(backend, paper, args.out)
        try:
            result = audit(paper, backend, opts)
        except Exception as exc:
            print(f"  FAILED: {exc}\n  {steps.stopped(saved)}", file=sys.stderr)
            failures += 1
            continue
        stem = args.out / _stem(path)
        stem.with_suffix(".md").write_text(to_markdown(result, backend.usage), encoding="utf-8")
        stem.with_suffix(".json").write_text(to_json(result, backend.usage), encoding="utf-8")
        to_pdf(result, backend.usage, stem.with_suffix(".pdf"))
        steps.done(saved)
        print(f"\nDONE. {len(result.kept())} issue(s) found.\n"
              f"  Report: {stem.with_suffix('.pdf')}  (also {stem.with_suffix('.md').name} and {stem.with_suffix('.json').name})"
              + _cost_line(backend, args.model))
    return 1 if failures else 0


def _assistant(args) -> int:
    from . import assistant

    opts = Options(deepdive=args.deepdive, blindspot=args.blindspot, council=args.council or bool(args.journal),
                   journal=args.journal)
    work = args.work or Path("freereport-work") / _stem(args.paper)
    try:
        result, backend = assistant.run(load(args.paper), opts, work)
    except assistant.NeedAnswer as need:
        done = len(list((work / "answers").glob("*.json")))
        print(f"NEXT STEP ({done} done so far): {need.label}\n"
              f"  1. Do the task in   {need.task_file}\n"
              f"  2. Save the answer  {need.answer_file}\n"
              f"  3. Run the same command again.")
        return 3
    except assistant.BadAnswer as exc:
        print(f"PROBLEM: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / _stem(args.paper)
    stem.with_suffix(".md").write_text(to_markdown(result, backend.usage), encoding="utf-8")
    stem.with_suffix(".json").write_text(to_json(result, backend.usage), encoding="utf-8")
    to_pdf(result, backend.usage, stem.with_suffix(".pdf"))
    print(f"\nDONE. {len(result.kept())} issue(s) found.\n"
          f"  Report: {stem.with_suffix('.pdf')}  (also {stem.with_suffix('.md').name} and {stem.with_suffix('.json').name})")
    return 0


def _respond(args) -> int:
    paper = load(args.paper)
    for note in paper.notes:
        print(f"  {note}")
    backend = make_backend(paper, model=args.model, effort=args.effort)
    tokens, exact = _paper_tokens(backend, paper, prompts.REFEREE_SYSTEM)
    if not _go(_cost(backend, tokens, estimate.plan_respond(len(args.reviews)), args.model, exact, args.overnight), args, paper):
        return 1 if args.refused else 0
    args.out.mkdir(parents=True, exist_ok=True)
    saved = steps.keep(backend, paper, args.out)
    try:
        result = referee.respond(paper, args.reviews, backend)
    except Exception as exc:
        print(f"  FAILED: {exc}\n  {steps.stopped(saved)}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{_stem(args.paper)}_referee_response"
    stem.with_suffix(".md").write_text(referee.to_markdown(result, backend.usage), encoding="utf-8")
    stem.with_suffix(".json").write_text(referee.to_json(result, backend.usage), encoding="utf-8")
    referee.to_pdf(result, backend.usage, stem.with_suffix(".pdf"))
    steps.done(saved)
    print(f"\nDONE.\n  Journal response report: {stem.with_suffix('.pdf')}")
    if paper.pdf_path:
        marked = args.out / f"{_stem(args.paper)}_highlighted.pdf"
        placed = referee.highlight_pdf(result, paper.pdf_path, marked)
        print(f"  Highlighted paper: {marked} ({placed} of {len(result.assessments)} comments placed on the text)")
    print(_cost_line(backend, args.model).lstrip("\n"))
    return 0


def _previous_comments(path: Path | None) -> list[dict]:
    if not path:
        return []
    return json.loads(Path(path).read_text(encoding="utf-8")).get("comments", [])


def _resubmit(args, opts: Options) -> int:
    original, revised = load(args.original), load(args.paper)
    for note in revised.notes:
        print(f"  revised: {note}")
    for note in original.notes:
        print(f"  original: {note}")
    # --quick: send only the change list, not the paper, so the run costs a fraction of a full one
    backend = make_backend(Paper(name=revised.name) if args.quick else revised, model=args.model, effort=args.effort)
    stem = _stem(args.paper)

    changes, _ = resubmit.compare(original, revised)  # free, on this computer
    changes_tokens = len(resubmit.changes_text(changes)) // 3
    if args.quick:
        tokens, exact = 0, True
    else:
        tokens, exact = _paper_tokens(backend, revised, prompts.RESUBMIT_SYSTEM)
    n_reports = len(args.reviews) or len({c["id"].split(".")[0] for c in _previous_comments(args.previous)})
    calls = estimate.plan_resubmit(opts, n_reports, bool(args.letter), args.quick, args.no_check,
                                   reuse_comments=bool(args.previous), changes_tokens=changes_tokens)
    if not _go(_cost(backend, tokens, calls, args.model, exact, args.overnight), args, revised):
        return 1 if args.refused else 0
    args.out.mkdir(parents=True, exist_ok=True)
    saved = steps.keep(backend, revised, args.out)
    try:
        return _resubmit_run(args, opts, original, revised, backend, stem, saved)
    except Exception as exc:
        print(f"  FAILED: {exc}\n  {steps.stopped(saved)}", file=sys.stderr)
        return 1


def _resubmit_run(args, opts: Options, original: Paper, revised: Paper, backend, stem: str, saved) -> int:

    check_summary = ""
    if not args.no_check and not args.quick:
        print("Error check of the revised paper (edits often add new mistakes)...")
        if opts.deepdive:  # extra-high effort for the error check only, not the comment-by-comment check
            backend.effort = "xhigh"
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
    steps.done(saved)
    print(f"\nDONE.\n  Resubmission report: {out.with_suffix('.pdf')}")
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
    print(_cost_line(backend, args.model).lstrip("\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
