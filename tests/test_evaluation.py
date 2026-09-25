from html import escape
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from markupdoc.evaluation import evaluate_documents, render_assessment, write_evaluation


def _docx(path: Path, paragraphs: list[str], *, bold: bool = False) -> Path:
    formatting = "<w:rPr><w:b/></w:rPr>" if bold else ""
    body = "".join(f"<w:p><w:r>{formatting}<w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs)
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr/></w:body></w:document>'
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return path


def test_exact_reference_match_records_operations_without_accuracy_claim(tmp_path):
    original = _docx(tmp_path / "original.docx", ["The cat sat.", "Unchanged paragraph."])
    predicted = _docx(tmp_path / "predicted.docx", ["The dog sat.", "Unchanged paragraph."])
    reference = _docx(tmp_path / "reference.docx", ["The dog sat.", "Unchanged paragraph."])
    result = evaluate_documents(original, predicted, reference)
    assert result["exact_document_text_match"] is True
    assert result["counts"] == {
        "reference_diff_operations": 1,
        "predicted_diff_operations": 1,
        "exactly_matched_diff_operations": 1,
        "unmatched_reference_diff_operations": 0,
        "unmatched_predicted_diff_operations": 0,
    }
    assert result["reference_changes"][0]["old_text"] == "cat"
    assert result["formatting_structure"]["status"] == "unchanged"
    assert result["formatting_structure"]["visually_verified"] is False
    assert "accuracy" not in result


def test_missed_and_unintended_changes_are_separate_diagnostics(tmp_path):
    original = _docx(tmp_path / "original.docx", ["The cat sat.", "Keep the tail."])
    predicted = _docx(tmp_path / "predicted.docx", ["The cat sat.", "Keep the sail."])
    reference = _docx(tmp_path / "reference.docx", ["The dog sat.", "Keep the tail."])
    result = evaluate_documents(original, predicted, reference)
    assert result["exact_document_text_match"] is False
    assert result["counts"]["exactly_matched_diff_operations"] == 0
    assert result["counts"]["unmatched_reference_diff_operations"] == 1
    assert result["counts"]["unmatched_predicted_diff_operations"] == 1
    assert result["unmatched_reference_changes"][0]["new_text"] == "dog"
    assert result["unmatched_predicted_changes"][0]["old_text"] == "t"


def test_missing_and_inserted_blocks_are_reported(tmp_path):
    original = _docx(tmp_path / "original.docx", ["Alpha", "Beta", "Gamma"])
    predicted = _docx(tmp_path / "predicted.docx", ["Alpha", "Gamma", "Delta"])
    reference = _docx(tmp_path / "reference.docx", ["Alpha", "Beta", "Gamma"])
    comparison = evaluate_documents(original, predicted, reference)["reference_vs_prediction"]
    assert comparison["missing_blocks"] == 1
    assert comparison["unexpected_blocks"] == 1
    assert comparison["equal_blocks"] == 2


def test_formatting_change_flagged_even_when_text_matches(tmp_path):
    original = _docx(tmp_path / "original.docx", ["The cat sat."], bold=True)
    predicted = _docx(tmp_path / "predicted.docx", ["The dog sat."])
    reference = _docx(tmp_path / "reference.docx", ["The dog sat."], bold=True)
    result = evaluate_documents(original, predicted, reference)
    assert result["exact_document_text_match"] is True
    assert result["formatting_structure"]["status"] == "review_required"
    assert result["formatting_structure"]["changed_parts"] == ["word/document.xml"]


def test_assessment_and_json_are_written_with_limitations(tmp_path):
    original = _docx(tmp_path / "original.docx", ["No changes."])
    result = evaluate_documents(original, original, original)
    paths = write_evaluation(result, tmp_path / "assessment")
    assert json.loads(Path(paths["evaluation"]).read_text(encoding="utf-8")) == result
    assessment = Path(paths["assessment"]).read_text(encoding="utf-8")
    assert assessment == render_assessment(result)
    assert "unmarked edits" in assessment
    assert "Visual verification: **not performed**" in assessment
    assert "handwriting-recognition accuracy" in assessment
    assert "| Exactly matching diff operations | 0 |" in assessment
    assert result["reference_changed_original_text"] is False


def test_block_boundary_difference_cannot_be_hidden_by_joined_text(tmp_path):
    original = _docx(tmp_path / "original.docx", ["Alpha\nBeta"])
    predicted = _docx(tmp_path / "predicted.docx", ["Alpha", "Beta"])
    result = evaluate_documents(original, predicted, original)
    assert result["exact_document_text_match"] is False
    assert result["reference_vs_prediction"]["difference_groups"]
    assert "Boundary-only changes" in result["diff_offset_basis"]


def test_write_evaluation_refuses_to_overwrite_existing_artifacts(tmp_path):
    original = _docx(tmp_path / "original.docx", ["No changes."])
    result = evaluate_documents(original, original, original)
    destination = tmp_path / "results"
    destination.mkdir()
    assessment = destination / "assessment.md"
    assessment.write_text("Existing report", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_evaluation(result, destination)
    assert assessment.read_text(encoding="utf-8") == "Existing report"
    assert not (destination / "evaluation.json").exists()


def test_assessment_prominently_displays_experiment_note(tmp_path):
    original = _docx(tmp_path / "original.docx", ["Demonstration."])
    result = evaluate_documents(original, original, original)
    result["experiment_note"] = "Offline synthetic demonstration; supplied edits, no handwriting recognition measured."
    assessment = render_assessment(result)
    assert assessment.startswith("# Document pilot assessment\n\n**Experiment note: Offline synthetic demonstration;")
    assert assessment.index(result["experiment_note"]) < assessment.index("Exact inspected document text")
    assert "matches the reference: **yes**" in assessment
    assert "matches the human reference" not in assessment
