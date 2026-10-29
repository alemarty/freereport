"""Loading .tex files and arXiv-style source bundles."""

import io
import tarfile
import zipfile

import pytest

from freereport.paper import load

MAIN = r"""\documentclass{article}
\begin{document}
% a private note that should not reach the model
Results: $N = 412$. % trailing comment
\input{sections/model}
\includegraphics[width=0.5\textwidth]{figs/plot}
\end{document}
"""
MODEL = r"""\section{Model}
\begin{equation} (a+b)^2 = a^2 + b^2 \end{equation}
"""
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def write_project(root):
    (root / "sections").mkdir()
    (root / "figs").mkdir()
    (root / "main.tex").write_text(MAIN)
    (root / "sections" / "model.tex").write_text(MODEL)
    (root / "figs" / "plot.png").write_bytes(PNG)


def test_tex_flattens_inputs_and_keeps_original_line_numbers(tmp_path):
    write_project(tmp_path)
    paper = load(tmp_path / "main.tex")
    numbered = paper.tex_numbered
    assert "2| \\begin{document}\n4| Results: $N = 412$." in numbered
    assert "=== file: sections/model.tex ===\n1| \\section{Model}\n2| \\begin{equation} (a+b)^2" in numbered
    assert numbered.count("=== file: main.tex ===") == 2  # main.tex resumes after the \input
    assert "private note" not in numbered and "trailing comment" not in numbered
    assert [f[0] for f in paper.figures] == ["figs/plot.png"]
    assert paper.pdf_path is None


def test_zip_bundle_finds_main_tex(tmp_path):
    write_project(tmp_path)
    bundle = tmp_path / "submission.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for p in tmp_path.rglob("*"):
            if p.is_file() and p != bundle:
                zf.write(p, p.relative_to(tmp_path).as_posix())
    paper = load(bundle)
    assert paper.name == "submission.zip"
    assert "(a+b)^2" in paper.tex_text
    assert len(paper.figures) == 1


def test_tar_gz_bundle(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    write_project(src)
    bundle = tmp_path / "arxiv.tar.gz"
    with tarfile.open(bundle, "w:gz") as tf:
        tf.add(src, arcname=".")
    assert "N = 412" in load(bundle).tex_text


def test_zip_with_unsafe_path_is_rejected(tmp_path):
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("../escape.tex", MAIN)
    with pytest.raises(ValueError, match="unsafe path"):
        load(bundle)


def _pdf(path, text, pages=1):
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=300, height=200).insert_text((20, 50), text)
    doc.save(path)


def test_pdf_figures_become_images_and_are_not_taken_as_the_paper(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    write_project(src)
    (src / "main.tex").write_text(MAIN + "\\includegraphics{bigfigure}\n")
    _pdf(src / "bigfigure.pdf", "a plot", pages=30)  # large PDF figure in the top folder
    bundle = tmp_path / "paper.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for p in src.rglob("*"):
            zf.write(p, p.relative_to(src).as_posix())
    paper = load(bundle)
    assert paper.pdf_path is None  # the figure is not mistaken for the compiled paper
    assert any("using the LaTeX only" in n for n in paper.notes)
    names = {name: (media, data) for name, media, data in paper.figures}
    assert names["bigfigure.pdf"][0] == "image/png" and names["bigfigure.pdf"][1].startswith(b"\x89PNG")
    assert any("1 PDF figure(s) turned into images" in n for n in paper.notes)

    _pdf(src / "main.pdf", "the compiled paper")  # main.tex -> main.pdf is used
    with zipfile.ZipFile(bundle, "w") as zf:
        for p in src.rglob("*"):
            zf.write(p, p.relative_to(src).as_posix())
    paper = load(bundle)
    assert paper.pdf_path.name == "main.pdf" and "compiled paper" in paper.pages[0]


def test_large_png_is_scaled_down(tmp_path):
    import pymupdf
    from freereport.paper import MAX_FIGURE_PIXELS
    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 3000, 1500), False)
    big.clear_with(200)
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\includegraphics{wide.png}\n")
    (tmp_path / "wide.png").write_bytes(big.tobytes("png"))
    [(name, media, data)] = load(tmp_path / "main.tex").figures
    small = pymupdf.Pixmap(data)
    assert (name, media) == ("wide.png", "image/png") and max(small.width, small.height) == MAX_FIGURE_PIXELS


def test_switched_off_blocks_are_not_sent_but_line_numbers_stay(tmp_path):
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{comment}\nold draft\n\\end{comment}\n"
                                       "\\iffalse older draft \\fi\n\\iffalse\nunused\n\\fi\nKept text.\n\\iffalse never closed\nlast line\n")
    numbered = load(tmp_path / "main.tex").tex_numbered
    assert "draft" not in numbered and "unused" not in numbered
    assert "9| Kept text." in numbered
    assert "10| \\iffalse never closed\n11| last line" in numbered  # an unclosed block is kept
