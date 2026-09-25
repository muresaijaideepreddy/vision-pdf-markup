"""Conservative DOCX inspection and exact, formatting-preserving text edits.

Only ordinary paragraph text can be changed. Matching is always against the
original document, never text introduced by an earlier edit. Review decisions
are data in the returned report; malformed packages/plans raise ValueError.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.parsers import expat
from xml.sax.saxutils import escape

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W}
PART_RE = re.compile(r"word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml\Z")
MAX_UNCOMPRESSED = 256 * 1024 * 1024
UNSUPPORTED = {
    "fldChar": "fields", "fldSimple": "fields", "instrText": "fields",
    "ins": "tracked changes", "del": "tracked changes", "delText": "tracked changes",
    "moveFrom": "tracked changes", "moveTo": "tracked changes",
    "moveFromRangeStart": "tracked changes", "moveToRangeStart": "tracked changes",
    "moveFromRangeEnd": "tracked changes", "moveToRangeEnd": "tracked changes",
    "pPrChange": "tracked changes", "rPrChange": "tracked changes",
    "hyperlink": "hyperlinks", "sdt": "content controls", "customXml": "custom XML containers",
    "drawing": "drawings or text boxes", "pict": "drawings or text boxes",
    "object": "embedded objects", "txbxContent": "text boxes",
    "tab": "tabs", "br": "line or page breaks", "cr": "line breaks",
    "sym": "symbol runs", "noBreakHyphen": "special hyphen elements",
    "softHyphen": "special hyphen elements", "ruby": "ruby annotations",
    "altChunk": "external content", "subDoc": "subdocuments",
    "footnoteReference": "footnote anchors", "footnoteRef": "footnote anchors",
    "endnoteReference": "endnote anchors", "endnoteRef": "endnote anchors",
    "commentReference": "comment anchors", "annotationRef": "comment anchors",
    "commentRangeStart": "comment anchors", "commentRangeEnd": "comment anchors",
    "bookmarkStart": "bookmark anchors", "bookmarkEnd": "bookmark anchors",
}


@dataclass
class _TextSpan:
    start: int
    opening_end: int
    closing_start: int
    end: int
    self_closing: bool


@dataclass
class _Block:
    info: dict
    nodes: list
    offsets: list


@dataclass
class _Package:
    raw: bytes
    sha256: str
    infos: list
    entries: dict
    blocks: dict
    roots: dict
    spans: dict
    warnings: list
    comment: bytes


def _tag_end(data: bytes, start: int) -> int:
    quote = None
    for i in range(start, len(data)):
        char = data[i]
        if quote:
            if char == quote:
                quote = None
        elif char in (34, 39):
            quote = char
        elif char == 62:
            return i + 1
    raise ValueError("Malformed XML tag")


def _text_spans(data: bytes) -> list[_TextSpan]:
    """Use XML byte positions, rather than regex, to locate w:t elements."""
    spans = []
    active = []
    parser = expat.ParserCreate(namespace_separator="|")

    def begin(name, attrs):
        if name == W + "|t":
            start = parser.CurrentByteIndex
            opening_end = _tag_end(data, start)
            active.append((start, opening_end, data[start:opening_end].rstrip().endswith(b"/>")))

    def end(name):
        if name == W + "|t":
            start, opening_end, self_closing = active.pop()
            closing_start = opening_end if self_closing else parser.CurrentByteIndex
            ending = opening_end if self_closing else _tag_end(data, closing_start)
            spans.append(_TextSpan(start, opening_end, closing_start, ending, self_closing))

    parser.StartElementHandler = begin
    parser.EndElementHandler = end
    parser.Parse(data, True)
    return spans


def _own_nodes(paragraph):
    return [n for n in paragraph.iter("{" + W + "}t")
            if next((a for a in n.iterancestors() if a.tag == "{" + W + "}p"), None) is paragraph]


def _reasons(paragraph) -> list[str]:
    reasons = set()
    for element in list(paragraph.iter()) + list(paragraph.iterancestors()):
        if not isinstance(element.tag, str):
            continue
        qname = etree.QName(element)
        if qname.namespace == W and qname.localname in UNSUPPORTED:
            reasons.add(UNSUPPORTED[qname.localname])
        if qname.namespace == W and qname.localname.endswith("Change"):
            reasons.add("tracked changes")
        if qname.namespace == "http://schemas.openxmlformats.org/officeDocument/2006/math":
            reasons.add("equations")
        if qname.namespace == "http://schemas.openxmlformats.org/markup-compatibility/2006":
            reasons.add("alternate compatibility content")
        if qname.namespace == W and qname.localname == "t" and len(element):
            reasons.add("non-text children inside text")
    # Plain runs and metadata are safe; invisible/unrecognized run content is not.
    allowed_run = {"rPr", "t", "lastRenderedPageBreak", "footnoteReference", "endnoteReference",
                   "footnoteRef", "endnoteRef", "commentReference", "annotationRef"}
    for run in paragraph.iter("{" + W + "}r"):
        for child in run:
            if isinstance(child.tag, str):
                qname = etree.QName(child)
                if qname.namespace != W or qname.localname not in allowed_run:
                    reasons.add(UNSUPPORTED.get(qname.localname, "unsupported run content"))
    # Revision metadata in a table/row/cell property applies to its paragraphs,
    # even though that metadata is a sibling of the paragraph rather than an ancestor.
    for ancestor in paragraph.iterancestors():
        if ancestor.tag in {"{" + W + "}tbl", "{" + W + "}tr", "{" + W + "}tc"}:
            for child in ancestor:
                if child.tag in {"{" + W + "}tblPr", "{" + W + "}trPr", "{" + W + "}tcPr"}:
                    for prop in child.iter():
                        if isinstance(prop.tag, str):
                            local = etree.QName(prop).localname
                            if local.endswith("Change") or local in {"ins", "del", "cellIns", "cellDel", "cellMerge"}:
                                reasons.add("tracked changes")
    return sorted(reasons)


def _load(path) -> _Package:
    path = Path(path)
    if path.suffix.lower() != ".docx":
        raise ValueError("Input must have a .docx extension; macro-enabled and template documents are unsupported")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len({info.filename for info in infos}) != len(infos):
                raise ValueError("DOCX contains duplicate ZIP member names")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
                raise ValueError("DOCX exceeds the 256 MiB uncompressed size limit")
            entries = {info.filename: archive.read(info) for info in infos}
            comment = archive.comment
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Input must be an unencrypted DOCX ZIP package") from exc
    if "word/document.xml" not in entries:
        raise ValueError("DOCX is missing word/document.xml")
    blocks, roots, spans, warnings = {}, {}, {}, []
    signed = any(name.startswith("_xmlsignatures/") for name in entries)
    if signed:
        warnings.append("Digitally signed documents require manual review; edits would invalidate signatures.")
    for part in sorted(name for name in entries if PART_RE.fullmatch(name)):
        data = entries[part]
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{part}: only UTF-8 XML is supported") from exc
        if b"\x00" in data or b"<!DOCTYPE" in data.upper():
            raise ValueError(f"{part}: UTF-16 XML and document type declarations are unsupported")
        declaration = re.search(br"<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", data[:256])
        if declaration and declaration.group(1).lower() not in (b"utf-8", b"utf8", b"us-ascii", b"ascii"):
            raise ValueError(f"{part}: only UTF-8 XML is supported")
        try:
            root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True,
                                                       remove_blank_text=False, strip_cdata=False))
            text_spans = _text_spans(data)
        except (etree.XMLSyntaxError, expat.ExpatError) as exc:
            raise ValueError(f"{part}: invalid XML") from exc
        all_nodes = list(root.iter("{" + W + "}t"))
        if len(all_nodes) != len(text_spans):
            raise ValueError(f"{part}: cannot map text nodes safely")
        roots[part] = root
        spans[part] = dict(zip(all_nodes, text_spans))
        field_depth = 0
        field_paragraphs = set()
        for element in root.iter():
            if element.tag == "{" + W + "}p" and field_depth:
                field_paragraphs.add(element)
            if element.tag == "{" + W + "}fldChar":
                paragraph = next((a for a in element.iterancestors() if a.tag == "{" + W + "}p"), None)
                if paragraph is not None:
                    field_paragraphs.add(paragraph)
                kind = element.get("{" + W + "}fldCharType")
                if kind == "begin":
                    field_depth += 1
                elif kind == "end":
                    field_depth = max(0, field_depth - 1)
        for index, paragraph in enumerate(root.iter("{" + W + "}p"), 1):
            block_id = f"{part}:p{index:06d}"
            nodes = _own_nodes(paragraph)
            text = "".join(node.text or "" for node in nodes)
            reasons = _reasons(paragraph)
            if paragraph in field_paragraphs and "fields" not in reasons:
                reasons.append("fields")
            if signed:
                reasons.append("digital signature")
            info = {"id": block_id, "text": text, "editable": not reasons,
                    "reason": "; ".join(sorted(reasons)), "part": part}
            offsets, offset = [], 0
            for node in nodes:
                length = len(node.text or "")
                offsets.append((offset, offset + length))
                offset += length
            blocks[block_id] = _Block(info, nodes, offsets)
        if root.find(".//w:altChunk", NS) is not None:
            warnings.append(f"{part}: imported external content is not exposed as editable paragraphs.")
    if not blocks:
        warnings.append("No WordprocessingML paragraphs were found.")
    blocked = sum(not block.info["editable"] for block in blocks.values())
    if blocked:
        warnings.append(f"{blocked} paragraph(s) contain unsupported structures and require review.")
    return _Package(raw, digest, infos, entries, blocks, roots, spans, warnings, comment)


def inspect_docx(path) -> dict:
    """Return stable paragraph IDs, literal text, and editing eligibility."""
    package = _load(path)
    return {"sha256": package.sha256, "blocks": [b.info for b in package.blocks.values()],
            "warnings": package.warnings}


def _invalid_text(text: str) -> bool:
    return any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in text)


def _validate_edit(edit, duplicate_ids) -> str | None:
    if not isinstance(edit, dict):
        return "Edit must be an object."
    for key in ("id", "block_id", "operation", "old_text", "new_text", "context_before", "context_after", "reason"):
        if not isinstance(edit.get(key), str):
            return f"{key} must be a string."
    if not edit["id"] or not edit["block_id"]:
        return "id and block_id must be nonempty."
    if edit["id"] in duplicate_ids:
        return "Edit IDs must be unique."
    if type(edit.get("page")) is not int or edit["page"] < 1:
        return "page must be a positive integer."
    if type(edit.get("needs_review")) is not bool:
        return "needs_review must be a boolean."
    if edit["needs_review"]:
        return "The proposed edit is marked as uncertain and requires review."
    operation = edit["operation"]
    if operation not in {"replace", "delete", "insert"}:
        return "Only text replace, delete, and insert operations are supported."
    old, new = edit["old_text"], edit["new_text"]
    if any(_invalid_text(edit[key]) for key in ("old_text", "new_text", "context_before", "context_after")):
        return "Newlines, tabs, control characters, and invalid XML characters are unsupported."
    if operation == "replace" and (not old or not new or old == new):
        return "Replacement requires distinct, nonempty old_text and new_text."
    if operation == "delete" and (not old or new):
        return "Deletion requires nonempty old_text and empty new_text."
    if operation == "insert" and (old or not new or not (edit["context_before"] or edit["context_after"])):
        return "Insertion requires empty old_text, nonempty new_text, and an immediate context anchor."
    return None


def _locations(text, edit):
    old, before, after = edit["old_text"], edit["context_before"], edit["context_after"]
    if old:
        candidates, cursor = [], 0
        while True:
            start = text.find(old, cursor)
            if start < 0:
                break
            candidates.append((start, start + len(old)))
            cursor = start + 1
    else:
        candidates = [(index, index) for index in range(len(text) + 1)]
    return [(start, end) for start, end in candidates
            if text[:start].endswith(before) and text[end:].startswith(after)]


def _overlap(a, b):
    start_a, end_a = a
    start_b, end_b = b
    if start_a == end_a:
        return start_b <= start_a <= end_b
    if start_b == end_b:
        return start_a <= start_b <= end_a
    return max(start_a, start_b) < min(end_a, end_b)


def _change(block: _Block, start: int, end: int, replacement: str):
    if start == end:
        candidates = [(i, begin, finish) for i, (begin, finish) in enumerate(block.offsets)
                      if begin <= start <= finish and finish > begin]
        if not candidates:
            raise ValueError("Insertion cannot be assigned to an existing text run")
        index, begin, finish = candidates[0]
        node = block.nodes[index]
        text = node.text or ""
        node.text = text[:start - begin] + replacement + text[start - begin:]
        return
    touched = [(i, begin, finish) for i, (begin, finish) in enumerate(block.offsets)
               if begin < end and finish > start]
    for position, (index, begin, finish) in enumerate(touched):
        node = block.nodes[index]
        text = node.text or ""
        left = max(0, start - begin)
        right = min(finish - begin, end - begin)
        node.text = text[:left] + (replacement if position == 0 else "") + text[right:]


def _node_bytes(data: bytes, span: _TextSpan, value: str) -> bytes:
    opening = data[span.start:span.opening_end]
    # Preserve pre-existing xml:space; promote to preserve for newly exposed spaces.
    if value[:1].isspace() or value[-1:].isspace():
        if re.search(br"\bxml:space\s*=", opening):
            opening = re.sub(br"(\bxml:space\s*=\s*)(['\"])[^'\"]*\2", br'\1"preserve"', opening)
        else:
            insertion = -2 if span.self_closing else -1
            opening = opening[:insertion] + b' xml:space="preserve"' + opening[insertion:]
    # Numeric references also keep an original ASCII encoding declaration valid.
    encoded = escape(value).encode("ascii", "xmlcharrefreplace")
    if span.self_closing:
        name = re.match(br"<([^\s/>]+)", opening).group(1)
        return opening[:-2] + b">" + encoded + b"</" + name + b">"
    return opening + encoded + data[span.closing_start:span.end]


def _write_new_atomic(path: Path, data: bytes):
    """Publish a completed file without replacing anything, including a raced file."""
    fd, temporary = tempfile.mkstemp(prefix=".markupdoc-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # An atomic hard link fails if the destination exists, on Windows and POSIX.
        # Both names are in the same directory/filesystem. Fail closed if unsupported.
        os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_docx(original_path, plan: dict, output_path) -> dict:
    """Apply independently safe edits to a new DOCX and report every decision.

    Conflicting edits are all sent to review. Accepted edits preserve run styles;
    replacement text inherits the first touched text run. No output is overwritten.
    """
    original, output = Path(original_path), Path(output_path)
    if output.suffix.lower() != ".docx":
        raise ValueError("Output must have a .docx extension")
    if original.resolve() == output.resolve():
        raise ValueError("Output must be different from the original document")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")
    if not output.parent.is_dir():
        raise ValueError("Output directory must already exist")
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1:
        raise ValueError("Plan schema_version must be 1")
    if not isinstance(plan.get("edits"), list):
        raise ValueError("Plan edits must be a list")
    if not isinstance(plan.get("warnings", []), list) or any(not isinstance(x, str) for x in plan.get("warnings", [])):
        raise ValueError("Plan warnings must be a list of strings")
    package = _load(original)
    if plan.get("source_sha256") != package.sha256:
        raise ValueError("Plan source_sha256 does not match the original document")
    counts = Counter(edit.get("id") for edit in plan["edits"]
                     if isinstance(edit, dict) and isinstance(edit.get("id"), str))
    duplicates = {identifier for identifier, count in counts.items() if count > 1}
    results, resolved = [], defaultdict(list)
    for index, edit in enumerate(plan["edits"]):
        result = {"id": edit.get("id") if isinstance(edit, dict) else None,
                  "status": "needs_review", "reason": "", "edit_index": index}
        results.append(result)
        issue = _validate_edit(edit, duplicates)
        if issue:
            result["reason"] = issue
            continue
        result.update({key: edit[key] for key in ("block_id", "operation", "old_text", "new_text", "page")})
        block = package.blocks.get(edit["block_id"])
        if block is None:
            result["reason"] = "block_id is absent from the original document."
            continue
        if not block.info["editable"]:
            result["reason"] = "Unsupported paragraph structure: " + block.info["reason"]
            continue
        locations = _locations(block.info["text"], edit)
        if len(locations) != 1:
            result["reason"] = ("No exact match with the supplied immediate context." if not locations
                                else "Ambiguous match; provide more immediate context.")
            continue
        start, end = locations[0]
        result.update({"start": start, "end": end})
        resolved[edit["block_id"]].append((start, end, index, edit))
    # Complete resolution/conflict preflight before mutating any XML.
    accepted = defaultdict(list)
    for block_id, candidates in resolved.items():
        conflicts = set()
        for i, candidate in enumerate(candidates):
            for other in candidates[i + 1:]:
                if _overlap(candidate[:2], other[:2]):
                    conflicts.update((candidate[2], other[2]))
        for candidate in candidates:
            if candidate[2] in conflicts:
                results[candidate[2]]["reason"] = "Overlapping edits or insertion boundary conflict."
            else:
                accepted[block_id].append(candidate)
    changed_parts = set()
    original_texts = {node: node.text or "" for root in package.roots.values()
                      for node in root.iter("{" + W + "}t")}
    for block_id, candidates in accepted.items():
        block = package.blocks[block_id]
        for start, end, index, edit in sorted(candidates, reverse=True, key=lambda item: item[:2]):
            _change(block, start, end, edit["new_text"])
            results[index].update(status="applied", reason="Exact original-text match; preflight checks passed.")
        changed_parts.add(block.info["part"])
    changed_data = {}
    for part in changed_parts:
        data = package.entries[part]
        patches = [(span, node.text or "") for node, span in package.spans[part].items()
                   if (node.text or "") != original_texts[node]]
        for span, value in sorted(patches, reverse=True, key=lambda item: item[0].start):
            data = data[:span.start] + _node_bytes(data, span, value) + data[span.end:]
        # A final parser check catches malformed patched XML before publishing.
        etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
        changed_data[part] = data
    applied = sum(result["status"] == "applied" for result in results)
    if applied:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.comment = package.comment
            for info in package.infos:
                archive.writestr(info, changed_data.get(info.filename, package.entries[info.filename]))
        output_bytes = buffer.getvalue()
    else:
        output_bytes = package.raw
    _write_new_atomic(output, output_bytes)
    return {"source_sha256": package.sha256, "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "summary": {"proposed": len(results), "applied": applied, "needs_review": len(results) - applied},
            "results": results, "warnings": package.warnings + plan.get("warnings", []),
            "output_path": str(output.resolve())}
