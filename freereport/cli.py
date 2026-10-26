"""Command line: freereport check paper.pdf [more.pdf ...]"""

import argparse
import sys
from pathlib import Path

from .backends import ClaudeBackend
from .pipeline import Options, audit
from .report import to_json, to_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freereport", description="Check an academic paper for math and data errors.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Audit one or more PDF papers.")
    check.add_argument("pdfs", nargs="+", type=Path)
    check.add_argument("--out", type=Path, default=Path("reports"), help="Folder for the reports (default: reports/).")
    check.add_argument("--model", default="claude-opus-5-5")
    check.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    check.add_argument("--deepdive", action="store_true", help="Slower, exhaustive checking (uses maximum effort).")
    check.add_argument("--blindspot", action="store_true", help="Each checker re-scans its own draft for missed assumptions and gaps.")
    check.add_argument("--council", action="store_true", help="Add a 5-role opinion review of the whole paper.")
    args = parser.parse_args(argv)
    opts = Options(deepdive=args.deepdive, blindspot=args.blindspot, council=args.council)
    effort = "max" if args.deepdive else args.effort

    args.out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for pdf in args.pdfs:
        print(f"\n== {pdf.name}")
        try:
            backend = ClaudeBackend(pdf, model=args.model, effort=effort)
            result = audit(pdf, backend, opts)
        except Exception as exc:  # keep going through a batch of papers
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures += 1
            continue
        stem = args.out / pdf.stem
        stem.with_suffix(".md").write_text(to_markdown(result, backend.usage), encoding="utf-8")
        stem.with_suffix(".json").write_text(to_json(result, backend.usage), encoding="utf-8")
        cost = backend.usage.cost_usd(args.model)
        print(f"  {len(result.kept())} issue(s) · report: {stem.with_suffix('.md')}"
              + (f" · cost ≈ ${cost:.2f}" if cost is not None else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
