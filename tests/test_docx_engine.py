"""Behavioral coverage for exact edits and preservation, using small OOXML fixtures."""

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree

from markupdoc.docx_engine import apply_docx, inspect_docx


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


class DocxEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / "original.docx"
        self.output = self.root / "output.docx"

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, body, parts=None):
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>').encode()
        entries = {"word/document.xml": document, "word/styles.xml": b"untouched styles bytes",
                   "word/media/image1.png": b"unchanged image bytes", "[Content_Types].xml": b"<Types/>"}
        entries.update(parts or {})
        with zipfile.ZipFile(self.original, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b"preserve archive comment"
            for name, value in entries.items():
                archive.writestr(name, value)
        self.source_bytes = self.original.read_bytes()
        return inspect_docx(self.original)

    def edit(self, **overrides):
        edit = {"id": "edit-1", "block_id": "word/document.xml:p000001", "operation": "replace",
                "old_text": "cat", "new_text": "dog", "context_before": "", "context_after": "",
                "page": 1, "reason": "handwritten replacement", "needs_review": False}
        edit.update(overrides)
        return edit

    def plan(self, *edits):
        return {"schema_version": 1, "source_sha256": hashlib.sha256(self.original.read_bytes()).hexdigest(),
                "edits": list(edits), "warnings": []}

    def apply(self, *edits):
        report = apply_docx(self.original, self.plan(*edits), self.output)
        self.assertEqual(self.original.read_bytes(), self.source_bytes, "Original must be untouched")
        return report

    def xml(self, part="word/document.xml"):
        with zipfile.ZipFile(self.output) as archive:
            return archive.read(part)

    def text(self):
        return [block["text"] for block in inspect_docx(self.output)["blocks"]]

    def test_exact_replacement_preserves_every_other_xml_byte_and_zip_member(self):
        self.fixture('<w:p w:rsidR="AABB"><w:r><w:rPr><w:b/></w:rPr><w:t>The cat.</w:t></w:r></w:p>')
        report = self.apply(self.edit())
        self.assertEqual(report["summary"], {"proposed": 1, "applied": 1, "needs_review": 0})
        with zipfile.ZipFile(self.original) as before, zipfile.ZipFile(self.output) as after:
            self.assertEqual(before.comment, after.comment)
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                expected = before.read(name).replace(b"The cat.", b"The dog.") if name == "word/document.xml" else before.read(name)
                self.assertEqual(after.read(name), expected)
                self.assertEqual(before.getinfo(name).date_time, after.getinfo(name).date_time)
                self.assertEqual(before.getinfo(name).compress_type, after.getinfo(name).compress_type)

    def test_repeated_phrase_is_reviewed_not_guessed(self):
        self.fixture('<w:p><w:r><w:t>The cat watched the cat.</w:t></w:r></w:p>')
        report = self.apply(self.edit())
        self.assertEqual(report["summary"]["applied"], 0)
        self.assertIn("Ambiguous", report["results"][0]["reason"])
        self.assertEqual(self.output.read_bytes(), self.source_bytes)

    def test_immediate_context_selects_one_of_repeated_phrases(self):
        self.fixture('<w:p><w:r><w:t>The cat watched the cat.</w:t></w:r></w:p>')
        self.apply(self.edit(context_before="watched the ", context_after="."))
        self.assertEqual(self.text(), ["The cat watched the dog."])

    def test_context_must_be_adjacent(self):
        self.fixture('<w:p><w:r><w:t>The cat watched.</w:t></w:r></w:p>')
        report = self.apply(self.edit(context_before="The"))
        self.assertEqual(report["summary"]["applied"], 0)
        self.assertIn("No exact match", report["results"][0]["reason"])

    def test_cross_run_replacement_keeps_styles_and_untouched_suffix(self):
        self.fixture('<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>The c</w:t></w:r>'
                     '<w:r><w:rPr><w:i/></w:rPr><w:t>at is here.</w:t></w:r></w:p>')
        self.apply(self.edit(new_text="elephant"))
        root = etree.fromstring(self.xml())
        nodes = root.findall(".//w:t", NS)
        self.assertEqual([n.text for n in nodes], ["The elephant", " is here."])
        self.assertIsNotNone(root.find(".//w:rPr/w:b", NS))
        self.assertIsNotNone(root.find(".//w:rPr/w:i", NS))
        self.assertEqual(nodes[1].get("{http://www.w3.org/XML/1998/namespace}space"), "preserve")

    def test_multiple_original_coordinate_edits_in_one_run(self):
        self.fixture('<w:p><w:r><w:t>cat and bird and cat</w:t></w:r></w:p>')
        report = self.apply(self.edit(context_before="", context_after=" and", new_text="elephant"),
                            self.edit(id="two", old_text="bird", new_text="owl"),
                            self.edit(id="three", context_before="bird and ", new_text="fox"))
        self.assertEqual(report["summary"]["applied"], 3)
        self.assertEqual(self.text(), ["elephant and owl and fox"])

    def test_table_paragraphs_have_stable_distinct_ids(self):
        inspected = self.fixture('<w:p><w:r><w:t>heading</w:t></w:r></w:p>'
                                 '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cat</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        self.assertEqual([b["id"] for b in inspected["blocks"]],
                         ["word/document.xml:p000001", "word/document.xml:p000002"])
        self.apply(self.edit(block_id="word/document.xml:p000002"))
        self.assertEqual(self.text(), ["heading", "dog"])

    def test_insert_and_delete_using_original_anchors(self):
        self.fixture('<w:p><w:r><w:t>cat sleeps</w:t></w:r></w:p>')
        report = self.apply(self.edit(operation="insert", old_text="", new_text="The ", context_after="cat"),
                            self.edit(id="delete", operation="delete", old_text="sleeps", new_text="", context_before="cat "))
        self.assertEqual(report["summary"]["applied"], 2)
        self.assertEqual(self.text(), ["The cat "])
        self.assertIn(b'xml:space="preserve"', self.xml())

    def test_insertion_without_anchor_and_ambiguous_anchor_require_review(self):
        self.fixture('<w:p><w:r><w:t>cat cat</w:t></w:r></w:p>')
        report = self.apply(self.edit(operation="insert", old_text="", new_text="big "),
                            self.edit(id="two", operation="insert", old_text="", new_text="big ", context_after="cat"))
        self.assertEqual(report["summary"]["needs_review"], 2)

    def test_hash_mismatch_does_not_create_output(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        plan = self.plan(self.edit())
        plan["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            apply_docx(self.original, plan, self.output)
        self.assertFalse(self.output.exists())

    def test_overlapping_edits_all_require_review_but_independent_edit_applies(self):
        self.fixture('<w:p><w:r><w:t>The cat and bird</w:t></w:r></w:p>')
        report = self.apply(self.edit(), self.edit(id="two", old_text="The cat", new_text="A horse"),
                            self.edit(id="three", old_text="bird", new_text="owl"))
        self.assertEqual(report["summary"], {"proposed": 3, "applied": 1, "needs_review": 2})
        self.assertEqual(self.text(), ["The cat and owl"])

    def test_insert_at_replacement_boundary_requires_review(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        report = self.apply(self.edit(), self.edit(id="two", operation="insert", old_text="", new_text="big ", context_after="cat"))
        self.assertEqual(report["summary"]["needs_review"], 2)

    def test_duplicate_ids_cannot_apply(self):
        self.fixture('<w:p><w:r><w:t>cat and bird</w:t></w:r></w:p>')
        report = self.apply(self.edit(), self.edit(old_text="bird", new_text="owl"))
        self.assertEqual(report["summary"]["needs_review"], 2)
        self.assertTrue(all("unique" in result["reason"] for result in report["results"]))

    def test_fields_hyperlinks_controls_tracked_changes_and_drawings_are_blocked(self):
        wrappers = [
            '<w:fldSimple w:instr="DATE"><w:r><w:t>cat</w:t></w:r></w:fldSimple>',
            '<w:hyperlink><w:r><w:t>cat</w:t></w:r></w:hyperlink>',
            '<w:sdt><w:sdtContent><w:r><w:t>cat</w:t></w:r></w:sdtContent></w:sdt>',
            '<w:ins><w:r><w:t>cat</w:t></w:r></w:ins>',
            '<w:r><w:t>cat</w:t><w:drawing/></w:r>',
        ]
        inspected = self.fixture("".join("<w:p>" + inner + "</w:p>" for inner in wrappers))
        self.assertTrue(all(not block["editable"] for block in inspected["blocks"]))
        report = self.apply(*(self.edit(id=str(i), block_id=block["id"]) for i, block in enumerate(inspected["blocks"])))
        self.assertEqual(report["summary"]["needs_review"], len(wrappers))

    def test_complex_field_spanning_paragraphs_blocks_result_paragraph(self):
        inspected = self.fixture('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>'
                                 '<w:p><w:r><w:t>cat</w:t></w:r></w:p>'
                                 '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        self.assertFalse(inspected["blocks"][1]["editable"])
        report = self.apply(self.edit(block_id="word/document.xml:p000002"))
        self.assertEqual(report["summary"]["applied"], 0)

    def test_nested_textbox_text_not_duplicated_and_not_editable(self):
        inspected = self.fixture('<w:p><w:r><w:t>outside</w:t><w:pict><w:txbxContent>'
                                 '<w:p><w:r><w:t>cat</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>')
        self.assertEqual([block["text"] for block in inspected["blocks"]], ["outside", "cat"])
        self.assertTrue(all(not block["editable"] for block in inspected["blocks"]))

    def test_header_and_footnote_paragraphs_inspected_and_editable(self):
        header = f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>cat</w:t></w:r></w:p></w:hdr>'.encode()
        footnote = f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="1"><w:p><w:r><w:t>note</w:t></w:r></w:p></w:footnote></w:footnotes>'.encode()
        inspected = self.fixture('<w:p><w:r><w:t>body</w:t></w:r></w:p>',
                                 {"word/header1.xml": header, "word/footnotes.xml": footnote})
        self.assertEqual(len(inspected["blocks"]), 3)
        self.apply(self.edit(block_id="word/header1.xml:p000001"))
        self.assertIn(b">dog<", self.xml("word/header1.xml"))
        self.assertEqual(self.xml("word/footnotes.xml"), footnote)

    def test_invalid_shapes_and_uncertainty_do_not_change_document(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        invalid = [None, {"id": "incomplete"}, self.edit(id="nl", new_text="a\nb"),
                   self.edit(id="same", new_text="cat"), self.edit(id="uncertain", needs_review=True),
                   self.edit(id="page", page=True), self.edit(id="format", operation="format"),
                   self.edit(id="surrogate", new_text="\ud800")]
        report = self.apply(*invalid)
        self.assertEqual(report["summary"]["needs_review"], len(invalid))
        self.assertEqual(self.output.read_bytes(), self.source_bytes)

    def test_cannot_edit_source_or_overwrite_existing_output(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        with self.assertRaisesRegex(ValueError, "different"):
            apply_docx(self.original, self.plan(self.edit()), self.original)
        self.output.write_bytes(b"important existing file")
        with self.assertRaises(FileExistsError):
            apply_docx(self.original, self.plan(self.edit()), self.output)
        self.assertEqual(self.output.read_bytes(), b"important existing file")

    def test_entity_and_non_ascii_replacements_round_trip(self):
        self.fixture('<w:p><w:r><w:t>cat &amp; mouse</w:t></w:r></w:p>')
        self.apply(self.edit(new_text="caf\u00e9 <dog> & fox"))
        self.assertEqual(self.text(), ["caf\u00e9 <dog> & fox & mouse"])

    def test_edit_cannot_target_text_introduced_by_another_edit(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        report = self.apply(self.edit(), self.edit(id="two", old_text="dog", new_text="fox"))
        self.assertEqual(report["summary"], {"proposed": 2, "applied": 1, "needs_review": 1})
        self.assertEqual(self.text(), ["dog"])

    def test_multiple_cross_run_edits_use_original_offsets(self):
        self.fixture('<w:p><w:r><w:t>abc</w:t></w:r><w:r><w:t>def</w:t></w:r>'
                     '<w:r><w:t>ghi</w:t></w:r></w:p>')
        self.apply(self.edit(old_text="bcde", new_text="X"), self.edit(id="two", old_text="ghi", new_text="YZ"))
        self.assertEqual(self.text(), ["aXfYZ"])

    def test_trailing_space_promotes_existing_xml_space_default(self):
        self.fixture('<w:p><w:r><w:t xml:space="default">cat!</w:t></w:r></w:p>')
        self.apply(self.edit(old_text="!", new_text=" "))
        self.assertIn(b'xml:space="preserve"', self.xml())
        self.assertEqual(self.text(), ["cat "])

    def test_tracked_table_row_is_not_editable(self):
        inspected = self.fixture('<w:tbl><w:tr><w:trPr><w:ins w:id="1"/></w:trPr>'
                                 '<w:tc><w:p><w:r><w:t>cat</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        self.assertFalse(inspected["blocks"][0]["editable"])
        self.assertIn("tracked changes", inspected["blocks"][0]["reason"])

    def test_ascii_declared_xml_accepts_unicode_replacement(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        with zipfile.ZipFile(self.original) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["word/document.xml"] = entries["word/document.xml"].replace(b'encoding="UTF-8"', b'encoding="US-ASCII"')
        with zipfile.ZipFile(self.original, "w") as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        self.source_bytes = self.original.read_bytes()
        self.apply(self.edit(new_text="\u732b"))
        self.assertEqual(self.text(), ["\u732b"])

    def test_non_docx_input_and_output_extensions_are_rejected(self):
        self.fixture('<w:p><w:r><w:t>cat</w:t></w:r></w:p>')
        macro_path = self.root / "original.docm"
        macro_path.write_bytes(self.source_bytes)
        with self.assertRaisesRegex(ValueError, "Input must have a .docx"):
            inspect_docx(macro_path)
        with self.assertRaisesRegex(ValueError, "Input must have a .docx"):
            apply_docx(macro_path, self.plan(self.edit()), self.output)
        with self.assertRaisesRegex(ValueError, "Output must have a .docx"):
            apply_docx(self.original, self.plan(self.edit()), self.root / "output.docm")
        self.assertFalse(self.output.exists())

    def test_note_comment_and_bookmark_anchor_paragraphs_require_review(self):
        anchor_paragraphs = [
            '<w:r><w:footnoteReference w:id="1"/><w:t>cat</w:t></w:r>',
            '<w:r><w:footnoteRef/><w:t>cat</w:t></w:r>',
            '<w:r><w:endnoteReference w:id="1"/><w:t>cat</w:t></w:r>',
            '<w:r><w:endnoteRef/><w:t>cat</w:t></w:r>',
            '<w:commentRangeStart w:id="1"/><w:r><w:t>cat</w:t></w:r><w:commentRangeEnd w:id="1"/>',
            '<w:r><w:commentReference w:id="1"/><w:t>cat</w:t></w:r>',
            '<w:bookmarkStart w:id="1" w:name="reference"/><w:r><w:t>cat</w:t></w:r><w:bookmarkEnd w:id="1"/>',
        ]
        inspected = self.fixture("".join("<w:p>" + text + "</w:p>" for text in anchor_paragraphs))
        self.assertTrue(all(not block["editable"] for block in inspected["blocks"]))
        report = self.apply(*(self.edit(id=str(i), block_id=block["id"])
                              for i, block in enumerate(inspected["blocks"])))
        self.assertEqual(report["summary"]["needs_review"], len(anchor_paragraphs))
        self.assertEqual(self.output.read_bytes(), self.source_bytes)


if __name__ == "__main__":
    unittest.main()
