#!/usr/bin/env python3
"""Copy a LaTeX manuscript into a flat Editorial Manager submission directory.

Examples:
    python3 flatten_submission.py demo
    python3 flatten_submission.py demo --precompile
    python3 flatten_submission.py path/to/paper --main main.tex --figure-dir figures

The generated PROJECT/submission (or --output) is replaced on each successful
run. TikZ (.tikz) and Inkscape (.pdf_tex) sources are exported as .tex files,
with matching references. Sources stay unchanged. Uses only the Python standard
library. Exported filenames are limited to 64 characters, including extensions.
Optional figure precompilation requires LuaLaTeX and pdfseparate
(Poppler), and removes unused files from the exported submission. This handles
literal, braced file references; it is not a general TeX macro interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


OUTPUT_NAME = "submission"
DEFAULT_MAIN = "paper.tex"
DEFAULT_FIGURE_DIRS = ("figure", "figures")
MAX_FILENAME_LENGTH = 64
TEX_RENAMED_EXTENSIONS = {".tikz", ".pdf_tex"}
LATEX_EXTENSIONS = {".tex", ".tikz", ".pdf_tex", ".sty", ".cls", ".clo", ".def", ".cfg", ".bbl"}
GRAPHICS_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".tif", ".tiff", ".svg")
SUBMISSION_EXTENSIONS = LATEX_EXTENSIONS | set(GRAPHICS_EXTENSIONS) | {
    ".bib", ".bst", ".csv", ".dat", ".txt", ".fd",
    ".nls", ".ilg", ".nlo", ".ist", ".gls", ".glo", ".glg", ".glsdefs",
}
# Omit the unused publisher examples; the compiled manuscript PDF is excluded
# separately. Figure PDFs, existing .bbl files, and nomenclature files are
# deliberately retained.
EXCLUDED_NAMES = {
    "cas-dc-template.tex", "cas-dc-template.pdf", "cas-sc-template.tex", "cas-sc-template.pdf",
}

# Comments are consumed first so example commands in Inkscape headers are
# preserved. The final branch also covers braced PGFPlots CSV paths.
REFERENCES = re.compile(
    r"(?P<comment>(?<!\\)%[^\n]*)"
    r"|\\(?P<import>import|subimport|includefrom|subincludefrom)\*?\s*"
    r"\{(?P<directory>[^{}]*)\}\s*\{(?P<filename>[^{}]*)\}"
    r"|\\(?P<command>input|include|includegraphics|bibliography|bibliographystyle|addbibresource)"
    r"\*?\s*(?:\[[^\]]*\]\s*)?\{(?P<target>[^{}]*)\}"
    r"|\{(?P<braced>[^{}\n]*)\}"
)

# Render each input where it occurs in the manuscript, so local fonts, macros,
# column widths and Inkscape scaling are retained. Ordinary manuscript pages
# are discarded; only figure boxes are shipped to the intermediate PDF.
# The margin protects labels outside an Inkscape picture's nominal bounds.
# PDF inclusions trim that margin WITHOUT clipping the protruding labels.
FIGURE_CAPTURE_PREAMBLE = r"""
\newbox\SubmissionFigureBox
\newcount\SubmissionFigurePage
\newwrite\SubmissionFigureSizes
\immediate\openout\SubmissionFigureSizes=\jobname.sizes
\AddToHook{shipout/before}{\DiscardShipoutBox}
\newcommand{\SubmissionFigure}[2]{%
  \begingroup
  \setbox\SubmissionFigureBox=\hbox{\input{#2}}%
  \global\advance\SubmissionFigurePage by 1
  \immediate\write\SubmissionFigureSizes{#1 \the\SubmissionFigurePage\space
    \the\wd\SubmissionFigureBox\space \the\ht\SubmissionFigureBox\space
    \the\dp\SubmissionFigureBox}%
  \begingroup
    \pagewidth=\dimexpr\wd\SubmissionFigureBox+2in\relax
    \pageheight=\dimexpr\ht\SubmissionFigureBox+\dp\SubmissionFigureBox+2in\relax
    \pdfvariable horigin=0pt
    \pdfvariable vorigin=0pt
    \hoffset=0pt \voffset=0pt
    \csname tex_shipout:D\endcsname\vbox{%
      \kern1in\hbox{\kern1in\copy\SubmissionFigureBox\kern1in}\kern1in}%
  \endgroup
  \leavevmode\box\SubmissionFigureBox
  \endgroup
}
"""


def shorten_filename(name: str) -> str:
    """Keep extensions and distinguish long names with a stable hash suffix."""
    if len(name) <= MAX_FILENAME_LENGTH:
        return name
    path = Path(name)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    stem_length = MAX_FILENAME_LENGTH - len(path.suffix) - len(digest) - 1
    return f"{path.stem[:stem_length]}-{digest}{path.suffix}"


def submission_name(path: Path) -> str:
    """Use .tex for TikZ/Inkscape sources and limit exported filename length."""
    if path.suffix.lower() in TEX_RENAMED_EXTENSIONS:
        path = path.with_suffix(".tex")
    return shorten_filename(path.name)


def collect_files(source: Path, output: Path, main: str, excluded: set[Path]) -> list[Path]:
    """Include ignored PNGs and .bbl files too; do not rely on git's file list."""
    files = []
    names: dict[str, Path] = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.resolve().is_relative_to(output) or any(
            part.startswith(".") or part == "__pycache__" for part in relative.parts
        ):
            continue
        if not path.is_file() or path.suffix.lower() not in SUBMISSION_EXTENSIONS:
            continue
        if any(relative == entry or entry in relative.parents for entry in excluded):
            continue
        # EM may run on a case-insensitive filesystem. Never silently overwrite
        # two distinct files with the same name after flattening and renaming.
        key = submission_name(path).casefold()
        if key in names:
            raise ValueError(
                f"Duplicate submission filename {submission_name(path)!r}: {names[key]} and {relative}"
            )
        names[key] = relative
        files.append(path)
    if source / main not in files:
        raise ValueError(f"Missing main manuscript: {source / main}")
    return files


def rewrite_latex(path: Path, source: Path, files: set[Path]) -> str:
    """Flatten local file arguments, including extensionless inputs/imports."""
    def resolve(value: str, extensions: tuple[str, ...] = ()) -> Path | None:
        # TeX macro arguments in the class/style files are left for TeX itself.
        if not value or "\\" in value or "#" in value:
            return None
        for base in (source, path.parent):
            for suffix in ("", *extensions):
                candidate = (base / (value + suffix)).resolve()
                if candidate in files:
                    return candidate
        return None

    def flatten(value: str, extensions: tuple[str, ...], required: bool = True) -> str:
        value = value.strip()
        resolved = resolve(value, extensions)
        if resolved is not None:
            return submission_name(resolved)
        if required and "\\" not in value and "#" not in value:
            raise ValueError(f"{path.relative_to(source)}: missing submission file {value!r}")
        return value

    def replace(match: re.Match[str]) -> str:
        if match.group("comment") is not None:
            return match.group(0)
        if match.group("import"):
            directory = match.group("directory").strip()
            filename = match.group("filename").strip()
            target = flatten(f"{directory.rstrip('/')}/{filename}" if directory else filename, (".tex",))
            command = "include" if "includefrom" in match.group("import") else "input"
            return f"\\{command}{{{target}}}"
        command = match.group("command")
        if command:
            extensions = {
                "input": (".tex",), "include": (".tex",),
                "includegraphics": GRAPHICS_EXTENSIONS,
                "bibliography": (".bib",), "addbibresource": (".bib",),
                "bibliographystyle": (".bst",),
            }[command]
            targets = match.group("target").split(",") if command == "bibliography" else [match.group("target")]
            rewritten = []
            for target in targets:
                # Standard bibliography styles and class support files can be
                # supplied by TeX itself (e.g. elsart.cls inputs fleqn.clo).
                system_file = command == "bibliographystyle" or (
                    command == "input" and path.suffix.lower() in {".cls", ".sty", ".clo"}
                    and "/" not in target
                    and Path(target.strip()).suffix.lower() in {".clo", ".def", ".cfg", ".sty", ".fd"}
                )
                target = flatten(target, extensions, required=not system_file)
                if command in {"include", "bibliography", "bibliographystyle"}:
                    suffix = extensions[0]
                    if target.endswith(suffix):
                        target = target[:-len(suffix)]
                rewritten.append(target)
            start = match.start("target") - match.start()
            end = match.end("target") - match.start()
            return match.group(0)[:start] + ",".join(rewritten) + match.group(0)[end:]
        value = match.group("braced").strip()
        if "/" in value or Path(value).suffix.lower() in TEX_RENAMED_EXTENSIONS | {".csv", ".dat"}:
            resolved = resolve(value)
            if resolved is not None:
                return "{" + submission_name(resolved) + "}"
            if Path(value).suffix.lower() in {".csv", ".dat"}:
                flatten(value, ())  # Report missing plot data before replacing the output.
        return match.group(0)

    return REFERENCES.sub(replace, path.read_text(encoding="utf-8"))


def precompile_figures(staged: Path, main: str, figure_names: set[str], build: Path, log: Path) -> None:
    """Replace the manuscript's figure inputs with PDFs rendered in context."""
    for executable in ("lualatex", "pdfseparate"):
        if shutil.which(executable) is None:
            raise ValueError(f"--precompile requires {executable}; install it before exporting.")

    manuscript = staged / main
    original = manuscript.read_text(encoding="utf-8")
    figures: list[tuple[re.Match[str], str]] = []
    occupied_names = {path.name.casefold() for path in staged.iterdir()}

    def capture(match: re.Match[str]) -> str:
        if match.group("command") != "input" or match.group("target") not in figure_names:
            return match.group(0)
        index = len(figures) + 1
        target = match.group("target")
        pdf_name = shorten_filename(f"figure-{index:02d}-{Path(target).stem}.pdf")
        if pdf_name.casefold() in occupied_names:
            raise ValueError(f"Precompiled figure filename already exists: {pdf_name}")
        occupied_names.add(pdf_name.casefold())
        figures.append((match, pdf_name))
        return f"\\SubmissionFigure{{{index}}}{{{target}}}"

    captured = REFERENCES.sub(capture, original)
    if not figures:
        raise ValueError(f"No figure inputs found in {main} for --precompile; check --figure-dir.")
    # Skip comments while locating the preamble/body boundary.
    boundaries = re.finditer(r"(?<!\\)%[^\n]*|\\begin\s*\{document\}", captured)
    begin_document = next((match for match in boundaries if match.group().startswith("\\")), None)
    if begin_document is None:
        raise ValueError(f"Missing \\begin{{document}} in {main}.")
    captured = captured[:begin_document.start()] + FIGURE_CAPTURE_PREAMBLE + captured[begin_document.start():]

    build.mkdir()
    driver = build / "figures.tex"
    driver.write_text(captured, encoding="utf-8")
    cache = build / "tex-cache"
    cache.mkdir()
    environment = dict(os.environ, TEXMFVAR=str(cache), TEXMFCACHE=str(cache))
    print(f"Precompiling {len(figures)} figures with LuaLaTeX ...", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        def run(command: list[str]) -> None:
            result = subprocess.run(
                command, cwd=staged, env=environment,
                stdout=stream, stderr=subprocess.STDOUT,
            )
            if result.returncode:
                raise ValueError(f"Figure precompilation failed ({command[0]}). See {log}")

        run([
            "lualatex", "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape",
            f"-output-directory={build}", str(driver),
        ])
        sizes = (build / "figures.sizes").read_text(encoding="utf-8").splitlines()
        if len(sizes) != len(figures):
            raise ValueError(f"Not all figure inputs rendered exactly once. See {log}")
        rendered: dict[int, tuple[int, str, str, str]] = {}
        for line in sizes:
            fields = line.split()
            if len(fields) != 5 or not all(re.fullmatch(r"-?\d+(?:\.\d+)?pt", dim) for dim in fields[2:]):
                raise ValueError(f"Invalid figure dimensions: {line!r}. See {log}")
            index, page = map(int, fields[:2])
            if index in rendered:
                raise ValueError(f"Figure input {index} rendered more than once. See {log}")
            rendered[index] = (page, *fields[2:])
        if set(rendered) != set(range(1, len(figures) + 1)):
            raise ValueError(f"Some figure inputs were not rendered. See {log}")

        run(["pdfseparate", str(build / "figures.pdf"), str(build / "figure-%d.pdf")])
        # Replace from the end to keep the original match offsets valid.
        for index in range(len(figures), 0, -1):
            match, pdf_name = figures[index - 1]
            page, width, height, depth = rendered[index]
            shutil.copy2(build / f"figure-{page}.pdf", staged / pdf_name)
            graphic = (
                f"\\raisebox{{-{depth}}}[{height}][{depth}]{{"
                f"\\includegraphics[trim=1in 1in 1in 1in,width={width},"
                f"height=\\dimexpr{height}+{depth}\\relax]{{{pdf_name}}}"
                "}"
            )
            # A trailing \vspace belongs to the surrounding paragraph, not
            # the PDF's graphical box. The surface-plot overlays use this to
            # keep their subcaptions below the protruding axis labels.
            figure_source = (staged / match.group("target")).read_text(encoding="utf-8")
            figure_source = re.sub(r"(?<!\\)%[^\n]*", "", figure_source)
            endings = list(re.finditer(r"\\end\s*\{(?:tikzpicture|picture)\}", figure_source))
            if endings:
                trailing = figure_source[endings[-1].end():]
                graphic += "".join(re.findall(r"\\vspace\*?\s*\{[^{}]+\}", trailing))
            original = original[:match.start()] + graphic + original[match.end():]
    manuscript.write_text(original, encoding="utf-8")
    print(f"Precompiled {len(figures)} figures. Build log: {log}", flush=True)


def remove_unused_files(staged: Path, main: str, build: Path, log: Path) -> None:
    """Keep recorded manuscript inputs and the files needed to rebuild its bibliography."""
    jobname = Path(main).stem
    bibliography = staged / f"{jobname}.bbl"
    if bibliography.is_file():
        shutil.copy2(bibliography, build / bibliography.name)
    cache = build / "tex-cache"
    environment = dict(os.environ, TEXMFVAR=str(cache), TEXMFCACHE=str(cache))
    print("Checking manuscript dependencies before removing unused files ...", flush=True)
    needed: set[Path] = set()
    previous_state = None
    with log.open("a", encoding="utf-8") as stream:
        stream.write("\nChecking the precompiled manuscript with LaTeX's file recorder.\n")
        # The class loads some assets (e.g. the email icon) only after author
        # metadata has been read back from the .aux. Keep inputs from every
        # pass and wait for the auxiliary files to stabilize before pruning.
        for attempt in range(1, 6):
            stream.write(f"\nDependency check, pass {attempt}.\n")
            stream.flush()
            result = subprocess.run(
                [
                    "lualatex", "-recorder", "-interaction=nonstopmode", "-halt-on-error",
                    "-no-shell-escape", f"-output-directory={build}", main,
                ],
                cwd=staged, env=environment, stdout=stream, stderr=subprocess.STDOUT,
            )
            if result.returncode:
                raise ValueError(f"Manuscript dependency check failed. See {log}")
            for line in (build / f"{jobname}.fls").read_text(encoding="utf-8").splitlines():
                if line.startswith("INPUT "):
                    path = (staged / line[6:]).resolve()
                    if path.parent == staged:
                        needed.add(path)
            state = {
                path.name: path.read_bytes() for path in build.iterdir()
                if path.stem == jobname and path.suffix in {".aux", ".out", ".toc", ".lof", ".lot", ".abs"}
            }
            if state == previous_state:
                break
            previous_state = state
        else:
            raise ValueError(f"Manuscript auxiliary files did not stabilize after 5 passes. See {log}")
    if staged / main not in needed:
        raise ValueError(f"Manuscript missing from LaTeX's recorded inputs. See {log}")
    if bibliography.is_file():
        needed.add(bibliography)

    # BibTeX reads .bib/.bst files separately, so LuaLaTeX's recorder omits
    # them. Keep the local bibliography dependencies declared by retained TeX.
    extensions = {"bibliography": ".bib", "addbibresource": ".bib", "bibliographystyle": ".bst"}
    for path in sorted(needed):
        if path.suffix.lower() not in LATEX_EXTENSIONS:
            continue
        for match in REFERENCES.finditer(path.read_text(encoding="utf-8")):
            command = match.group("command")
            if command not in extensions:
                continue
            targets = match.group("target").split(",") if command == "bibliography" else [match.group("target")]
            for target in targets:
                for suffix in ("", extensions[command]):
                    dependency = staged / (target.strip() + suffix)
                    if dependency.is_file():
                        needed.add(dependency.resolve())
                        break

    unused = [path for path in staged.iterdir() if path.is_file() and path not in needed]
    for path in unused:
        path.unlink()
    print(f"Removed {len(unused)} unused files from the precompiled submission.", flush=True)


def is_previous_export(output: Path, main: str) -> bool:
    """Only replace an empty directory or a flat export with its test build."""
    children = list(output.iterdir())
    return not children or ((output / main).is_file() and all(
        child.is_file() or child.name == "test_compilation" for child in children
    ))


def flatten_submission(
    source: Path, *, output: Path | None = None, main: str = DEFAULT_MAIN,
    figure_dirs: tuple[str, ...] = DEFAULT_FIGURE_DIRS, excludes: tuple[str, ...] = (),
    precompile: bool = False,
) -> tuple[Path, int]:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"Project directory not found: {source}")
    if Path(main).name != main or Path(main).suffix != ".tex" or len(main) > MAX_FILENAME_LENGTH:
        raise ValueError(f"Main manuscript must be a .tex file in the project root: {main!r}")
    if not (source / main).is_file():
        raise ValueError(f"Missing main manuscript: {source / main}")
    # Resolve the parent only, so a symlinked output is still detected below.
    output = Path(os.path.abspath(output or source / OUTPUT_NAME))
    output = output.parent.resolve() / output.name
    if output == source or source.is_relative_to(output):
        raise ValueError(f"Output must not contain the project directory: {output}")
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError(f"Output must be a regular directory: {output}")
    if output.exists() and not is_previous_export(output, main):
        raise ValueError(
            f"Refusing to replace {output}: it is not empty and is not a previous export of {main}."
        )
    # A default export left in the project must not be collected when --output
    # points elsewhere.
    excluded = {Path(name) for name in (*EXCLUDED_NAMES, *excludes, OUTPUT_NAME)}
    excluded.add(Path(main).with_suffix(".pdf"))
    files = collect_files(source, output, main, excluded)
    resolved_files = {path.resolve() for path in files}
    log = output.parent / "precompile_figures.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Finish copying and validating first so a bad reference or name collision
    # does not destroy the previously generated submission.
    with tempfile.TemporaryDirectory(prefix=".submission-", dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        staged.mkdir()
        for path in files:
            destination = staged / submission_name(path)
            if path.suffix.lower() in LATEX_EXTENSIONS:
                destination.write_text(rewrite_latex(path, source, resolved_files), encoding="utf-8")
            else:
                shutil.copy2(path, destination)
        if precompile:
            figure_roots = [(source / directory).resolve() for directory in figure_dirs]
            figure_names = {
                submission_name(path) for path in files
                if any(path.resolve().is_relative_to(root) for root in figure_roots)
                and path.suffix.lower() in {".tex", ".tikz", ".pdf_tex"}
            }
            precompile_figures(staged, main, figure_names, Path(temporary) / "figure-build", log)
            remove_unused_files(staged, main, Path(temporary) / "figure-build", log)
        staged_files = list(staged.iterdir())
        for path in staged_files:
            if len(path.name) > MAX_FILENAME_LENGTH:
                raise ValueError(f"Submission filename exceeds {MAX_FILENAME_LENGTH} characters: {path.name}")
        count = len(staged_files)
        if output.exists():
            shutil.rmtree(output)
        staged.rename(output)
    return output, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="manuscript directory containing the main .tex file")
    parser.add_argument(
        "--main", default=DEFAULT_MAIN,
        help=f"main manuscript file in the project root (default: {DEFAULT_MAIN})",
    )
    parser.add_argument(
        "--output", type=Path,
        help=f"submission directory to create or replace (default: PROJECT/{OUTPUT_NAME})",
    )
    parser.add_argument(
        "--figure-dir", action="append", dest="figure_dirs", metavar="DIR",
        help="project subdirectory whose TeX inputs --precompile renders; repeatable "
             f"(default: {', '.join(DEFAULT_FIGURE_DIRS)})",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATH",
        help="project-relative file or directory to leave out of the export; repeatable",
    )
    parser.add_argument(
        "--precompile", action="store_true",
        help="render figures as PDFs and remove unused export files (requires LuaLaTeX and pdfseparate)",
    )
    args = parser.parse_args()
    try:
        output, count = flatten_submission(
            args.project, output=args.output, main=args.main,
            figure_dirs=tuple(args.figure_dirs or DEFAULT_FIGURE_DIRS),
            excludes=tuple(args.exclude), precompile=args.precompile,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Created {output} with {count} files and no subfolders.")
    print(f"Select {args.main} as the main manuscript in Editorial Manager.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
