"""Reference comparison for a pilot, not a substitute for markup-level review."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .docx_engine import inspect_docx


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TEXT_TAGS = {f"{{{W_NS}}}t", f"{{{W_NS}}}delText", f"{{{W_NS}}}instrText"}
CAVEATS = [
    "The human reference can contain additional, unmarked edits. Differences must be checked against the scanned markup before being called errors.",
    "Exact diff-operation matches are a diagnostic, not measured handwritten-edit accuracy. Character diff boundaries may merge adjacent edits or align repeated text differently.",
    "Text comparison uses the blocks exposed by the document inspector. Unsupported content, including some embedded objects and text boxes, may not be represented.",
    "Formatting checks compare non-text OOXML structure; legitimate run changes can trigger differences. No pages were rendered or visually verified by this evaluation.",
    "No model confidence, training benefit, or production reliability can be established from this comparison alone.",
]


def _block_texts(snapshot: dict[str, Any]) -> list[str]:
    return [str(block.get("text", "")) for block in snapshot.get("blocks", [])]


def _changes(original: str, target: str) -> list[dict[str, Any]]:
    """Return source-offset character operations, preserving exact whitespace."""
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, original, target, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            changes.append({
                "operation": tag,
                "original_start": i1,
                "original_end": i2,
                "old_text": original[i1:i2],
                "new_text": target[j1:j2],
            })
    return changes


def _change_key(change: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(change[key] for key in (
        "operation", "original_start", "original_end", "old_text", "new_text"
    ))


def _block_comparison(reference: list[str], predicted: list[str]) -> dict[str, Any]:
    opcodes = difflib.SequenceMatcher(None, reference, predicted, autojunk=False).get_opcodes()
    groups = []
    counts = {"equal_blocks": 0, "reference_blocks_in_replacements": 0,
              "predicted_blocks_in_replacements": 0, "missing_blocks": 0,
              "unexpected_blocks": 0}
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            counts["equal_blocks"] += i2 - i1
            continue
        if tag == "delete":
            counts["missing_blocks"] += i2 - i1
        elif tag == "insert":
            counts["unexpected_blocks"] += j2 - j1
        else:
            counts["reference_blocks_in_replacements"] += i2 - i1
            counts["predicted_blocks_in_replacements"] += j2 - j1
        groups.append({"operation": tag, "reference_start": i1,
                       "reference_end": i2, "predicted_start": j1,
                       "predicted_end": j2, "reference_text": reference[i1:i2],
                       "predicted_text": predicted[j1:j2]})
    return {**counts, "difference_groups": groups,
            "alignment_note": "Sequence alignment is based on exact block text, not persistent document IDs. Replacements can contain unequal numbers of blocks; missing/unexpected counts cover only unpaired delete/insert groups."}


def _xml_shape(element: ET.Element) -> tuple[Any, ...]:
    # Retain attributes, styles, sections, runs, tabs and breaks. Strip only
    # ordinary text-bearing node contents. Serializer namespace prefixes do not
    # affect this representation because ElementTree expands namespace URIs.
    text = None if element.tag in TEXT_TAGS else (element.text or "").strip()
    return (element.tag, tuple(sorted(element.attrib.items())), text,
            tuple(_xml_shape(child) for child in element))


def _package_structure(path: Path) -> dict[str, str]:
    fingerprints = {}
    with ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("word/") or name.endswith("/"):
                continue
            data = archive.read(name)
            if name.endswith(".xml"):
                try:
                    shape = _xml_shape(ET.fromstring(data))
                    data = repr(shape).encode("utf-8")
                except ET.ParseError:
                    pass
            fingerprints[name] = hashlib.sha256(data).hexdigest()
    return fingerprints


def _formatting_comparison(original: Path, predicted: Path) -> dict[str, Any]:
    try:
        before = _package_structure(original)
        after = _package_structure(predicted)
    except (BadZipFile, OSError, ET.ParseError) as error:
        return {"status": "unavailable", "error": str(error), "visually_verified": False}
    changed = sorted(name for name in before.keys() & after.keys() if before[name] != after[name])
    missing = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    return {
        "status": "unchanged" if not changed and not missing and not added else "review_required",
        "changed_parts": changed, "missing_parts": missing, "added_parts": added,
        "visually_verified": False,
        "method": "Compare word/ package parts after removing text-node values from XML. Relationships and binary assets are compared byte for byte. This is a conservative structural check, not a layout assessment.",
    }


def evaluate_documents(original: str | Path, predicted: str | Path,
                       reference: str | Path) -> dict[str, Any]:
    """Compare a generated DOCX with a withheld human reference.

    The result deliberately contains no overall similarity-as-accuracy score.
    Only an independent markup reviewer can label the correctness of edits.
    """
    paths = {"original": Path(original).resolve(), "predicted": Path(predicted).resolve(),
             "reference": Path(reference).resolve()}
    snapshots = {name: inspect_docx(path) for name, path in paths.items()}
    blocks = {name: _block_texts(snapshot) for name, snapshot in snapshots.items()}
    texts = {name: "\n".join(values) for name, values in blocks.items()}
    expected = _changes(texts["original"], texts["reference"])
    actual = _changes(texts["original"], texts["predicted"])
    expected_keys = {_change_key(change) for change in expected}
    actual_keys = {_change_key(change) for change in actual}
    matched = [change for change in expected if _change_key(change) in actual_keys]
    unmatched_reference = [change for change in expected if _change_key(change) not in actual_keys]
    unmatched_prediction = [change for change in actual if _change_key(change) not in expected_keys]
    return {
        "schema_version": 1,
        "files": {name: {"path": str(path), "sha256": snapshots[name].get("sha256"),
                         "block_count": len(blocks[name])} for name, path in paths.items()},
        "exact_document_text_match": blocks["predicted"] == blocks["reference"],
        "prediction_changed_original_text": blocks["predicted"] != blocks["original"],
        "reference_changed_original_text": blocks["reference"] != blocks["original"],
        "diff_offset_basis": "Zero-based Unicode character offsets in inspected original block text joined with newline separators. Boundary-only changes may disappear in this joined text; inspect the separate block comparison too.",
        "counts": {"reference_diff_operations": len(expected),
                   "predicted_diff_operations": len(actual),
                   "exactly_matched_diff_operations": len(matched),
                   "unmatched_reference_diff_operations": len(unmatched_reference),
                   "unmatched_predicted_diff_operations": len(unmatched_prediction)},
        "reference_changes": expected, "predicted_changes": actual,
        "matched_changes": matched, "unmatched_reference_changes": unmatched_reference,
        "unmatched_predicted_changes": unmatched_prediction,
        "reference_vs_prediction": _block_comparison(blocks["reference"], blocks["predicted"]),
        "original_vs_reference": _block_comparison(blocks["original"], blocks["reference"]),
        "original_vs_prediction": _block_comparison(blocks["original"], blocks["predicted"]),
        "formatting_structure": _formatting_comparison(paths["original"], paths["predicted"]),
        "inspector_warnings": {name: snapshot.get("warnings", []) for name, snapshot in snapshots.items()},
        "caveats": CAVEATS.copy(),
    }


def render_assessment(result: dict[str, Any]) -> str:
    """Return a human-readable assessment using only observed metrics."""
    counts = result["counts"]
    comparison = result["reference_vs_prediction"]
    formatting = result["formatting_structure"]
    lines = ["# Document pilot assessment", ""]
    if result.get("experiment_note"):
        lines.extend([f"**Experiment note: {result['experiment_note']}**", ""])
    lines.extend([
             f"Exact inspected document text matches the reference: **{'yes' if result['exact_document_text_match'] else 'no'}**.", "",
             "These are reference-comparison diagnostics, not handwriting-recognition accuracy.", "",
             "| Diagnostic | Count |", "|---|---:|",
             f"| Original → reference diff operations | {counts['reference_diff_operations']} |",
             f"| Original → prediction diff operations | {counts['predicted_diff_operations']} |",
             f"| Exactly matching diff operations | {counts['exactly_matched_diff_operations']} |",
             f"| Unmatched reference diff operations | {counts['unmatched_reference_diff_operations']} |",
             f"| Unmatched predicted diff operations | {counts['unmatched_predicted_diff_operations']} |",
             f"| Exact reference/prediction block matches | {comparison['equal_blocks']} |",
             f"| Reference blocks in replacement groups | {comparison['reference_blocks_in_replacements']} |",
             f"| Predicted blocks in replacement groups | {comparison['predicted_blocks_in_replacements']} |",
             f"| Missing blocks in delete groups | {comparison['missing_blocks']} |",
             f"| Unexpected blocks in insert groups | {comparison['unexpected_blocks']} |", "",
             comparison["alignment_note"], "",
             f"Non-text OOXML structure check: **{formatting['status']}**. Visual verification: **not performed**.", ""])
    for field in ("changed_parts", "missing_parts", "added_parts"):
        if formatting.get(field):
            lines.append(f"- {field.replace('_', ' ').capitalize()}: {', '.join(formatting[field])}")
    if formatting.get("error"):
        lines.append(f"Structure check error: {formatting['error']}")
    lines.extend(["", "## Files", ""])
    for name, details in result["files"].items():
        lines.append(f"- {name.capitalize()}: `{details['path']}` ({details['block_count']} inspected blocks)")
    lines.extend(["", "## Limits and required review", ""])
    lines.extend(f"- {caveat}" for caveat in result["caveats"])
    warnings = result.get("inspector_warnings", {})
    for name, items in warnings.items():
        lines.extend(f"- {name.capitalize()} inspector warning: {item}" for item in items)
    lines.extend(["", "## Next assessment step", "",
                  "Review each unmatched change against the marked-up scan; label missed markup, incorrect application, ambiguity, or extra human editing. Render and inspect the generated Word document before judging formatting fidelity. Add measured runtime, API cost, and human review time to the pilot results.", ""])
    return "\n".join(lines)


def write_evaluation(result: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write machine-readable diagnostics and a Markdown assessment."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "evaluation.json"
    assessment_path = destination / "assessment.md"
    for path in (json_path, assessment_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite evaluation artifact: {path}")
    with json_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    with assessment_path.open("x", encoding="utf-8") as output:
        output.write(render_assessment(result))
    return {"evaluation": str(json_path.resolve()), "assessment": str(assessment_path.resolve())}
