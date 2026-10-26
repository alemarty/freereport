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
    assert "main.tex:4| Results: $N = 412$." in numbered
    assert "sections/model.tex:2| \\begin{equation} (a+b)^2" in numbered
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
