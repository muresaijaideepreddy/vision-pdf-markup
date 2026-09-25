# Scanned markup to Word experiment

## Browser workspace

The `website/` directory contains the current review interface, PDF page renderer, conservative DOCX editing engine, and lab-hosted Ollama connector. See [website setup and workflow](website/README.md). Copy `website/.dev.vars.example` to `website/.dev.vars` and set your server URL locally before starting the website.

The working default is `qwen3-vl:4b-instruct`. The provided larger `qwen3.6:35b-a3b` model worked for text but returned image-runner errors during testing. A completed website test took 6.796 seconds total; it found only one of four clear synthetic text corrections. This is an experimental review tool, not validated unattended editing.

Private CI source documents, scanned markups, human-edited references, handbooks, endpoint configuration, model credentials, generated experiment runs, and local dependencies are excluded from this repository. The included website demo documents are synthetic.

The Python CLI below is an earlier API-adapter prototype. For the tested lab Ollama connection, use the website.


This prototype turns a marked-up PDF and its original Word document into an explicit edit list, an edited Word copy, and review reports. It is a starting point for Jaideep's experiment. Real handwriting accuracy has **not** been measured yet.

The workflow is:

**Original DOCX + marked-up PDF → vision model → proposed edits → exact text checks → edited DOCX → comparison with human reference**

The model interprets the handwriting and locates it within named Word paragraphs. Local code applies the edits. The human-edited reference is used only during evaluation, never during a test prediction.

## Try the offline demonstration

Requires Python 3.10 or later. In this project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[demo,test]"
.\.venv\Scripts\python.exe -m markupdoc demo --out runs/demo-new
```

There is already a completed demonstration in `runs/demo/`. Open `runs/demo/review.html` locally to see its edit list and scan previews. The demo applies four supplied edits (replacement, insertion, deletion, and table text replacement), and leaves one ambiguous note for review. It includes an original, a raster-only synthetic scan, an expected Word reference, and an edited copy.

**The demonstration uses a hand-authored edit list. No AI model is called. It does not test handwriting recognition.** Its marks are simulated typeset annotations, not the supervisor's handwriting. Its reference retains the unresolved note's original value. Word layout still needs visual inspection.

In this Codex workspace the bundled Python already has the dependencies. Without installing anything, it can be invoked as:

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m markupdoc --help
```

Replace `python` in the commands below with your virtual environment or bundled Python executable as needed. Each command writes to a new or empty output directory. Existing files are never overwritten.

## Run a real example

Put the matching triplet together:

```text
data/
  sample_001/
    original.docx
    marked.pdf
    human_final.docx
```

Use `.docx`, not the older `.doc` format or macro-enabled `.docm`. Verify that the scan came from this exact original. The local fingerprint prevents accidentally applying an edit list to a different original.

### 1. Inspect the original locally

```powershell
python -m markupdoc inspect --original data/sample_001/original.docx --out runs/sample_001-inspect
```

`original-blocks.json` lists body and table paragraphs, headers, footers, footnotes, and endnotes, with stable block IDs. It flags paragraphs containing structures the prototype cannot edit safely.

### 2. Configure API access

Set `OPENAI_API_KEY` locally using your normal secret-management process. Set the model to a vision-capable model actually enabled for your account:

```powershell
$env:MARKUPDOC_MODEL = "YOUR_AVAILABLE_VISION_MODEL"
```

`.env.example` documents the variable names; this program does not automatically load `.env` files. Do not commit real keys. No model is silently selected, and no API request is made by `demo`, `inspect`, `apply`, or `evaluate`.

The default is OpenAI's Responses endpoint, with the marked-up PDF and extracted original Word text. For another compatible service, set `OPENAI_BASE_URL` to its documented API base URL. The alternative `--api chat-completions` sends the rendered pages as images. That service must support image inputs, strict JSON-schema output, and `max_completion_tokens`. API compatibility is not assumed or automatically downgraded. Local HTTP endpoints on localhost are allowed without a key; other endpoints require HTTPS and a key.

### 3. Ask the model for edits

```powershell
python -m markupdoc propose --original data/sample_001/original.docx --scan data/sample_001/marked.pdf --out runs/sample_001-proposals
```

This is the only step that sends document content to the configured provider. It sends the original's extracted text plus the scan, not the human final. Use the lab's intended provider and approved documents.

Optional: add `--guide markup-guide.txt` with Daniel and Chelsea's markup conventions. Do not put a held-out sample's final answer in that guide. `--max-pages`, `--dpi`, and `--max-output-tokens` control scan limits, preview resolution, and response capacity. The default scan limit is 30 pages; larger documents fail rather than having pages silently omitted. The first version processes the entire original and scan in one request. It does not yet automatically locate and re-read difficult crops.

Outputs include `edits.json`, `original-blocks.json`, rendered `scan-pages/`, and `review.html`. The edit list records the model, prompt version, document/scan hashes, API usage, request duration, and available provider IDs. Cost is not estimated without the actual model's pricing.

### 4. Apply the edit list locally

```powershell
python -m markupdoc apply --original data/sample_001/original.docx --edits runs/sample_001-proposals/edits.json --out runs/sample_001-output
```

