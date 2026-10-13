# Elsevier publication-ready LaTeX export

This tool turns a LaTeX manuscript that is organized in subfolders into a flat
folder you can upload to Elsevier's Editorial Manager (EM). It can also render
TikZ, PGFPlots and Inkscape (SVG) figures as PDFs first, so EM receives only
the manuscript, its figure PDFs, and the class and bibliography files.

The scripts use only the Python standard library. A demo manuscript is included
to test the pipeline.

```
elsevier-publication-ready/
├── flatten_submission.py   # source project -> flat submission folder
├── compile_submission.py   # test-compiles the flat folder before upload
└── demo/                   # dummy Elsevier CAS manuscript with subfolders
    ├── paper.tex
    ├── references.bib
    ├── cas-dc.cls, cas-common.sty, cas-model2-names.bst
    ├── thumbnails/*.jpeg               # icons loaded by cas-common.sty
    └── figure/
        ├── workflow/workflow.tikz      # TikZ diagram
        ├── results/results.tex         # PGFPlots plot ...
        ├── results/results_data.csv    # ... reading CSV data
        ├── setup/setup.svg             # Inkscape drawing (source) ...
        ├── setup/setup.pdf             # ... exported graphics
        └── setup/setup.pdf_tex         # ... exported LaTeX text overlay
```

## Requirements

| Task | Needs |
| --- | --- |
| Flatten only | Python 3.9 or newer |
| `--precompile` | LuaLaTeX and `pdfseparate` (Poppler, e.g. Debian/Ubuntu `poppler-utils`) |
| `compile_submission.py` | `latexmk`, LuaLaTeX and BibTeX |
| Re-exporting SVG drawings | Inkscape 1.x (not called by the scripts) |

With `--precompile`, your manuscript must compile with LuaLaTeX. The pipeline
was tested with TeX Live 2022.

## Quick start with the demo

Run these commands from this directory:

```bash
python3 flatten_submission.py demo --precompile
python3 compile_submission.py demo/submission
```

The first command creates `demo/submission/` with 9 files and no subfolders:

```
cas-common.sty  cas-dc.cls  cas-email.jpeg  cas-model2-names.bst
figure-01-workflow.pdf  figure-02-results.pdf  figure-03-setup.pdf
paper.tex  references.bib
```

The second command builds `demo/submission/test_compilation/paper.pdf`. It
should look the same as a direct build of `demo/paper.tex`.

Without `--precompile`, the export keeps every source file (17 files): the
TikZ and PGFPlots sources, the CSV data, the SVG, the Inkscape PDF and overlay,
and all six thumbnails.

To reset the demo, delete `demo/submission/` and `demo/precompile_figures.log`.

## What the scripts do

### `flatten_submission.py PROJECT`

1. Collects the submission files in `PROJECT`, including those in subfolders:
   TeX sources, classes and styles, images, `.bib`, `.bst` and `.bbl` files,
   CSV/DAT plot data, and nomenclature files. It skips hidden folders,
   `__pycache__/`, build artifacts (`.aux`, `.log`, ...), the compiled
   `<main>.pdf`, Elsevier's template example files, and the submission
   folder itself.
2. Copies all of them into one folder. TikZ `.tikz` files and Inkscape
   `.pdf_tex` overlays are renamed to `.tex`.
3. Rewrites the literal file references in every exported TeX file:
   `\input`, `\include`, `\includegraphics`, `\bibliography`,
   `\bibliographystyle`, `\addbibresource`, `\import`/`\subimport` (which become
   `\input`), and braced paths such as PGFPlots CSV tables. For example,
   `\input{figure/results/results}` becomes `\input{results.tex}`.
4. Stops before replacing the previous export if a referenced file is missing
   or if two files would get the same flat name. The name check ignores case.
   Names longer than 64 characters are shortened with a stable hash suffix.

Your source files are never modified.

### `--precompile`

After flattening, the script also does the following:

