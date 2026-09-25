from markupdoc.review import render_review


def _inputs():
    snapshot = {"sha256": "abc", "path": "original.docx", "warnings": [], "blocks": [
        {"id": "p1", "text": "The cat was here.", "editable": True, "part": "word/document.xml"}
    ]}
    plan = {"source_sha256": "abc", "warnings": [], "edits": [{
        "id": "e1", "block_id": "p1", "page": 3, "operation": "replace",
        "old_text": "cat", "new_text": "dog", "context_before": "The ",
        "context_after": " was here.", "reason": "Handwritten replacement.", "needs_review": False,
    }]}
    return snapshot, plan


def test_review_contains_exact_anchors_and_original_text():
    snapshot, plan = _inputs()
    html = render_review(snapshot, plan)
    assert "PDF page 3" in html
    assert "<pre>cat</pre>" in html
    assert "<pre>dog</pre>" in html
    assert "The cat was here." in html
    assert "<pre>The </pre>" in html
    assert "<pre> was here.</pre>" in html
    assert "Preview only" in html
    assert "Formatting has not been visually verified" in html


def test_document_and_model_strings_are_escaped_in_html():
    snapshot, plan = _inputs()
    attack = '<script>alert("x")</script>'
    snapshot["path"] = attack
    snapshot["blocks"][0]["text"] = attack
    plan["edits"][0]["id"] = attack
    plan["edits"][0]["new_text"] = attack
    plan["warnings"] = [attack]
    html = render_review(snapshot, plan)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&quot;x&quot;" in html
    assert "Content-Security-Policy" in html


def test_engine_outcome_takes_precedence_over_proposed_status():
    snapshot, plan = _inputs()
    report = {"results": [{"id": "e1", "status": "needs_review", "reason": "Multiple matches found."}],
              "summary": {"proposed": 1, "applied": 0, "needs_review": 1}}
    html = render_review(snapshot, plan, report)
    assert "needs review" in html
    assert "Multiple matches found." in html
    assert "<strong>1</strong>Need review" in html
    assert "Preview only" not in html


def test_hash_mismatch_and_empty_plan_do_not_suggest_success():
    snapshot, plan = _inputs()
    plan["source_sha256"] = "wrong"
    plan["edits"] = []
    html = render_review(snapshot, plan)
    assert "does not match the inspected original" in html
    assert "This does not confirm that the scan has no corrections" in html


def test_unlocated_mark_displays_review_and_no_fabricated_original():
    snapshot, plan = _inputs()
    plan["edits"][0].update(block_id="", needs_review=True)
    html = render_review(snapshot, plan)
    assert "needs review" in html
    assert "Block not found in original snapshot." in html
    assert "<strong>1</strong>Need review" in html


def test_offline_demo_warning_and_relative_scan_preview():
    snapshot, plan = _inputs()
    plan["metadata"] = {"mode": "offline_demo", "scan_pages": 3, "preview_dir": "scan-pages"}
    html = render_review(snapshot, plan)
    assert "Offline demonstration — supplied edit list; no handwriting recognition measured" in html
    assert 'src="scan-pages/page-003.png"' in html
    assert 'href="scan-pages/page-003.png"' in html


def test_untrusted_preview_directory_does_not_create_links():
    snapshot, plan = _inputs()
    plan["metadata"] = {"scan_pages": 3, "preview_dir": 'javascript:alert("x")'}
    html = render_review(snapshot, plan)
    assert "javascript:" not in html
    assert "<img" not in html


def test_combined_warnings_are_deduplicated_in_first_seen_order():
    snapshot, plan = _inputs()
    snapshot["warnings"] = ["Source warning."]
    plan["warnings"] = ["Plan warning."]
    report = {"results": [], "warnings": ["Source warning.", "Plan warning.", "Application warning."]}
    html = render_review(snapshot, plan, report)
    assert html.count("Source warning.") == 1
    assert html.count("Plan warning.") == 1
    assert html.index("Source warning.") < html.index("Plan warning.") < html.index("Application warning.")
