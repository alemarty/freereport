"""Loading a paper from a PDF, a LaTeX source, both, or an arXiv-style
source bundle (.zip / .tar.gz with the .tex files and figures).

LaTeX gives the models exact equations and table values (no PDF extraction
noise) and lets findings cite source line numbers. The PDF shows the
rendered result. Given a .tex file, a PDF with the same name next to it is
used too.
"""

import atexit
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .pdf import page_texts

_INPUT = re.compile(r"\\(?:input|include|subfile)\{([^}]+)\}")
_COMMENT = re.compile(r"(?<!\\)%.*")
_GRAPHIC = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_FIGURES = 20
MAX_FIGURE_BYTES = 5 * 1024 * 1024


@dataclass
class Paper:
    name: str
    pdf_path: Path | None = None
    # (file name, original line number, text with comments removed), with
    # \input / \include files inlined so line numbers match the author's files
    tex_lines: list[tuple[str, int, str]] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)  # extracted PDF text per page
    figures: list[tuple[str, str, bytes]] = field(default_factory=list)  # (file name, media type, bytes)
    notes: list[str] = field(default_factory=list)  # what was loaded or skipped, for the log

    @property
    def tex_numbered(self) -> str:
        """The LaTeX source as the models see it: 'file:line| text', blank lines left out."""
        return "\n".join(f"{f}:{n}| {text}" for f, n, text in self.tex_lines if text.strip())

    @property
    def tex_text(self) -> str:
        return "\n".join(text for _, _, text in self.tex_lines)


def _flatten(path: Path, root: Path, seen: set[Path]) -> list[tuple[str, int, str]]:
    """Read a .tex file, inlining \\input / \\include files and dropping comments."""
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    name = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    lines = []
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = _COMMENT.sub("", raw).rstrip()
        lines.append((name, n, line))
        match = _INPUT.search(line)
        if match:
            child = root / match.group(1).strip()
            if child.suffix != ".tex":
                child = child.with_name(child.name + ".tex")
            if child.exists():
                lines += _flatten(child, root, seen)
    return lines


def _figures(paper: Paper, root: Path) -> None:
    """Attach the raster figures used by \\includegraphics (PNG/JPG/GIF/WebP)."""
    skipped = []
    for ref in dict.fromkeys(m.group(1).strip() for _, _, t in paper.tex_lines for m in _GRAPHIC.finditer(t)):
        base = root / ref
        found = next((p for p in [base, *(base.with_name(base.name + ext) for ext in _IMAGE_TYPES)]
                      if p.suffix.lower() in _IMAGE_TYPES and p.is_file()), None)
        if found is None or found.stat().st_size > MAX_FIGURE_BYTES or len(paper.figures) >= MAX_FIGURES:
            skipped.append(ref)
            continue
        paper.figures.append((found.relative_to(root).as_posix(), _IMAGE_TYPES[found.suffix.lower()], found.read_bytes()))
    if paper.figures:
        paper.notes.append(f"{len(paper.figures)} figure(s) attached")
    if skipped:
        paper.notes.append(f"figures not sent (vector/EPS/PDF, too large, or over {MAX_FIGURES}): {', '.join(skipped)}")


def _load_tex(tex: Path, name: str, pdf: Path | None) -> Paper:
    root = tex.parent.resolve()
    paper = Paper(name=name, tex_lines=_flatten(tex, root, set()))
    paper.notes.append(f"LaTeX source: {tex.name}, {len(paper.tex_lines)} lines")
    if pdf and pdf.exists():
        paper.pdf_path = pdf
        paper.pages = page_texts(pdf)
        paper.notes.append(f"PDF: {pdf.name}")
    _figures(paper, root)
    return paper


def _extract(bundle: Path) -> Path:
    """Unpack a source bundle into a temporary folder, refusing paths that escape it."""
    dest = Path(tempfile.mkdtemp(prefix="freereport-"))
    atexit.register(shutil.rmtree, dest, ignore_errors=True)
    if zipfile.is_zipfile(bundle):
        with zipfile.ZipFile(bundle) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            for m in members:
                target = (dest / m).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise ValueError(f"{bundle.name}: unsafe path inside archive: {m}")
            zf.extractall(dest, members)
    elif tarfile.is_tarfile(bundle):
        with tarfile.open(bundle) as tf:
            tf.extractall(dest, filter="data")  # "data" filter blocks unsafe paths and links
    else:
        raise ValueError(f"{bundle.name}: not a .zip or .tar archive")
    return dest


def _main_tex(folder: Path) -> Path:
    """The file with \\documentclass; if several, prefer one named main/ms/paper, then the largest."""
    candidates = [p for p in folder.rglob("*.tex") if "\\documentclass" in p.read_text(encoding="utf-8", errors="replace")]
    if not candidates:
        raise ValueError("no .tex file with \\documentclass found in the archive")
    preferred = [p for p in candidates if p.stem.lower() in {"main", "ms", "paper", "manuscript"}]
    return max(preferred or candidates, key=lambda p: p.stat().st_size)


def load(path: Path, pdf: Path | None = None) -> Paper:
    path = Path(path)
    suffix = "".join(path.suffixes[-2:]).lower() if path.name.lower().endswith(".tar.gz") else path.suffix.lower()
    if suffix == ".pdf":
        return Paper(name=path.name, pdf_path=path, pages=page_texts(path), notes=[f"PDF: {path.name}"])
    if suffix == ".tex":
        return _load_tex(path, path.name, pdf or path.with_suffix(".pdf"))
    if suffix in {".zip", ".tar.gz", ".tgz", ".tar"}:
        folder = _extract(path)
        tex = _main_tex(folder)
        if pdf is None:
            pdfs = [tex.with_suffix(".pdf"), *folder.glob("*.pdf")]
            pdf = next((p for p in pdfs if p.exists() and p.stat().st_size > 50_000), None)  # skip small figure PDFs
        return _load_tex(tex, path.name, pdf)
    raise ValueError(f"{path.name}: expected a .pdf, .tex, .zip or .tar.gz file")
