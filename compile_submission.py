#!/usr/bin/env python3
"""Compile a flat submission with LuaLaTeX; keep outputs in test_compilation/."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "submission_dir", type=Path,
        help="flat submission directory created by flatten_submission.py",
    )
    parser.add_argument("--main", default="paper.tex", help="main manuscript file (default: paper.tex)")
    args = parser.parse_args()
    submission = args.submission_dir.resolve()
    manuscript = submission / args.main
    if not manuscript.is_file():
        parser.error(f"Missing {manuscript}; run flatten_submission.py first.")
    for executable in ("latexmk", "lualatex", "bibtex"):
        if shutil.which(executable) is None:
            parser.error(f"{executable} is required; install it with your TeX distribution.")

    output = submission / "test_compilation"
    output.mkdir(exist_ok=True)
    # Seed the output bibliography so latexmk associates it with the output
    # .aux, rather than trying to run BibTeX against the submission root.
    bibliography = manuscript.with_suffix(".bbl")
    if bibliography.is_file() and not (output / bibliography.name).exists():
        shutil.copy2(bibliography, output / bibliography.name)
    cache = output / "tex-cache"
    cache.mkdir(exist_ok=True)
    environment = dict(os.environ, TEXMFVAR=str(cache), TEXMFCACHE=str(cache))
    log = output / "latexmk.log"
    print(f"Compiling {manuscript} ...", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [
                "latexmk", "-g", "-lualatex", "-bibtex",
                "-interaction=nonstopmode", "-halt-on-error",
                f"-outdir={output}", args.main,
            ],
            cwd=submission, env=environment,
            stdout=stream, stderr=subprocess.STDOUT,
        )
    if result.returncode:
        print(f"Compilation failed. See {log}")
    else:
        print(f"PDF: {output / manuscript.with_suffix('.pdf').name}")
    print(f"Build log: {log}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
