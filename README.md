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

For each paper you get two files in `reports/`:

- `my_paper.md`: the readable report.
- `my_paper.json`: the same results as data, plus token usage and cost (used for testing in step 2).

Options:

- `--model claude-sonnet-5-5`: cheaper model (about half the price).
- `--effort medium`: think less, cost less. Default is `high`.

### Extra modes (can be combined)

| Flag | What it adds | Extra cost |
|---|---|---|
| `--blindspot` | Each checker re-reads its own draft and looks for what it missed: unstated assumptions, theorem conditions never verified, sections it skipped. | +2 calls |
| `--deepdive` | Exhaustive mode: section by section, re-derive every step, check every table cell. Uses maximum effort. | slower, roughly 2-3x |
| `--council` | A separate "Council review" section: five roles give opinions on the paper as a whole. Contrarian (how the main claim could fail), First Principles (does the method really answer the question), Expansionist (where the contribution is undersold), Outsider (unclear jargon or notation), Executive (verdict and ordered revision plan). | +5 calls |

The council's points are **opinions** and appear in their own section, separate from the verified error list, so they never mix with real errors.

Example: `freereport check my_paper.pdf --blindspot --council`

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
