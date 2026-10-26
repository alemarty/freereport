"""Builds examples/sample_paper.pdf: a short fake paper with 3 planted errors.

Planted errors (the answer key for testing):
  1. Equation (3): (a+b)^2 expanded as a^2 + b^2 (missing 2ab).
  2. Text says N = 412 participants; Table 1 groups add to 400.
  3. Text says the treatment effect is 0.34; Table 2 says 0.43.
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

out = Path(__file__).with_name("sample_paper.pdf")
doc = pymupdf.open()
page = doc.new_page()
page.insert_text((60, 70), TEXT, fontsize=10, fontname="cour")
doc.save(out)
print(f"wrote {out}")
