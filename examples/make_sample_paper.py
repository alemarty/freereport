"""Builds examples/sample_paper.pdf: a short fake paper with 3 planted errors.

Planted errors (the answer key for testing):
  1. Equation (3): (a+b)^2 expanded as a^2 + b^2 (missing 2ab).
  2. Text says N = 412 participants; Table 1 groups add to 400.
  3. Text says the treatment effect is 0.34; Table 2 says 0.43.

Also builds examples/sample_paper_revised.pdf, a revision after the referee
reports in examples/referee_reports/: errors 1-3 fixed, standard errors still
not clustered (Reviewer 1, point 3), p = 0.000 left as is, and two changes no
referee asked for (a new robustness sentence, and the standard error 0.08 -> 0.09).
"""

from pathlib import Path

import pymupdf

TEXT = """A Short Study of Tutoring and Exam Scores

Abstract. We study N = 412 students randomly assigned to tutoring or control.
Tutoring raises exam scores by 0.34 standard deviations (p < 0.01).

1. Model
Let y = a + b x be the score model. Squaring the combined effect gives
  (a + b)^2 = a^2 + b^2                                   (3)
which we use to compute the variance of the total effect.

2. Data
Table 1. Sample sizes
  Group        Students
  Tutoring        210
  Control         190
  Total           400

3. Results
Table 2. Effect of tutoring (standardised)
  Estimate   Std. error   p-value
  0.43       0.08         0.000

The estimated effect of 0.34 is large and significant.
"""

REVISED = (
    TEXT.replace("N = 412", "N = 400")
    .replace("by 0.34 standard", "by 0.43 standard")
    .replace("= a^2 + b^2      ", "= a^2 + 2ab + b^2")
    .replace("0.08  ", "0.09  ")
    .replace("standardised", "standardized")
    .replace("effect of 0.34 is large and significant.",
             "effect of 0.43 is large and significant.\nResults are robust to excluding the first cohort.")
)


def write(text: str, name: str) -> None:
    out = Path(__file__).with_name(name)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 70), text, fontsize=10, fontname="cour")
    doc.save(out)
    print(f"wrote {out}")


write(TEXT, "sample_paper.pdf")
write(REVISED, "sample_paper_revised.pdf")
