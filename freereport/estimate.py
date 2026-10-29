"""Cost estimate before a run.

The paper's size is counted exactly with the API's free token-counting
endpoint (no model call, no charge). What the agents will write and think
can only be a range, so each call gets a low and a high number of output
tokens, calibrated on real runs. The low end assumes the cached paper is
reused by every call; the high end assumes the cache (kept 5 minutes)
expires after each long call, so the next call pays to read the paper again.
"""

from dataclasses import dataclass

from .backends import PRICES
from .paper import Paper
from .pipeline import Options

# (low, high) output tokens per call, thinking included
FINDER = (12_000, 55_000)
FINDER_DEEP = (40_000, 64_000)
SYNTHESIS = (10_000, 40_000)
BLINDSPOT = (10_000, 40_000)
COUNCIL = (4_000, 12_000)
SPLIT = (3_000, 10_000)
ASSESS = (10_000, 35_000)
OVERVIEW = (4_000, 12_000)
STATUS = (8_000, 30_000)
LETTER = (8_000, 25_000)
TASK_TOKENS = 3_000  # instructions and earlier results added to each call
MAX_TOKENS = 400_000  # default size limit: about 150 pages with figures
MAX_COST = 25.0  # default price cap, in dollars, on the high end of the estimate
LONG_CALL = 10_000  # output tokens after which the 5-minute cache has likely expired (seen on a real run)


@dataclass
class Call:
    label: str
    out: tuple[int, int]
    reads_paper: bool = True
    extra_in: int = 0  # input sent in the task itself, not cached (e.g. the list of changes)


@dataclass
class Estimate:
    paper_tokens: int
    calls: list[Call]
    low: float | None
    high: float | None
    exact_count: bool  # False when the free count failed and the size was guessed

    def line(self) -> str:
        size = f"paper {self.paper_tokens / 1000:.0f}k tokens" + ("" if self.exact_count else " (rough guess)")
        if self.low is None:
            return f"Estimated cost: unknown for this model ({size} · {len(self.calls)} AI calls)"
        return f"Estimated cost: ${self.low:.2f} – {self.high:.2f}  ({size} · {len(self.calls)} AI calls)"


def plan_check(opts: Options) -> list[Call]:
    finder = FINDER_DEEP if opts.deepdive else FINDER
    calls = [Call("adversary", finder), Call("data_auditor", finder)]
    if opts.blindspot:
        calls += [Call("adversary_blindspot", BLINDSPOT), Call("data_auditor_blindspot", BLINDSPOT)]
    calls.append(Call("synthesis", SYNTHESIS))
    if opts.council:
        calls += [Call(f"council_{n}", COUNCIL) for n in range(5)]
    return calls


def plan_respond(n_reports: int) -> list[Call]:
    return [Call("referee_split", SPLIT), *[Call(f"referee_assess_R{n}", ASSESS) for n in range(1, n_reports + 1)],
            Call("referee_overview", OVERVIEW)]


def plan_resubmit(opts: Options, n_reports: int, letter: bool, quick: bool, no_check: bool,
                  reuse_comments: bool, changes_tokens: int) -> list[Call]:
    """quick: the paper is not sent at all, only the list of changes."""
    calls = [] if (quick or no_check) else plan_check(opts)
    full = not quick
    if not reuse_comments:
        calls.append(Call("resubmit_split", SPLIT, full))
    calls += [Call(f"resubmit_status_R{n}", STATUS, full, changes_tokens) for n in range(1, max(n_reports, 1) + 1)]
    calls.append(Call("resubmit_overview", OVERVIEW, full, changes_tokens))
    if letter:
        calls.append(Call("resubmit_letter", LETTER, full, changes_tokens))
    return calls


def rough_tokens(paper: Paper) -> int:
    """Offline guess of the paper's size, used only if the free count fails."""
    tex = len(paper.tex_numbered) / 3.3
    pdf = 1_500 * len(paper.pages) if paper.pdf_path else 0
    return int(tex + pdf + 1_600 * len(paper.figures))


def size_parts(paper: Paper, paper_tokens: int) -> tuple[int, int]:
    """Split the exact size into (text, figures), using the offline guess for the proportions."""
    guess = rough_tokens(paper) or 1
    figures = int(paper_tokens * 1_600 * len(paper.figures) / guess)
    return paper_tokens - figures, figures


def limit_problem(paper: Paper | None, est: Estimate, max_tokens: int, max_cost: float) -> str:
    """Why the run is refused before any paid call, with what to shrink; empty when it is within limits."""
    if paper is not None and est.paper_tokens > max_tokens:
        text, figures = size_parts(paper, est.paper_tokens)
        pages = f" · {len(paper.pages)} pages" if paper.pages else ""
        parts = [f"text about {text / 1000:.0f}k"]
        if paper.figures:
            parts.append(f"figures about {figures / 1000:.0f}k ({len(paper.figures)} figures)")
        tip = ("Try removing some figures or the appendix" if figures > text
               else "Try removing the appendix or sending only the sections you want checked")
        return (f"Paper too large: {est.paper_tokens / 1000:.0f}k tokens{pages}; the limit is {max_tokens / 1000:.0f}k.\n"
                f"  Size: {', '.join(parts)}.\n"
                f"  {tip}, then run again. Nothing was spent.")
    if est.high is not None and est.high > max_cost:
        return (f"{est.line()}\n"
                f"  The upper estimate is above the ${max_cost:.0f} cap. Drop an extra mode or shrink the paper"
                f" (or raise the cap with --max-cost). Nothing was spent.")
    return ""


def estimate(paper_tokens: int, calls: list[Call], model: str, exact_count: bool = True) -> Estimate:
    p = PRICES.get(model)
    if p is None:
        return Estimate(paper_tokens, calls, None, None, exact_count)
    per_m = 1 / 1_000_000
    readers = [c for c in calls if c.reads_paper]
    task_in = sum(TASK_TOKENS + c.extra_in for c in calls) * p["input"] * per_m
    out_low = sum(c.out[0] for c in calls) * p["output"] * per_m
    out_high = sum(c.out[1] for c in calls) * p["output"] * per_m
    if readers:
        # low: one cache write, then cheap cache reads.
        # high: the cache is written again after every call that may run past its 5 minutes.
        paper_low = paper_tokens * (p["cache_write"] + p["cache_read"] * (len(readers) - 1)) * per_m
        writes = sum(1 for i, c in enumerate(readers) if i == 0 or readers[i - 1].out[1] >= LONG_CALL)
        paper_high = paper_tokens * (p["cache_write"] * writes + p["cache_read"] * (len(readers) - writes)) * per_m
    else:
        paper_low = paper_high = 0.0
    return Estimate(paper_tokens, calls, paper_low + task_in + out_low, paper_high + task_in + out_high, exact_count)


def confirm(est: Estimate, assume_yes: bool, ask=None) -> bool:
    """Print the estimate and ask before spending anything."""
    ask = ask or input
    print(est.line())
    if assume_yes:
        return True
    try:
        return ask("Continue? [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:  # no keyboard (e.g. a script): do not spend without a yes
        return False
