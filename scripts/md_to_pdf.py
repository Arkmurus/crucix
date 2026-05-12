"""md_to_pdf — render a markdown file to PDF via Edge headless.

Usage: py scripts/md_to_pdf.py <input.md> [<input.md> ...]
Outputs: same name with .pdf extension, alongside the .md.

Pipeline:
  markdown (with tables + fenced_code + toc) → styled HTML →
  msedge --headless --print-to-pdf → final PDF
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown  # type: ignore[import-not-found]

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

PRINT_CSS = """
:root {
  color-scheme: light;
  --fg: #111;
  --muted: #555;
  --border: #d0d7de;
  --code-bg: #f6f8fa;
  --table-stripe: #f6f8fa;
}
@page {
  size: A4;
  margin: 18mm 16mm 18mm 16mm;
  @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; color: #666; }
}
html, body {
  font: 10.5pt/1.55 -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  color: var(--fg);
  margin: 0;
  background: #fff;
}
.page-shell { max-width: 760px; margin: 0 auto; padding: 0 4mm; }
h1, h2, h3, h4 { color: #111; line-height: 1.25; margin-top: 1.4em; }
h1 { font-size: 22pt; border-bottom: 2px solid var(--border); padding-bottom: 6pt; margin-top: 0; }
h2 { font-size: 15pt; border-bottom: 1px solid var(--border); padding-bottom: 4pt; margin-top: 1.6em; }
h3 { font-size: 12.5pt; }
h4 { font-size: 11pt; color: #333; }
p, li { font-size: 10.5pt; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
hr { border: 0; border-top: 1px solid var(--border); margin: 1.6em 0; }
blockquote { border-left: 3px solid var(--border); margin: 1em 0; padding: 0.4em 1em; color: var(--muted); }
code { font-family: "Cascadia Code", "Consolas", "SF Mono", monospace; background: var(--code-bg); padding: 1px 5px; border-radius: 3px; font-size: 9.5pt; }
pre { background: var(--code-bg); padding: 12pt; border-radius: 6px; overflow-x: auto; font-size: 9pt; line-height: 1.45; }
pre code { background: transparent; padding: 0; font-size: 9pt; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 9.5pt; page-break-inside: auto; }
table thead { background: var(--code-bg); }
th, td { border: 1px solid var(--border); padding: 6px 9px; text-align: left; vertical-align: top; }
tbody tr:nth-child(even) { background: var(--table-stripe); }
ul, ol { padding-left: 1.6em; }
li { margin-bottom: 0.25em; }
strong { color: #111; }
em { color: #333; }
.footer { margin-top: 4em; padding-top: 1em; border-top: 1px solid var(--border); font-size: 8.5pt; color: var(--muted); text-align: center; }
"""

HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="page-shell">
{body}
</div>
</body>
</html>
"""


def find_edge() -> str:
    for path in EDGE_PATHS:
        if os.path.exists(path):
            return path
    raise SystemExit("Microsoft Edge not found at known paths — install Edge or add a path to EDGE_PATHS.")


def render_md_to_pdf(md_path: Path, edge_path: str) -> Path:
    md_text = md_path.read_text(encoding="utf-8")
    body_html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
    )
    title = md_path.stem.replace("_", " ").title()
    html_doc = HTML_SHELL.format(title=title, css=PRINT_CSS, body=body_html)

    pdf_path = md_path.with_suffix(".pdf")
    # msedge --print-to-pdf needs a real file URL on Windows, so we drop a temp .html
    # next to the source so relative links would resolve if the doc had any.
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / (md_path.stem + ".html")
        html_path.write_text(html_doc, encoding="utf-8")
        # Use a fresh user-data-dir to avoid colliding with the user's running Edge.
        user_data_dir = Path(tmp) / "edge-profile"
        user_data_dir.mkdir()
        cmd = [
            edge_path,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={user_data_dir}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not pdf_path.exists():
            raise SystemExit(
                f"Edge headless failed ({result.returncode}):\n"
                f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
    return pdf_path


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    edge_path = find_edge()
    for md_arg in sys.argv[1:]:
        md_path = Path(md_arg).resolve()
        if not md_path.is_file():
            print(f"skip: {md_path} (not a file)")
            continue
        pdf_path = render_md_to_pdf(md_path, edge_path)
        size_kb = pdf_path.stat().st_size / 1024
        print(f"OK  {md_path.name} -> {pdf_path.name}  ({size_kb:,.1f} KB)")


if __name__ == "__main__":
    main()
