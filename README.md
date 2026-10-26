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

## Set your Claude API key

Get a key at https://console.anthropic.com (Settings → API keys), then:

```bash
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

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

Options:

- `--model claude-sonnet-5-5`: cheaper model (about half the price).
- `--effort medium`: think less, cost less. Default is `high`.

### Modes

`--quick` is the baseline: the 3 core agents only, the cheapest run (the same as giving no mode, but explicit). The three extra modes below can be combined with each other, not with `--quick`.

| Flag | What it adds | Extra cost |
|---|---|---|
| `--quick` | Baseline only: Adversary, Data Auditor and Synthesis Critic. | none (3 calls) |
| `--blindspot` | Each checker re-reads its own draft and looks for what it missed: unstated assumptions, theorem conditions never verified, sections it skipped. | +2 calls |
| `--deepdive` | Exhaustive mode: section by section, re-derive every step, check every table cell. Uses maximum effort. | slower, roughly 2-3x |
| `--council` | A separate "Council review" section: five roles give opinions on the paper as a whole. Contrarian (how the main claim could fail), First Principles (does the method really answer the question), Expansionist (where the contribution is undersold), Outsider (unclear jargon or notation), Executive (verdict and ordered revision plan). | +5 calls |

The council's points are **opinions** and appear in their own section, separate from the verified error list, so they never mix with real errors.

Example: `freereport check my_paper.pdf --blindspot --council`

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
4. **Runs the normal error check on the revised paper**, because edits often add new mistakes. Skip it with `--no-check`.
5. **Drafts the response letter**: each comment with a reply saying what changed and where. Missing parts are marked `[TO DO]`.

Add `--previous reports/my_paper_referee_response.json` (from `freereport respond`) to keep the same comment numbers and the earlier plan.

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

The paper is sent once and cached (stored temporarily by the API), so agents 2 and 3 pay about a tenth of the normal price to re-read it. Each report shows the cost.

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
