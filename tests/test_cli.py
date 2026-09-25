import json
from pathlib import Path

from pypdf import PdfReader

from markupdoc.cli import main
from markupdoc.docx_engine import inspect_docx
from markupdoc.pdf_input import inspect_pdf, render_pdf_pages


def test_offline_workflow_and_apply_preview_copy(tmp_path, capsys):
    demo = tmp_path / "demo"
    assert main(["demo", "--out", str(demo)]) == 0
    original = demo / "sample" / "original.docx"
    original_bytes = original.read_bytes()
    report = json.loads((demo / "application-report.json").read_text())
    assert report["summary"] == {"proposed": 5, "applied": 4, "needs_review": 1}
    assert len(PdfReader(demo / "sample" / "marked-scan.pdf").pages) == 1
    assert not PdfReader(demo / "sample" / "marked-scan.pdf").pages[0].extract_text().strip()
    actual = [b["text"] for b in inspect_docx(demo / "edited.docx")["blocks"]]
    expected = [b["text"] for b in inspect_docx(demo / "sample" / "reference.docx")["blocks"]]
    assert actual == expected
    applied = tmp_path / "applied"
    assert main(["apply", "--original", str(original), "--edits", str(demo / "edits.json"), "--out", str(applied)]) == 0
    assert (applied / "scan-pages" / "page-001.png").is_file()
    assert 'src="scan-pages/page-001.png"' in (applied / "review.html").read_text(encoding="utf-8")
    assert original.read_bytes() == original_bytes
    evaluation = tmp_path / "eval"
    assert main(["evaluate", "--original", str(original), "--predicted", str(applied / "edited.docx"),
                 "--reference", str(demo / "sample" / "reference.docx"), "--out", str(evaluation)]) == 0
    assert (evaluation / "assessment.md").is_file()
    # An existing output is rejected before anything in it is changed.
    before = (demo / "edits.json").read_bytes()
    assert main(["demo", "--out", str(demo)]) == 2
    assert (demo / "edits.json").read_bytes() == before


def test_pdf_limits_and_no_preview_overwrite(tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    scan = tmp_path / "two-pages.pdf"
    canvas = Canvas(str(scan))
    for _ in range(2):
        canvas.drawString(20, 20, "Test page")
        canvas.showPage()
    canvas.save()
    import pytest
    with pytest.raises(ValueError, match="No pages were processed"):
        inspect_pdf(scan, max_pages=1)
    assert inspect_pdf(scan)["pages"] == 2
    with pytest.raises(ValueError, match="dpi"):
        render_pdf_pages(scan, tmp_path / "invalid", dpi=500)
    pages = render_pdf_pages(scan, tmp_path / "pages", dpi=72)
    assert len(pages) == 2 and all(p.is_file() for p in pages)
    with pytest.raises(ValueError, match="already exist"):
        render_pdf_pages(scan, tmp_path / "pages", dpi=72)


def test_propose_requires_model_before_touching_files(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MARKUPDOC_MODEL", raising=False)
    output = tmp_path / "out"
    assert main(["propose", "--original", "missing.docx", "--scan", "missing.pdf", "--out", str(output)]) == 2
    assert "--model" in capsys.readouterr().err
    assert not output.exists()


def test_wrong_source_plan_fails_without_output(tmp_path):
    from docx import Document
    from markupdoc.demo import _word
    original = tmp_path / "source.docx"
    _word(original)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schema_version": 1, "source_sha256": "0" * 64, "edits": [], "warnings": []}))
    output = tmp_path / "applied"
    assert main(["apply", "--original", str(original), "--edits", str(plan), "--out", str(output)]) == 2
    assert not output.exists()
