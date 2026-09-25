"""Generate a standalone, escaped HTML edit review with no active content."""

from __future__ import annotations

from html import escape
from typing import Any


def _e(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _warning_list(warnings: list[Any]) -> str:
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in warnings) + "</ul>" if warnings else "<p>None reported.</p>"


def render_review(snapshot: dict[str, Any], plan: dict[str, Any],
                  report: dict[str, Any] | None = None) -> str:
    """Render proposals and application outcomes, always treating inputs as text."""
    edits = plan.get("edits", [])
    blocks = {block.get("id"): block for block in snapshot.get("blocks", [])}
    results = {item.get("id"): item for item in (report or {}).get("results", [])}
    summary = (report or {}).get("summary", {})
    applied = sum(item.get("status") == "applied" for item in results.values())
    needs_review = sum(item.get("status") == "needs_review" for item in results.values())
    if report is None:
        needs_review = sum(bool(edit.get("needs_review")) for edit in edits)
    warnings = list(snapshot.get("warnings", [])) + list(plan.get("warnings", [])) + list((report or {}).get("warnings", []))
    metadata = plan.get("metadata", {})
    scan_count = metadata.get("scan_pages")
    has_scan_previews = metadata.get("preview_dir") == "scan-pages" and type(scan_count) is int and scan_count > 0
    demo_banner = '<aside class="demo"><strong>Offline demonstration — supplied edit list; no handwriting recognition measured</strong></aside>' if metadata.get("mode") == "offline_demo" else ""
    source_hash = snapshot.get("sha256", "not recorded")
    if plan.get("source_sha256") != snapshot.get("sha256"):
        warnings.append("The plan source hash does not match the inspected original. Do not apply this plan to this document.")
    warnings = list(dict.fromkeys(warnings))
    cards = []
    for number, edit in enumerate(edits, 1):
        block = blocks.get(edit.get("block_id"), {})
        result = results.get(edit.get("id"))
        status = (result or {}).get("status") or ("needs_review" if edit.get("needs_review") else "proposed")
        status_class = "applied" if status == "applied" else "review" if status == "needs_review" else "proposed"
        outcome = (result or {}).get("reason", "No application result recorded.")
        page = edit.get("page")
        scan_preview = ""
        if has_scan_previews and type(page) is int and 1 <= page <= scan_count:
            image_path = f"scan-pages/page-{page:03d}.png"
            scan_preview = f'<figure><a href="{image_path}" target="_blank" rel="noopener"><img src="{image_path}" alt="Scanned markup, PDF page {page}" loading="lazy"></a><figcaption><a href="{image_path}" target="_blank" rel="noopener">Open scan page {page} at full size</a></figcaption></figure>'
        cards.append(f'''<article class="edit"><div class="edit-head"><h2>{number}. {_e(edit.get('id', 'Unnamed edit'))}</h2><span class="badge {status_class}">{_e(status.replace('_', ' '))}</span></div>
<p class="meta">PDF page {_e(edit.get('page', 'unspecified'))} · Block {_e(edit.get('block_id'))} · {_e(edit.get('operation'))}</p>
{scan_preview}
<div class="pair"><section><h3>Old text / exact anchor</h3><pre>{_e(edit.get('old_text'))}</pre></section><section><h3>Proposed new text</h3><pre>{_e(edit.get('new_text'))}</pre></section></div>
<details><summary>Original block and surrounding anchors</summary><h3>Original block</h3><pre>{_e(block.get('text', 'Block not found in original snapshot.'))}</pre><h3>Context before</h3><pre>{_e(edit.get('context_before'))}</pre><h3>Context after</h3><pre>{_e(edit.get('context_after'))}</pre><p class="meta">Document part: {_e(block.get('part'))} · Editable: {_e(block.get('editable', 'unknown'))}</p></details>
<p><strong>Interpretation:</strong> {_e(edit.get('reason'))}</p><p><strong>Application result:</strong> {_e(outcome)}</p></article>''')
    source = snapshot.get("source", snapshot.get("path", "See original document supplied with this report."))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; form-action 'none'"><title>Marked-up document review</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;margin:0;background:#f4f6f8;color:#182332}}main{{max-width:1040px;margin:auto;padding:32px 20px}}h1{{line-height:1.2}}h2{{font-size:20px;margin:0}}h3{{font-size:14px}}.intro,.edit,.warnings{{background:white;border:1px solid #dce1e7;border-radius:12px;padding:22px;margin-bottom:18px}}.stats{{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}}.stat{{background:#edf2f7;border-radius:8px;padding:12px 18px}}.stat strong{{font-size:24px;display:block}}.edit-head{{display:flex;justify-content:space-between;gap:16px;align-items:center}}.badge{{font-size:13px;border-radius:16px;padding:4px 12px;background:#e8edf4;white-space:nowrap}}.applied{{background:#d9efe0;color:#1d5534}}.review{{background:#fff0cf;color:#6c4900}}.meta{{font-size:13px;color:#526071;overflow-wrap:anywhere}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}pre{{font:14px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;border:1px solid #e1e5ea;border-radius:6px;padding:12px;min-height:24px}}summary{{cursor:pointer;color:#235780}}li{{margin-bottom:8px}}@media(max-width:640px){{.pair{{grid-template-columns:1fr}}.edit-head{{align-items:flex-start;flex-direction:column}}}}@media print{{body{{background:white}}main{{padding:0}}.edit{{break-inside:avoid}}details{{display:block}}}}</style>
<style>.demo{{padding:18px;border:2px solid #a76b00;background:#fff0cf;border-radius:10px;margin-bottom:20px}}figure{{margin:16px 0}}figure img{{max-width:100%;max-height:260px;border:1px solid #dce1e7}}figcaption{{font-size:13px}}</style></head>
<body><main>{demo_banner}<div class="intro"><h1>Marked-up document review</h1><p>Compare each proposed edit with the scanned markup. An applied status means the text operation passed application checks; it does not confirm that the handwriting was interpreted correctly.</p>
<p class="meta">Original source: {_e(source)}<br>Original SHA-256: {_e(source_hash)}<br>Plan SHA-256: {_e(plan.get('source_sha256'))}</p>
<div class="stats"><div class="stat"><strong>{_e(summary.get('proposed', len(edits)))}</strong>Proposed edits</div><div class="stat"><strong>{_e(summary.get('applied', applied))}</strong>Applied</div><div class="stat"><strong>{_e(summary.get('needs_review', needs_review))}</strong>Need review</div></div>
<p>{'Application results are included below.' if report is not None else 'Preview only: this report contains proposals, not confirmation of document edits.'} Formatting has not been visually verified by this report.</p></div>
<section class="warnings"><h2>Warnings</h2>{_warning_list(warnings)}</section>
{''.join(cards) if cards else '<p>No proposed edits were recorded. This does not confirm that the scan has no corrections.</p>'}
</main></body></html>'''
