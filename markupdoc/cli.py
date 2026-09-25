"""Command-line workflow with explicit propose, apply, and evaluate stages."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .docx_engine import apply_docx, inspect_docx
from .pdf_input import inspect_pdf, render_pdf_pages
from .schema import validate_plan


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def fresh_directory(path: str | Path) -> Path:
    destination = Path(path)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError(f"Output folder is not empty: {destination}. Choose a new folder to preserve earlier results.")
    return destination


def _original(parser):
    parser.add_argument("--original", type=Path, required=True, help="Original .docx (never overwritten)")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="markupdoc", description="Experimental scanned handwritten markup to Word edits.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="List the original's text blocks and editing limitations locally")
    _original(p)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("propose", help="Send original text and marked-up scan to the configured vision model")
    _original(p)
    p.add_argument("--scan", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default=os.environ.get("MARKUPDOC_MODEL", ""))
    p.add_argument("--api", choices=["responses", "chat-completions"], default="responses")
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    p.add_argument("--guide", type=Path, help="Optional plain-text markup conventions; never the test's final Word file")
    p.add_argument("--max-pages", type=int, default=30)
    p.add_argument("--dpi", type=int, default=170)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--max-output-tokens", type=int, default=12000)
    p = sub.add_parser("apply", help="Apply exact, unambiguous proposed edits locally to a new Word copy")
    _original(p)
    p.add_argument("--edits", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("evaluate", help="Compare predicted Word with the human reference locally after generation")
    _original(p)
    p.add_argument("--predicted", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("demo", help="Run a synthetic example with a supplied edit list; no API or AI recognition")
    p.add_argument("--out", type=Path, default=Path("runs/demo"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run_demo
            destination = fresh_directory(args.out)
            run_demo(destination)
            print(f"Offline demonstration completed: {destination.resolve() / 'review.html'}")
            print("The edit list was supplied by the demo. This is not a handwriting accuracy result.")
        elif args.command == "inspect":
            snapshot = inspect_docx(args.original)
            destination = fresh_directory(args.out)
            write_json(destination / "original-blocks.json", snapshot)
            print(f"Inspected {len(snapshot['blocks'])} text blocks. See {destination.resolve() / 'original-blocks.json'}")
        elif args.command == "propose":
            from .review import render_review
            from .vision import propose
            if not args.model.strip():
                raise ValueError("Specify --model or set MARKUPDOC_MODEL to a vision-capable model available to your account.")
            if args.timeout < 1:
                raise ValueError("timeout must be positive.")
            snapshot = inspect_docx(args.original)
            snapshot["source"] = str(args.original.resolve())
            pdf_info = inspect_pdf(args.scan, max_pages=args.max_pages)
            guide = args.guide.read_text(encoding="utf-8-sig") if args.guide else ""
            destination = fresh_directory(args.out)
            write_json(destination / "original-blocks.json", snapshot)
            images = render_pdf_pages(args.scan, destination / "scan-pages", dpi=args.dpi, max_pages=args.max_pages)
            print(f"Sending original text and {pdf_info['pages']} scan pages to model {args.model} ({args.api}).", flush=True)
            plan = propose(snapshot, args.scan, model=args.model, pdf_info=pdf_info, api=args.api, base_url=args.base_url,
                           guide=guide, page_images=images, timeout=args.timeout, max_output_tokens=args.max_output_tokens)
            plan["metadata"]["preview_dir"] = "scan-pages"
            validate_plan(plan)
            write_json(destination / "edits.json", plan)
            write_text(destination / "review.html", render_review(snapshot, plan))
            print(f"Proposed {len(plan['edits'])} edits. Review {destination.resolve() / 'review.html'}")
            print("The original Word file is unchanged. Use apply to create an edited copy.")
        elif args.command == "apply":
            from .review import render_review
            plan = validate_plan(json.loads(args.edits.read_text(encoding="utf-8-sig")))
            snapshot = inspect_docx(args.original)
            snapshot["source"] = str(args.original.resolve())
            if plan["source_sha256"] != snapshot["sha256"]:
                raise ValueError("Edit plan belongs to a different original document.")
            destination = fresh_directory(args.out)
            report = apply_docx(args.original, plan, destination / "edited.docx")
            write_json(destination / "application-report.json", report)
            write_json(destination / "edits.json", plan)
            # Keep a self-contained report, with only expected local page previews.
            preview_plan = json.loads(json.dumps(plan))
            preview = preview_plan.get("metadata", {}).get("preview_dir")
            count = preview_plan.get("metadata", {}).get("scan_pages")
            if preview == "scan-pages" and type(count) is int and 0 < count <= 1000:
                page_names = [f"page-{i:03d}.png" for i in range(1, count + 1)]
                files = [args.edits.parent / "scan-pages" / name for name in page_names]
                if all(path.is_file() for path in files):
                    (destination / "scan-pages").mkdir()
                    for path in files:
                        shutil.copyfile(path, destination / "scan-pages" / path.name)
                else:
                    preview_plan["metadata"].pop("preview_dir", None)
                    preview_plan["warnings"].append("Scan previews were not found; open the original marked-up PDF to review edits.")
            write_text(destination / "review.html", render_review(snapshot, preview_plan, report))
            print(json.dumps(report["summary"], indent=2))
            print(f"Edited copy: {destination.resolve() / 'edited.docx'}")
            print(f"Review report: {destination.resolve() / 'review.html'}")
            print("Applied means exact text targeting passed; it does not verify handwriting interpretation. Inspect the Word layout.")
        elif args.command == "evaluate":
            from .evaluation import evaluate_documents, write_evaluation
            result = evaluate_documents(args.original, args.predicted, args.reference)
            destination = fresh_directory(args.out)
            write_evaluation(result, destination)
            print(f"Comparison saved in {destination.resolve()}")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