1. Renders every figure input written in the main `.tex` file from a figure
   directory (`figure/` or `figures/` by default). The figures are rendered
   in place, in a LuaLaTeX run of the full manuscript. This keeps the
   preamble macros, fonts, column width, `\svgwidth` and Inkscape labels as
   they appear in the paper.
2. Saves each figure as `figure-NN-<name>.pdf` and replaces its `\input` with
   an `\includegraphics` of the same size and baseline. Wrappers such as
   `\resizebox` and the captions stay as they were:

   ```latex
   % demo/paper.tex
   \resizebox{\columnwidth}{!}{\input{figure/workflow/workflow.tikz}}
   % demo/submission/paper.tex
   \resizebox{\columnwidth}{!}{\raisebox{-0.0pt}[71.93181pt][0.0pt]{\includegraphics[trim=1in 1in 1in 1in,width=255.85538pt,height=\dimexpr71.93181pt+0.0pt\relax]{figure-01-workflow.pdf}}}
   ```

   Each PDF has a 1 in margin, which `trim` removes without clipping. Labels
   that stick out of a drawing's nominal bounds therefore stay visible.
3. Compiles the rewritten manuscript with LaTeX's file recorder until its
   auxiliary files stop changing. It then deletes every exported file that
   LaTeX did not load. The `.bib`/`.bst` files declared by the manuscript and
   an existing `.bbl` are kept.

The build log is `precompile_figures.log`, next to the submission folder.

### `compile_submission.py SUBMISSION_DIR`

This script runs `latexmk -lualatex -bibtex` on the flat folder. All outputs,
including the PDF, `latexmk.log` and a private font cache, go to
`SUBMISSION_DIR/test_compilation/`, so the upload files stay unchanged.

## Using it with your own manuscript

1. Put the main `.tex` file at the project root. The scripts look for
   `paper.tex` unless you pass `--main`. Keep the Elsevier class files in the
   project, as in `demo/`.
2. Put figure sources under `figure/` or `figures/`, or name the directories
   with `--figure-dir`.