Review `review.html` beside the scan. This command creates `edited.docx` and `application-report.json`. Each operation is either `applied` or `needs_review`. A status of applied means exact text targeting passed, **not** that the handwritten instruction was correctly interpreted. Independently valid edits can apply while others remain for review; the output can therefore be incomplete.

For a human-confirmed correction, save a separate copy of `edits.json`, fix the text/anchors, and clear `needs_review` only after verifying the mark. Apply that revised plan to the **original**, into another new folder. All coordinates refer to the original. Do not apply the same plan to an already-edited file.

The engine preserves ZIP member contents and changes only necessary Word text nodes. Unaffected runs/styles stay intact; replacement text inherits the first touched run's formatting. This is not Word Track Changes: the audit trail is in JSON and HTML. Open the Word copy to verify layout and final pagination.

### 5. Compare with the human final

```powershell
python -m markupdoc evaluate --original data/sample_001/original.docx --predicted runs/sample_001-output/edited.docx --reference data/sample_001/human_final.docx --out runs/sample_001-evaluation
```

`evaluation.json` and `assessment.md` report exact inspected text equality, block differences, original-relative character diff operations, and non-text OOXML structure checks. Diff operations are **not** the same as human markup edits: one replacement can become several character operations. The comparison does not establish visual layout fidelity or handwriting accuracy. Confirm every discrepancy against the actual scan; the human reference may contain extra unmarked corrections.

## Supported first-version scope

- Exact single-paragraph text replacements, insertions, and deletions, including plain text inside tables.
- Matching by block ID, original text, and exact adjacent context. Repeated/ambiguous text is flagged.
- Original-coordinate preflight, source fingerprint, overlap/conflict detection, and separate output copies.
- Review-only handling for unreadable marks and unsupported paragraph structures: fields, tracked revisions, hyperlinks, content controls, equations, drawings/textboxes, tabs/breaks, bookmarks, and note/comment anchors.
- No paragraph moves, table restructuring, formatting-only changes, automatic acceptance of existing tracked changes, or local-model training yet.

Some embedded/complex content is outside the inspector's text representation. A complete-looking edit list is not evidence that the model found every mark. All pilot outputs need human checking.

## Today's experiment

1. Get the markup walkthrough from Daniel/Chelsea and 6–10 matching triplets if available.
2. Use 2–3 document families for development and keep the others unseen for evaluation. Keep related versions together.
3. Run the same held-out inputs with one or two accessible frontier vision models. Keep separate result folders.
4. Count human-confirmed correct, missed, incorrect, unintended, and unresolved edits. Also record fully correct documents and human review time.
5. Report observed successes/failures, time/usage, and remaining limitations. Describe small-sample results as exploratory.

Supplying examples in a prompt does not permanently train the model. For future local fine-tuning, save human-confirmed scan crops, original block locations, intended operations, and corrected text. Choose a model after checking the lab GPU's memory, and maintain an untouched evaluation set across training rounds.

## Development and verification

```powershell
python -m pytest --basetemp tmp/test-run-new
```

Tests cover Word run/style preservation, exact matching, ambiguous and overlapping edits, inserts/deletions, unsupported structures, source protection, evaluation diagnostics, escaped HTML, both API request formats, refusal/truncation/malformed responses, and provider URL handling. They make no live API calls. Use a fresh test temp path on this workspace if OS temp access is restricted.

The demonstration scan was rendered and inspected. Automatic Word rendering was attempted but the available runtime has no LibreOffice executable, so the Word outputs have not been visually verified. The initial implementation used synthetic samples and made no live API calls. The offline test suite passed all 103 tests.

### Review of the supplied lab samples

The project now contains two real triplets in `TASK 2 ( CI )/TASK 2 ( CI )/` and three guidance files in `Workflow/`. The local review is saved in [the sample review summary](runs/lab-sample-review/review-summary.md). Four selected visual observations agree with Sample 1's reference; two compound/move observations retain unresolved details. This is not a full-document or API-model accuracy result.

The review identified necessary extensions: revision-aware reference text, genuine Word Track Changes output, paragraph moves/merges/splits, global replacements, and document-wide/page-group context. Both marked PDFs exceed the default 30-page limit. The human reference retains revisions, so the current `evaluate` command's raw extraction must not be treated as a reliable accuracy score for it. Use the review's accepted-view diagnostics while developing a revision-aware evaluator. The source documents remain unchanged.

## Source layout

| File | Purpose |
|---|---|
| `markupdoc/cli.py` | Command-line workflow |
| `markupdoc/vision.py` | Vision prompts and provider adapters |
| `markupdoc/schema.py` | Explicit edit format and response validation |
| `markupdoc/docx_engine.py` | Original inspection and conservative text patches |
| `markupdoc/pdf_input.py` | Scan validation and page rendering |
| `markupdoc/review.py` | Local HTML review report |
| `markupdoc/evaluation.py` | Reference comparisons and written diagnostics |
| `markupdoc/demo.py` | Synthetic, offline demonstration |

API implementation references: [PDF inputs](https://developers.openai.com/api/docs/guides/file-inputs), [vision inputs and limitations](https://developers.openai.com/api/docs/guides/images-vision), and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). These document API behavior, not the prototype's accuracy.
