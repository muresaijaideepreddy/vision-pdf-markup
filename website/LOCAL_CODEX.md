# Local Codex comparison

The local website can use `gpt-6-astra` through the installed Codex CLI while retaining the lab Ollama choices. This is a local experiment using the CLI's existing sign-in, not a public OpenAI Responses API connection. Inference runs on OpenAI servers and uses the signed-in Codex account. The hosted website cannot access this adapter.

Use Node.js 22.18 or later. Verify `codex login status`, then configure the ignored `website/.dev.vars` file with `CODEX_BRIDGE_URL=http://127.0.0.1:5174`, a securely generated random `CODEX_BRIDGE_TOKEN` of at least 32 characters, and optionally `CODEX_CLI_PATH` if the executable is not on PATH. The existing Ollama settings can remain. Never commit this file or copy a Codex login token into it. The bridge token is a separate local application secret.

Start `npm run codex:bridge` in one terminal and `npm run dev` in another, both inside `website`. Restart the preview after changing `.dev.vars`. The model selector defaults to Codex when the authenticated local health check succeeds. If the bridge is offline, the configured Ollama choices remain available. Both local processes must stay running while testing.

Upload the original DOCX and marked PDF, select 1–3 pages, and analyze. The bridge receives original paragraph text and rendered selected pages. The human-edited reference remains outside the model input. Each request uses a new read-only, ephemeral Codex invocation with shell, browser, plugin and related tools disabled. The server uses a fixed model and executable, accepts no user-provided commands, binds only to `127.0.0.1:5174`, authenticates server calls, rejects browser origins, and allows one request at a time. Temporary page images and results are deleted after the invocation ends. Do not expose this service through a public host or tunnel.

Review every proposal before selecting it for export. A successful request or a valid target does not establish handwriting accuracy. Model request time includes the local adapter, CLI startup, network, inference and validation. The model name in a Codex run comes from the CLI startup report; `returned_model` remains null because this interface does not supply an API `response.model`. Token usage and pure inference duration are not inferred.

Select the reviewed edits and click **Create updated files**. The results section provides an updated DOCX download, body-text preview, PDF download, and a paginated PDF preview. Output is invalidated when the documents or selected edits change. The rest of the document is preserved; the interface states which scan pages were analyzed.

PDF conversion is available locally on Windows with Microsoft Word installed. The same authenticated local adapter renders the exact updated DOCX with Word's `ExportAsFixedFormat`; this step does not call a model or upload the document to another service. The converter opens its temporary copy read-only in a hidden Word instance, disables macros and automatic link updates, and removes temporary job data afterward. Conversion has a two-minute timeout. The Word download remains available if PDF conversion fails. The Vite watcher excludes temporary job directories because Word may lock temporary files.

The hosted website has no access to desktop Word and reports PDF export as unavailable. A hosted PDF conversion service would need a separate implementation. Current output contains selected text edits only, without Track Changes; a successful export does not establish that all handwritten marks were recovered.

For a later hosted integration, use an independently configured provider API key and an appropriate API adapter. Do not extract or repurpose the Codex sign-in credentials.

References: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [authentication](https://learn.chatgpt.com/docs/auth).