3. Write file references as literal, braced paths relative to the project
   root or to the including file (see [Figure conventions](#figure-conventions)).
4. Export, test-compile and check the PDF:

   ```bash
   python3 flatten_submission.py ../my-paper --precompile
   python3 compile_submission.py ../my-paper/submission
   ```

5. Upload every file in the submission folder **except `test_compilation/`**,
   and select the main `.tex` file as the manuscript in Editorial Manager.
   EM then builds its own PDF; compare it with `test_compilation/paper.pdf`.

Running the flatten script again replaces the submission folder. Make your
changes in the sources, not in the export.

### Figure conventions

**TikZ / PGFPlots.** Keep the `tikzpicture` in its own file and input it
directly from the main file. Shared libraries, packages and macros belong in
the main preamble. PGFPlots data paths must be literal and braced:

```latex
\resizebox{\columnwidth}{!}{\input{figure/workflow/workflow.tikz}}
\input{figure/results/results}   % inside it: table[...]{./figure/results/results_data.csv}
```

**SVG drawn in Inkscape.** LaTeX cannot read SVG directly. Export the drawing
as PDF + LaTeX: the PDF holds the graphics and a `.pdf_tex` overlay holds the
text, which LaTeX typesets in the paper's fonts. Text in the SVG can therefore
contain LaTeX, such as `$\boldsymbol{u}_{\Cycle+1}$` in the demo. You can
export in two ways:

- In the GUI, use *Save a Copy… → PDF* and choose *Omit text in PDF and create
  LaTeX file*.
- On the command line:

  ```bash
  inkscape setup.svg --export-type=pdf --export-latex --export-filename=setup.pdf
  ```

Then include the overlay. This needs `\usepackage{import}` and `graphicx`:

```latex
\def\svgwidth{\columnwidth}
\import{./figure/setup/}{setup.pdf_tex}
```

The `.svg` file is copied as a source in the default mode and dropped by
`--precompile`. Re-export after each edit, because the scripts never call
Inkscape. In the demo, `setup.pdf` and `setup.pdf_tex` use Inkscape's export
format, so re-exporting `setup.svg` replaces them.

**Raster images and ready-made PDFs.** Include them with `\includegraphics`.
They are copied as they are and kept.

### Options

`flatten_submission.py PROJECT [options]`

| Option | Default | Purpose |
| --- | --- | --- |
| `--main FILE` | `paper.tex` | Main manuscript, a `.tex` file in the project root |
| `--output DIR` | `PROJECT/submission` | Submission folder to create or replace |
| `--figure-dir DIR` | `figure`, `figures` | Directory whose TeX inputs `--precompile` renders (repeatable) |
| `--exclude PATH` | — | Project-relative file or directory to leave out (repeatable) |
| `--precompile` | off | Render figures as PDFs and remove unused files |

`compile_submission.py SUBMISSION_DIR [--main FILE]`, where `--main`
defaults to `paper.tex`.

The scripts resolve paths relative to the current directory, so you can run
them from anywhere.

## Limitations and troubleshooting

- **Literal paths only.** The scripts do not interpret TeX macros. References
  built from macros or found through `\graphicspath` are not rewritten. Adapt
  the source or the script. Any braced literal path that matches a project
  file is rewritten, even in body text such as `\texttt{figure/a.png}`.
- **Missing file errors.** References are checked in class and style files
  too. For example, `cas-common.sty` names all six `thumbnails/` icons, so the
  default export needs all of them and `--exclude thumbnails` fails.
  `--precompile` removes the icons that LaTeX does not load.
- **Only direct figure inputs are rendered.** A figure that is `\input` from
  another included `.tex` file is exported as TeX source. A figure must be
  typeset exactly once: environments that typeset their content several
  times, such as `tabularx`, cause *"Not all figure inputs rendered exactly
  once"*.
- **No shell escape.** Rendering runs with `-no-shell-escape`. Packages that
  call external programs, such as `\includesvg` from the `svg` package,
  `minted` or TikZ externalization, will not work. Export SVGs with Inkscape
  as described above.
- **Replacing the output is guarded.** The output folder is replaced only if
  it is empty or is a previous export: it contains the main file and only
  files, plus `test_compilation/`. It may not contain the project. Anything
  else is refused.
- **Default exclusions.** `cas-dc-template.*`, `cas-sc-template.*` (`.tex` and
  `.pdf`), the compiled `<main>.pdf` and the `submission/` folder are left out.
  Add more with `--exclude`.
- **Template footer.** The demo's `cas-common.sty` is the copy from this
  repository's manuscript. Its first-page footer reads "Preprint submitted to
  Journal of Process Control" (around line 1989). Edit it, or use the template
  files for your journal.
- **Logs.** When a step fails, the export stops, the error is printed, and
  the previous submission folder is preserved. LaTeX output from rendering and
  the dependency check is in `precompile_figures.log`, next to the submission
  folder. Test builds log to `test_compilation/latexmk.log`.

## Licenses

The files in `demo/` named `cas-dc.cls`, `cas-common.sty` and
`cas-model2-names.bst`, and the icons in `demo/thumbnails/`, are part of
Elsevier's CAS (`cas-dc`) and `elsarticle` template bundles. They are not
original to this repository and are distributed under the
[LaTeX Project Public License](https://www.latex-project.org/lppl/) (LPPL).
Their file headers state version 1.2 or later for `cas-dc.cls` and version
1.3c or later for the other two. Keep those headers when you copy or modify
the files. Get the current template files from Elsevier or CTAN for your own
manuscript.

The scripts, the README and the demo manuscript, `references.bib` and figure
sources in `demo/` are released under the [MIT License](LICENSE). The MIT
License does not apply to the Elsevier template files listed above.
