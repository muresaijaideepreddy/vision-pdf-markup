# MarkupDoc CI Review Lab

A browser review workspace for original Word documents and scanned PDF corrections. It supports lab-hosted Ollama and an optional local Codex CLI connection requesting `gpt-6-astra` through the CLI's existing ChatGPT sign-in. The human-edited reference is never included in model requests.

## Use

1. Choose the original `.docx` and marked `.pdf`.
2. Select the model and 1–3 consecutive scan pages. The initial selection is page 1 only.
3. Click **Analyze selected pages**. The browser renders those pages to images; the server sends the images and original Word blocks to the selected model connection.
4. Check each proposed edit against the scan and select supported edits. Click **Create updated files**.
5. Download the updated Word copy and view its body-text preview. With the local Windows Word converter available, the same section provides the updated PDF download and a paginated PDF preview. The PDF is rendered from the updated Word file, not overlaid on the scan. If conversion fails, Word remains downloadable and PDF conversion can be retried.
6. Optionally choose the human-edited reference after creating the files. Comparison covers body/table text only and rejects tracked-change references.
7. Open **Model & timing** or download the run report for model identity, measured request/total time, available usage, processed page numbers, Word preparation time, and PDF conversion time.

Only selected edits are applied. This is a partial-page pilot: unprocessed pages remain unevaluated, and new PDF pagination may differ from the marked scan. Output is cleared when the source, proposals, or selected edits change. No full-document accuracy or training claim is made.

## Observed model results, 2026-09-25

- **Codex CLI · `gpt-6-astra`**: a website request recovered all **3 of 3 checked corrections on one real marked page**, taking **13.4 seconds total**. The CLI reported `gpt-6-astra`; this interface does not provide an API `response.model`, so `returned_model` remains null. This small check is not a document-wide accuracy score. The human-edited reference was excluded from model input.
- **`qwen3-vl:4b-instruct`**: image requests work, but the same real page returned zero proposed edits. An earlier synthetic website test found one of four clear text corrections. A successful empty response must not be interpreted as proof that the scan has no marks.
- **`qwen3.6:35b-a3b`**: text requests worked, but image requests returned HTTP 500 with `unexpected EOF`; the underlying server cause is unknown.

Ollama models run on the configured lab server. Codex inference runs on OpenAI servers and uses the signed-in account's Codex allowance. Neither connection installs or trains a model on the user's laptop.

## Configuration

Copy `.dev.vars.example` to the ignored `.dev.vars` for local preview. Production Ollama configuration uses Sites runtime variables:

- `OLLAMA_BASE_URL`: HTTPS server origin, kept server-side.
- `MARKUPDOC_MODEL`: default installed Ollama vision model.
- `MARKUPDOC_MODELS`: comma-separated permitted Ollama model identifiers.
- `OLLAMA_API_KEY`: optional server-specific bearer credential. Do not forward an unrelated provider key.

For the optional Codex connection, follow [LOCAL_CODEX.md](LOCAL_CODEX.md). Configure a separate random local bridge secret, verify `codex login status`, then run `npm run codex:bridge` and `npm run dev` in separate terminals. A healthy local adapter makes Codex the initial model selection. The existing ChatGPT sign-in is used only by the installed CLI; it is not a general-purpose API key and must not be extracted for API requests. Keep both local processes running while testing.

PDF conversion requires **Windows and installed Microsoft Word**, using the authenticated local adapter to render the updated DOCX. It does not call a vision model. The hosted site cannot reach this loopback adapter or desktop Word, so hosted PDF conversion is unavailable. A hosted API or PDF service would need its own integration.

`/api/propose` authenticates the Site user and checks request origin. The Ollama path uses native `/api/chat`, `think:false`, non-streaming structured JSON, and a 240-second timeout. The local Codex path accepts fixed image/text inputs for an isolated read-only CLI invocation. Invalid, duplicate-ID, or out-of-scope-page results produce no applicable plan. There are no automatic model retries.

## Development

Use Node 22.13 or newer for the website, or **Node 22.18 or newer for the local Codex adapter**. Run `npm run install:ci`, `npm run dev`, and `npm run build`. The PDF worker in `public/pdf.worker.min.mjs` must match the installed `pdfjs-dist` version; refresh it from `node_modules/pdfjs-dist/build/pdf.worker.min.mjs` after upgrades.

The website uses ephemeral browser state. Reloading clears selected documents and results; download the report and updated files before leaving. Private source documents, references, credentials, runtime files, and generated output are excluded from this repository.

## Editing limits

- Clean-copy text edits only. Word Track Changes, paragraph moves/merges/splits, formatting instructions, and global markup rules are not implemented.
- Exact original hash and context matching. Ambiguous targets, conflicting edits, fields spanning paragraphs, existing revisions, links, and other unsupported structures require review.
- Unchanged ZIP member contents are preserved. Unchanged ZIP compression bytes and Word pagination are not guaranteed; visually inspect the updated Word and rendered PDF.
- Word ≤20 MB; input PDF ≤8 MB; expanded Word ZIP ≤40 MB; selected scan pages 1–3 per request; source/guide ≤200,000 characters.
- The synthetic demo has four supplied text edits and one unresolved mark; it does not measure AI recognition.

The document engine previously passed 17 targeted checks covering synthetic reference matching, unselected/review edits, cross-run formatting preservation, source mismatch, overlaps, multi-paragraph fields, and malformed ZIP/XML rejection. Browser review and WebMCP actions were exercised. These checks establish implementation behavior, not handwriting accuracy.
