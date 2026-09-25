# MarkupDoc CI Review Lab

A browser review workspace for original Word documents and scanned PDF corrections. Live vision calls use the user's lab-hosted Ollama server. The reference Word file is never included in model requests.

## Use

1. Choose the original `.docx` and marked `.pdf`.
2. Select the model and 1–3 consecutive PDF pages. The initial selection is page 1 only.
3. Click **Analyze selected pages**. The browser renders the selected pages to images; the server sends those images and original Word blocks to Ollama.
4. Check each proposed edit against the scan. Select supported edits and export a new Word copy.
5. Optionally select the human-edited reference after export. Comparison covers body/table text only and rejects tracked-change references.
6. Open **Model & timing** and download the run report for requested/returned model IDs, measured HTTP/total time, server loading/prompt/generation durations, tokens, and processed page numbers.

This is a partial-page pilot. Unprocessed pages are explicitly reported. No full-document handwriting accuracy or training claim is made.

## Verified model connection, 2026-09-25

- `qwen3.6:35b-a3b`: text requests work. Native greeting 15.830 s (15.230 s model load); subsequent OpenAI-compatible greeting 0.815 s. Image requests returned HTTP 500 with `unexpected EOF`; the underlying server cause is unknown.
- `qwen3-vl:4b-instruct`: public handwriting image request succeeded in 5.240 s, including 4.316 s server model load. This is the default. The first word differed from a published example output, so this is connectivity/vision evidence, not a perfect transcription score.
- Both models are visible in the selector. These models run on the remote lab server, not this Windows laptop.

## Configuration

Ignored `.dev.vars` configures local preview. Production uses Sites runtime variables:

- `OLLAMA_BASE_URL`: HTTPS server origin; kept server-side.
- `MARKUPDOC_MODEL`: default installed vision model.
- `MARKUPDOC_MODELS`: comma-separated permitted model identifiers.
- `OLLAMA_API_KEY`: optional server-specific bearer credential; never use or forward an unrelated OpenAI key.

`/api/propose` authenticates the Site user and checks request origin. It uses Ollama native `/api/chat`, `think:false`, non-streaming structured JSON, and a 240-second timeout. Invalid, truncated, duplicate-ID, and out-of-scope-page responses produce no applicable plan. No automatic retries.

## Development

Use Node 22.13 or newer and npm. Run `npm run install:ci`, `npm run dev`, and `npm run build`. The PDF worker in `public/pdf.worker.min.mjs` must match the installed `pdfjs-dist` version; refresh it from `node_modules/pdfjs-dist/build/pdf.worker.min.mjs` after upgrades.

The live website uses ephemeral browser state. Reloading clears selected documents and results; download the report and edited file before leaving.

## Editing limits

- Clean-copy text edits only. Word Track Changes, paragraph moves/merges/splits, formatting instructions, and global markup rules are not implemented.
- Exact original hash and context matching; ambiguous targets, conflicting edits, fields spanning paragraphs, existing revisions, links, and other unsupported structures require review.
- Unchanged ZIP member contents are preserved; unchanged ZIP compression bytes and Word pagination are not guaranteed. Visually inspect the exported Word layout.
- Word ≤20 MB; PDF ≤8 MB; expanded Word ZIP ≤40 MB; selected scan pages 1–3 per request; source/guide ≤200,000 characters.
- The synthetic demo has four supplied text edits and one unresolved mark; it does not measure AI recognition.

The document engine passed 17 targeted checks, including the synthetic reference match, unselected/review edits, cross-run formatting preservation, source mismatch, overlaps, multi-paragraph fields, and malformed ZIP/XML rejection. Browser demo/export/comparison and WebMCP actions were also exercised.
