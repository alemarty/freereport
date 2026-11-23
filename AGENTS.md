# Checking a paper with FreeReport (for AI assistants)

These instructions are for an AI coding assistant (Codex, Claude Code or similar) that a person has
opened in this repository on their own plan, to check an academic paper without an API key.
You do the reviewers' work yourself; the `freereport assistant` command hands out one task at a
time and builds the report at the end.

## When the person asks you to check a paper

1. **Install once**, if `freereport --help` fails: `pip install -e .`
2. **Start**, with the paper path and the options the person asked for:
   ```bash
   freereport assistant PATH/TO/paper.zip --deepdive --blindspot --council --journal "Journal Name"
   ```
   Options: `--deepdive` (exhaustive, in two halves), `--blindspot` (re-scan each draft for what it
   missed), `--council` (five reviewers and a chair give one joint opinion on the whole paper),
   `--journal "Name"` (the council also judges fit with that journal; turns on `--council`). With no
   options it runs the 3 core checks. Keep the same options for every run.
3. **Loop until DONE.** Each run prints `NEXT STEP` with a task file and an answer file:
   - read the task file, then read the manuscript files it lists, in full (the LaTeX source has
     'line|' numbers; look at the figure images too);
   - do the task yourself, carefully, as the role it describes;
   - write your answer as JSON, and nothing else, to the answer file, following the schema in the task;
   - run exactly the same command again.

   If it prints `PROBLEM`, fix the answer file it names (or delete it and redo that step), then run again.
4. When it prints `DONE`, tell the person where the report is (`reports/<paper>.pdf`, also `.md` and
   `.json`) and summarise the verdict in a few sentences.

## How to do the tasks well

- Treat every step as a fresh, independent reviewer. Do not soften a later step because you wrote an
  earlier one; the final checker (`synthesis`) must re-check every candidate against the paper and
  reject what does not hold.
- Council members each give their own role's view; the chair (`council_executive`) then merges them into
  one report, settles disagreements and cites the verified findings by id instead of repeating them.
- Quote the paper verbatim (without the 'line|' prefix) and cite page and `file:line`. The report flags
  quotes it cannot find in the paper.
- Show the working (the calculation or logical steps) so a human can verify each finding in a minute.
- A run has 3 to 13 steps and can be long. Keep going until `DONE` unless the person stops you; if your
  plan's limit is reached, the saved answers are kept and the same command continues where it stopped.
- **The report is the one `freereport assistant` writes.** Do not write a report of your own; everything
  the person needs goes into the answers, which the command turns into the report.
- Fill `how_checked` on every finding with one short line ("recomputed from Table 3", "re-derived Eq. (7)",
  "read from the bars of Figure 5, approximate").
- If you run code or do longer calculations to check something, do not put them in the answers. Append
  them to `verification.md` in the work folder (the folder that holds `tasks/` and `answers/`), under a
  heading naming the step and finding id, with the code and its output. It is saved beside the report
  as `<paper>_verification.md`, so anyone can re-run a check that is disputed.
- Keep to each step's role. The checker steps report errors that can be shown from the paper (math,
  numbers, internal consistency). Doubts about the research design, identification or interpretation
  belong to the council steps; if the council is not part of the run, leave them out.
- Fill `checked_correct` in the synthesis step with the important results you re-checked and that hold.
- Do not change the code in this repository to get a run through.
