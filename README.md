# FreeReport

Free referee report for scientific research. Runs locally (on the researcher's computer), through an API, or on an institution's HPC (high-performance computing) cluster. It lets all researchers get high-quality feedback on their work without high fees, using university resources, and protects scientific integrity and data confidentiality by running locally.

This first version checks academic papers for **math errors** and **numbers that don't match the tables**, before you submit.

It runs three AI "agents" (separate AI calls, each with its own job):

| Agent | Job |
|---|---|
| 1. Academic Adversary | Re-checks equations, derivations and proofs. |
| 2. Data Auditor | Compares numbers in the text with the tables (sample sizes, coefficients, p-values, totals). |
| 3. Synthesis Critic | Re-checks every issue the first two raised and throws out false alarms. |

Every reported issue gives a **page number, a location and an exact quote**, so you can check it yourself in a minute. The tool also confirms each quote really exists in the PDF; if it can't, the report marks it with ⚠️.

## Install

You need Python 3.10 or newer.

```bash
git clone https://github.com/alemarty/freereport.git
cd freereport
pip install -e .
```

## Set your API key

FreeReport works with **Claude** (Anthropic, the default), **GPT** (OpenAI) or **Gemini** (Google). You pay the company directly with your own API key (a password-like code linked to your account and its billing).

| Company | Get a key | Set it (macOS / Linux) | Extra install |
|---|---|---|---|
| Claude (default) | https://console.anthropic.com → API keys | `export ANTHROPIC_API_KEY="sk-ant-..."` | none |
| GPT | https://platform.openai.com → API keys | `export OPENAI_API_KEY="sk-..."` | `pip install -e ".[openai]"` |
| Gemini | https://aistudio.google.com → Get API key | `export GEMINI_API_KEY="..."` | `pip install -e ".[gemini]"` |

On Windows PowerShell use `$env:ANTHROPIC_API_KEY = "..."` (same for the others).

### Choose the AI

Pick the model with `--model`; the company is recognised from the name:

| `--model` | Company | Price per million tokens (in / out) | Note |
|---|---|---|---|
| `claude-opus-5-5` (default) | Anthropic | $4 / $20 | What the prompts were tuned on. |
| `claude-sonnet-5-5` | Anthropic | $2 / $10 | About half the price. |
| `gpt-6.1-sol` | OpenAI | $2 / $10 | |
| `gpt-6-astra` | OpenAI | $10 / $50 | OpenAI's top model; expensive. |
| `gemini-4-argon` | Google | $2 / $10 | Google doubles this on 1 Dec 2026. |
| `gemini-3.8-flash` | Google | $0.75 / $3.75 | Cheapest; good for a quick look. |

All three read PDFs, LaTeX and `.zip` bundles, figures included. Prices are list prices from October 2026; each report shows the real cost. Other models from the same companies also work, but without a cost estimate.

## Run

```bash
freereport check my_paper.pdf
freereport check papers/*.pdf --out reports/
```

### What you can give it

| Input | Example | What the AI sees |
|---|---|---|
| PDF | `freereport check paper.pdf` | The rendered paper, including figures. |
| LaTeX file | `freereport check main.tex` | The exact source, so equations and table numbers are read without PDF errors. Issues cite the file and line, e.g. `main.tex:212`. Files pulled in with `\input{...}` are included. If `main.pdf` sits next to it, the PDF is sent too. |
| arXiv-style bundle | `freereport check submission.zip` (or `.tar.gz`) | The same zip you would upload to arXiv: it finds the main `.tex` (the one with `\documentclass`), follows its `\input` files, and sends the figures (PNG/JPG as they are, PDF figures turned into images on your computer). Put the compiled paper in it too, with the same name as the main file (`main.tex` → `main.pdf`), and it reads both. Up to 60 figures are sent (large ones scaled down); EPS figures are not sent. |

LaTeX comments (`% ...`) are removed before sending, so private notes in your source stay private.

For each paper you get three files in `reports/`:

- `my_paper.pdf`: the formatted report to read, print or share.
- `my_paper.md`: the same report as plain text (Markdown).
- `my_paper.json`: the same results as data, plus token usage and cost (used for testing in step 2).

### Cost before you run

Before spending anything, FreeReport measures your paper with the API's free token counter and shows a price range, then asks:

```
Estimated cost: $2.18 – 7.11  (paper 272k tokens · 3 AI calls)
Continue? [y/N]
```

Nothing is spent unless you type `y`. The range is wide because the AI's thinking time varies; the size of the paper is exact. Every report shows the real cost at the end.

**Checked before anything is paid.** A run starts only when all of these pass:
- the AI service answers the free size check, so the API key, the model name and the internet connection work;
- the paper fits what the AI can read in one go, and is under the size and price limits;
- every planned step fits in one answer;
- the reports folder can be written to, so answers are never paid for and then lost.

If any check fails, the run stops with the reason, and nothing is spent.

**If a run stops halfway** (a step fails, the internet drops for long, the computer sleeps), every step that finished is kept in `reports/.freereport-steps/`. Run the same command again and it continues from there: finished steps are not paid for twice. The folder is removed once the report is written. With `--overnight`, short internet drops while waiting are simply retried, and a step that was already in the queue when the run stopped is picked up again on the rerun, not sent (and paid for) a second time.

- `--estimate`: only show the price, do not run.
- `--yes` (or `-y`): skip the question, e.g. when checking many papers in a row.
- `--overnight`: **half price**. Each AI step waits in a queue and is answered within hours instead of minutes, so a full run can take most of a night. Start it in the evening, read the report in the morning. Claude models only for now.

Works the same for `respond` and `resubmit`.

**Limits.** Two checks run before anything is paid:

- **Size:** a paper over **400k tokens** (about 150 pages with figures) is refused, with what makes it big and what to cut:
  ```
  Paper too large: 520k tokens · 160 pages; the limit is 400k.
    Size: text about 400k, figures about 120k (45 figures).
    Try removing the appendix or sending only the sections you want checked, then run again. Nothing was spent.
  ```
- **Price:** a run whose upper estimate is above **$25** is refused.

Change them with `--max-tokens 600000` or `--max-cost 40`. When checking several papers, the limits apply to each paper.

Options:

- `--model ...`: which AI to use (see [Choose the AI](#choose-the-ai)).
- `--effort medium`: think less, cost less. Default is `high` for the steps that find and verify errors, judge referee comments and write the council's opinions; steps that only sort, summarise or match text (splitting referee reports, overviews, the resubmission status and letter checks) use `medium` on their own. `--effort` never raises them above what you choose.

### Modes

Every run starts with the **baseline**: the 3 core agents (Adversary, Data Auditor, Synthesis Critic). `--quick` names it explicitly. The extra modes below are added **on top of** the baseline and can be combined with each other (not with `--quick`).

| Flag | In one line | What it catches that the baseline may miss | Extra cost |
|---|---|---|---|
| `--deepdive` | **Read slower.** Exhaustive, at extra-high effort, in two halves of the paper so each answer fits: section by section, re-derive every step, check every table cell, appendices included. | Small errors a normal read skims past: a wrong sign in step 4 of a proof, one cell in Table 5 that does not add up. | slower, roughly 2-3x |
| `--blindspot` | **Look back at what was left out.** After the first pass, each agent re-reads its own list and asks "what did I miss?". | Gaps rather than typos: an assumption never stated, a theorem condition never checked ("Lemma 2 needs independence, but the paper never shows it holds"), a section skipped. | +2 calls |
| `--council` | **Opinions on the paper as a whole**, from six roles: Contrarian (how the main claim could fail), First Principles (does the method really answer the question), Expansionist (where the contribution is undersold), Outsider (jargon or notation unclear to a non-specialist), Methods Referee (identification and statistics: comparison groups, confounders, fixed effects, sample definitions, clustering, pre-trends, scaling local estimates up to totals; for any field that uses data), Executive (verdict and ordered revision plan). | Big-picture weaknesses no line-by-line check finds, including research-design problems. | +6 calls |
| `--journal "Name"` | **Council tuned to a target journal**: it also judges fit with the journal's scope, the kind of contribution it publishes, and its tone and style, and the Executive writes a "Fit with ..." paragraph. Works for any field. Turns on `--council`. | Mismatch with the journal: too long, too technical for its readers, a contribution it rarely publishes. | same as `--council` |

In short: **deepdive** goes deeper into what is written, **blindspot** checks what is missing, **council** steps back to judge the whole paper.

The council's points are **opinions** and appear in their own section, separate from the verified error list, so they never mix with real errors. If the model does not know a journal well, it says so instead of guessing its policies.

Examples:

```bash
freereport check my_paper.pdf --blindspot --council
freereport check my_paper.zip --deepdive --journal "Nature Communications"
freereport check my_paper.zip --journal "Journal of Development Economics"
```

## Responding to referee reports

Got reports back from a journal? Give FreeReport your paper and the reports (one file per referee: `.pdf`, `.docx`, `.txt` or `.md`):

```bash
freereport respond my_paper.pdf --reviews reviewer1.pdf reviewer2.docx
freereport respond main.tex --reviews reviewer1.pdf reviewer2.pdf   # LaTeX: also suggests exact edits
```

For every referee comment it:

1. **Checks whether the referee is right** about your paper: right, partly right, wrong, or unclear, with the evidence.
2. **Ranks it** from fatal to major, minor and trivial, and estimates the effort.
3. **Drafts a reply** you can paste into your response letter. With a `.tex` file it also proposes the concrete edit (`main.tex:212: old -> new`).

It also flags comments from different referees that make the same point, or conflicting ones.

You get, in `reports/`:

| File | What it is |
|---|---|
| `my_paper_referee_response.pdf` | Colour-coded journal response report: overview, revision plan, then every comment from most to least serious. |
| `my_paper_highlighted.pdf` | Your paper with each comment highlighted where it applies (red = fatal, orange = major, yellow = minor, grey = trivial). Click a highlight to read the comment, the assessment and the suggested reply. Only made when a PDF of the paper is available. |
| `.md` / `.json` | The same content as text and data. |

Try it with the examples: `freereport respond examples/sample_paper.pdf --reviews examples/referee_reports/*.txt`

## Before resubmitting

Revised the paper after the reports? Give FreeReport the **revised paper**, the **original** (the version the referees read) and the **referee reports**:

```bash
freereport resubmit revised.pdf --original my_paper.pdf --reviews reviewer1.pdf reviewer2.docx
freereport resubmit revised/main.tex --original original/main.tex --reviews reviewer1.pdf   # LaTeX: exact line-by-line comparison
```

Give both versions in the same form (both PDF, or both LaTeX). It:

1. **Compares the two versions on your computer** (free) and lists every change: LaTeX line by line, or PDF text sentence by sentence.
2. **Marks every referee comment** as addressed, partly addressed or not addressed, judged from what actually changed, not from what the paper claims. For example, if the letter says "we fixed Table 2" but Table 2 is identical, it is marked not addressed.
3. **Lists changes no referee asked for** (for example a changed standard error, or a new claim), which editors often ask about.
4. **Runs the normal error check on the revised paper**, because edits often add new mistakes. Skip it with `--no-check`, or make it deeper with the same modes as `check` (`--blindspot`, `--council`, `--deepdive`).
5. **Drafts the response letter**: each comment with a reply saying what changed and where. Missing parts are marked `[TO DO]`.

Add `--previous reports/my_paper_referee_response.json` (from `freereport respond`) to keep the same comment numbers and the earlier plan.

### Final review of your own response letter

Wrote your own letter? Add `--letter` and every claim in it ("we added a robustness table", "Equation 3 is corrected") is checked against the real changes: **confirmed**, **partly done**, **not found** or **contradicted**, problems first. It also lists referee comments your letter does not answer.

```bash
freereport resubmit revised.zip --original my_paper.zip --reviews r1.pdf r2.pdf --letter my_response.tex
```

### Quick double-check

`--quick` makes `resubmit` a cheap double-check: it sends only the list of changes (not the whole paper) and skips the error check, so it costs a fraction of a full run (the report shows the exact cost). Good for a last look after small fixes; run without `--quick` for the full review.

```bash
freereport resubmit revised.zip --original my_paper.zip --reviews r1.pdf r2.pdf --letter my_response.tex --quick
```

You get, in `reports/`:

| File | What it is |
|---|---|
| `revised_resubmission.pdf` | Status of every comment (red = not addressed, orange = partly, green = addressed), not-addressed first, then the unrequested changes and the draft letter. |
| `revised_response_letter.tex` | The draft response letter in LaTeX. In Overleaf: New Project → Upload Project, pick the file, and it compiles. `[TO DO]` parts show in red. |
| `revised_response_letter.md` | The same letter as plain text, to copy into Word. |
| `revised_status_highlighted.pdf` | The revised paper with each comment's spot highlighted in its status colour. |
| `revised.pdf` / `.md` / `.json` | The error check of the revised paper. |

Try it with the examples: `freereport resubmit examples/sample_paper_revised.pdf --original examples/sample_paper.pdf --reviews examples/referee_reports/*.txt`

## Try it on the sample paper

`examples/sample_paper.pdf` is a one-page fake paper with 3 planted errors (listed in `examples/make_sample_paper.py`). A good run should find all three.

```bash
freereport check examples/sample_paper.pdf
```

## How it keeps costs down

The paper is sent once and cached (stored temporarily by the API), so agents 2 and 3 pay about a tenth of the normal price to re-read it. The cache normally lasts 5 minutes. For runs that re-read the paper many times or include a long call (`--council`, `--blindspot`, `--deepdive`, `respond`, `resubmit`), Claude keeps it for 1 hour instead. Writing it costs a bit more once ($8 instead of $5 per million tokens on Opus), but it never expires mid-run and has to be paid again. On a 270k-token paper this cuts the worst-case cost of `--council` from about $17 to about $7.

The LaTeX source is sent lean: comments and switched-off blocks (`\begin{comment}`, `\iffalse ... \fi`) are left out, and each file name is written once as a header instead of on every line, which makes the source about a quarter shorter. `--overnight` halves the whole bill. Each report shows the cost.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests use a fake model, so they cost nothing.

## Roadmap

1. ✅ Checker using the Claude API.
2. Test on 5 to 10 real papers with known errors; measure errors found, false alarms and cost.
3. University supercomputer (HPC) version: run an open model with vLLM on the cluster and point FreeReport at it.

## License

MIT
