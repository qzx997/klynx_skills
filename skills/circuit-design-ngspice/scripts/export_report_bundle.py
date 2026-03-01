#!/usr/bin/env python3
"""Export markdown report to HTML/PDF with best-effort PDF backend selection."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export report.md to HTML/PDF")
    parser.add_argument("--report-path", required=True, help="Input markdown report path")
    parser.add_argument("--html-path", default="", help="Output HTML path (default: report.html)")
    parser.add_argument("--pdf-path", default="", help="Output PDF path (default: report.pdf)")
    parser.add_argument("--skip-pdf", action="store_true", help="Only export HTML")
    parser.add_argument("--require-pdf", action="store_true", help="Return non-zero if PDF export fails")
    return parser


def convert_markdown_to_html(md_text: str) -> tuple[str, str]:
    try:
        import markdown  # type: ignore

        body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
        engine = "python-markdown"
    except Exception:
        body = f"<pre>{html.escape(md_text)}</pre>"
        engine = "preformatted-fallback"

    css = """
<style>
body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; margin: 28px; color: #1f2328; }
h1, h2, h3 { color: #0b2f5f; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 20px; }
th, td { border: 1px solid #c9d1d9; padding: 6px 8px; vertical-align: top; }
code { background: #f6f8fa; padding: 1px 4px; border-radius: 4px; }
pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto; }
img { max-width: 100%; height: auto; border: 1px solid #e5e7eb; margin: 8px 0; }
</style>
"""
    html_text = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Design Report</title>"
        + css
        + "</head><body>"
        + body
        + "</body></html>\n"
    )
    return html_text, engine


def try_pdf_weasyprint(html_text: str, base_dir: Path, pdf_path: Path) -> tuple[bool, str]:
    try:
        from weasyprint import HTML  # type: ignore

        HTML(string=html_text, base_url=str(base_dir)).write_pdf(str(pdf_path))
        return True, "weasyprint"
    except Exception:
        return False, ""


def try_pdf_wkhtmltopdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    exe = shutil.which("wkhtmltopdf")
    if not exe:
        return False, ""
    completed = subprocess.run(
        [exe, str(html_path), str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.returncode == 0 and pdf_path.exists()), "wkhtmltopdf"


def try_pdf_chromium(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    exe = next((c for c in candidates if c), "")
    if not exe:
        return False, ""
    completed = subprocess.run(
        [
            exe,
            "--headless",
            "--disable-gpu",
            "--print-to-pdf=" + str(pdf_path),
            str(html_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.returncode == 0 and pdf_path.exists()), "chromium-headless"


def pdf_escape_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def try_pdf_minimal_text(md_text: str, pdf_path: Path) -> tuple[bool, str]:
    # Minimal PDF fallback that embeds markdown text as monospaced lines.
    page_w = 612
    page_h = 792
    left = 40
    top = 760
    line_h = 12
    max_lines = 58
    max_chars = 105

    raw_lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for raw in raw_lines:
        text = raw if raw else " "
        while len(text) > max_chars:
            lines.append(text[:max_chars])
            text = text[max_chars:]
        lines.append(text)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + ["[truncated in fallback PDF output]"]

    stream_lines = ["BT", "/F1 9 Tf", f"{left} {top} Td"]
    first = True
    for line in lines:
        if not first:
            stream_lines.append(f"0 -{line_h} Td")
        stream_lines.append(f"({pdf_escape_text(line)}) Tj")
        first = False
    stream_lines.append("ET")
    stream = "\n".join(stream_lines) + "\n"
    stream_bytes = stream.encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        (
            "3 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
            "endobj\n"
        ).encode("ascii")
    )
    objects.append(
        b"4 0 obj\n<< /Length "
        + str(len(stream_bytes)).encode("ascii")
        + b" >>\nstream\n"
        + stream_bytes
        + b"endstream\nendobj\n"
    )
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n")

    pdf = bytearray()
    pdf.extend(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_offset = len(pdf)
    count = len(objects) + 1
    pdf.extend(f"xref\n0 {count}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            "trailer\n"
            f"<< /Size {count} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )

    try:
        pdf_path.write_bytes(pdf)
    except OSError:
        return False, ""
    return pdf_path.exists() and pdf_path.stat().st_size > 200, "minimal-text-pdf"


def main() -> int:
    args = build_parser().parse_args()

    report_path = Path(args.report_path).resolve()
    html_path = Path(args.html_path).resolve() if args.html_path else report_path.with_suffix(".html")
    pdf_path = Path(args.pdf_path).resolve() if args.pdf_path else report_path.with_suffix(".pdf")

    result = {
        "ok": False,
        "report_path": str(report_path),
        "html_path": str(html_path),
        "pdf_path": str(pdf_path),
        "html_ok": False,
        "pdf_ok": False,
        "html_engine": "",
        "pdf_engine": "",
        "warnings": [],
    }

    if not report_path.exists():
        result["warnings"].append(f"report not found: {report_path}")
        print(json.dumps(result, ensure_ascii=False))
        return 1

    md_text = report_path.read_text(encoding="utf-8-sig", errors="ignore")
    html_text, html_engine = convert_markdown_to_html(md_text)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")
    result["html_ok"] = True
    result["html_engine"] = html_engine

    if not args.skip_pdf:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        ok_pdf, pdf_engine = try_pdf_weasyprint(html_text, html_path.parent, pdf_path)
        if not ok_pdf:
            ok_pdf, pdf_engine = try_pdf_wkhtmltopdf(html_path, pdf_path)
        if not ok_pdf:
            ok_pdf, pdf_engine = try_pdf_chromium(html_path, pdf_path)
        if not ok_pdf:
            ok_pdf, pdf_engine = try_pdf_minimal_text(md_text, pdf_path)

        if ok_pdf:
            result["pdf_ok"] = True
            result["pdf_engine"] = pdf_engine
        else:
            result["warnings"].append(
                "PDF export backend unavailable (tried weasyprint/wkhtmltopdf/chromium-headless)"
            )

    result["ok"] = result["html_ok"] and (result["pdf_ok"] or args.skip_pdf or not args.require_pdf)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
