import io
import re
import time
import unittest
import zipfile
from collections import Counter
from pathlib import Path

from hacksolve import ai
from hacksolve.parsers import ParseError, parse_docx, parse_pdf, parse_upload
from helpers import make_docx, make_pdf

MAX = 5 * 2 ** 20
LINES = ["Offline Clinic Triage Assistant", "Must work (without) internet access", "Submit by 5 December 2026"]


def code(fn, *a):
    try:
        fn(*a)
    except ParseError as e:
        return e.code
    return None


class ParserTests(unittest.TestCase):
    def test_plain_text_and_markdown(self):
        r = parse_upload("brief.md", b"# Title\nBuild a thing that helps people.", MAX)
        self.assertEqual((r["ext"], r["mime"]), (".md", "text/markdown"))
        self.assertIn("Build a thing", r["text"])
        self.assertIn("caf\u00e9", parse_upload("a.txt", "caf\u00e9 menu planning tool".encode("cp1252"), MAX)["text"])

    def test_rejects_wrong_extension_empty_oversize_and_binary(self):
        self.assertEqual(code(parse_upload, "x.exe", b"hello world hello", MAX), "unsupported_type")
        self.assertEqual(code(parse_upload, "x.txt", b"", MAX), "empty_file")
        self.assertEqual(code(parse_upload, "x.txt", b"a" * 2000, 1000), "too_large")
        self.assertEqual(code(parse_upload, "x.txt", b"MZ\x90\x00 fake exe renamed", MAX), "bad_file")
        self.assertEqual(code(parse_upload, "x.txt", b"text\x00with nul bytes", MAX), "bad_file")

    def test_magic_bytes_must_match_extension(self):
        self.assertEqual(code(parse_upload, "fake.pdf", b"Not a real PDF header at all", MAX), "bad_file")
        self.assertEqual(code(parse_upload, "fake.docx", b"%PDF-1.4 pretending", MAX), "bad_file")
        self.assertEqual(code(parse_upload, "fake.docx", b"PK\x03\x04garbage", MAX), "bad_file")

    def test_pdf_literal_text(self):
        text = parse_upload("b.pdf", make_pdf(LINES), MAX)["text"]
        for want in ("Offline Clinic Triage Assistant", "Must work (without) internet access", "5 December 2026"):
            self.assertIn(want, text)
        self.assertEqual(text.count("\n"), 2)

    def test_pdf_flate_compressed(self):
        self.assertIn("Offline Clinic Triage", parse_upload("b.pdf", make_pdf(LINES, compress=True), MAX)["text"])

    def test_pdf_hex_strings_with_tounicode_cmap(self):
        self.assertIn("HELLO HACKATHON JUDGES", parse_upload("b.pdf", make_pdf(hex_text="HELLO HACKATHON JUDGES", compress=True), MAX)["text"])

    def test_pdf_encrypted_and_image_only_are_rejected_clearly(self):
        self.assertEqual(code(parse_upload, "b.pdf", make_pdf(LINES, encrypt=True), MAX), "encrypted")
        self.assertEqual(code(parse_upload, "b.pdf", make_pdf(no_text=True), MAX), "no_text")

    def test_pdf_decompression_bomb_is_stopped(self):
        import zlib
        bomb = zlib.compress(b"BT " + b"A" * 40_000_000)
        pdf = b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + bomb + b"\nendstream\nendobj\n"
        self.assertEqual(code(parse_pdf, pdf, 1e12), "archive_limits")

    def test_docx_text_extraction(self):
        r = parse_upload("b.docx", make_docx(LINES), MAX)
        self.assertIn("Must work (without) internet access", r["text"])
        self.assertEqual(r["ext"], ".docx")

    def test_docx_zip_bomb_is_rejected_before_reading(self):
        self.assertEqual(code(parse_docx, make_docx(["x"], doc_xml="<a>" + "A" * 25_000_000 + "</a>")), "archive_limits")

    def test_docx_zip_slip_path_is_rejected(self):
        self.assertEqual(code(parse_docx, make_docx(LINES, extra={"../evil.txt": "x"})), "unsafe_archive")

    def test_docx_with_macros_is_rejected(self):
        self.assertEqual(code(parse_docx, make_docx(LINES, extra={"word/vbaProject.bin": b"\x00"})), "macros")

    def test_docx_xml_entities_and_doctype_are_rejected(self):
        evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(code(parse_docx, make_docx([], doc_xml=evil)), "unsafe_xml")

    def test_docx_missing_parts_and_malformed_xml(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "x")
        self.assertEqual(code(parse_docx, buf.getvalue()), "bad_file")
        self.assertEqual(code(parse_docx, make_docx([], doc_xml="<w:document")), "bad_file")

    def test_hidden_unicode_is_stripped_from_extracted_text(self):
        self.assertNotIn("\u202e", parse_upload("a.txt", "Build\u202e a tool\u200b for farmers".encode(), MAX)["text"])


FIX = Path(__file__).parent / "fixtures"
REF = Counter(re.findall(r"[a-z0-9]+", (FIX / "brief.md").read_text().lower()))


def score(text):
    got = Counter(re.findall(r"[a-z0-9]+", text.lower()))
    return (sum(min(REF[w], got[w]) for w in REF) / sum(REF.values()), sum(min(REF[w], got[w]) for w in got) / max(1, sum(got.values())))


class RealWorldFileTests(unittest.TestCase):
    """Files made by real generators (see tests/fixtures). Reference text: fixtures/brief.md."""

    PDFS = {"chromium.pdf": "Chrome / Google Docs style (Skia, Type0 + ToUnicode, split runs)", "chromium_objstm.pdf": "same, PDF 1.5 object streams",
            "libreoffice.pdf": "LibreOffice (TrueType subsets)", "wkhtml.pdf": "wkhtmltopdf (one glyph per run)",
            "pdftex.pdf": "pdfTeX (TJ kerning, ligatures, hyphenation)", "reportlab.pdf": "reportlab (ASCII85 + Flate)"}

    def test_pdfs_from_real_generators_extract_almost_perfectly(self):
        for name, what in self.PDFS.items():
            text = parse_upload(name, (FIX / name).read_bytes(), 5 * 2 ** 20)["text"]
            recall, precision = score(text)
            self.assertGreaterEqual(recall, 0.97, f"{name} ({what}): recall {recall:.0%}")
            self.assertGreaterEqual(precision, 0.97, f"{name} ({what}): precision {precision:.0%}")
            self.assertIn("5 December 2026", text.replace("  ", " "), name)

    def test_words_are_not_split_or_joined_by_run_boundaries(self):
        for name in ("chromium.pdf", "wkhtml.pdf", "pdftex.pdf"):
            text = parse_upload(name, (FIX / name).read_bytes(), 5 * 2 ** 20)["text"]
            for word in ("Offline", "efficient", "workflow", "identifiers", "Triage"):
                self.assertIn(word, text, f"{name}: {word}")

    def test_extracted_pdf_text_feeds_the_analyzer(self):
        for name in self.PDFS:
            spec = ai.local_extract(parse_upload(name, (FIX / name).read_bytes(), 5 * 2 ** 20)["text"])
            self.assertEqual(spec["deadline"], "2026-12-05", name)
            self.assertGreaterEqual(len(spec["requirements"]), 3, name)
            self.assertTrue(spec["judging"], name)

    def test_multi_page_pdf_keeps_page_order(self):
        text = parse_pdf((FIX / "multipage.pdf").read_bytes(), time.monotonic() + 10)
        self.assertEqual(re.findall(r"PAGE-(\w+)", text), ["ONE", "TWO", "THREE"])

    def test_scanned_image_only_pdf_gets_a_clear_message(self):
        with self.assertRaises(ParseError) as cm:
            parse_upload("s.pdf", (FIX / "scanned.pdf").read_bytes(), 5 * 2 ** 20)
        self.assertEqual(cm.exception.code, "no_text")
        self.assertIn("OCR", cm.exception.message)

    def test_docx_from_word_like_libreoffice_and_pandoc(self):
        for name in ("word_like.docx", "libreoffice.docx", "pandoc.docx"):
            recall, precision = score(parse_upload(name, (FIX / name).read_bytes(), 5 * 2 ** 20)["text"])
            self.assertGreaterEqual(recall, 0.97, name)
            self.assertGreaterEqual(precision, 0.97, name)

    def test_hindi_pdf_reads_in_logical_order_like_pdftotext(self):
        r = parse_upload("hindi.pdf", (FIX / "hindi.pdf").read_bytes(), 5 * 2 ** 20)
        self.assertEqual(r["text"], "Offline Clinic Triage\n\u0905\u0938\u094d\u092a\u0924\u093e\u0932 \u092e\u0947\u0902 \u0907\u0902\u091f\u0930\u0928\u0947\u091f \u0915\u0947 \u092c\u093f\u0928\u093e \u0915\u093e\u092e \u0915\u0930\u0928\u093e \u091a\u093e\u0939\u093f\u090f\u0964")
        self.assertEqual(len(r["warnings"]), 1)
        self.assertEqual(parse_upload("chromium.pdf", (FIX / "chromium.pdf").read_bytes(), 5 * 2 ** 20)["warnings"], [])

    def test_long_files_are_flagged_as_truncated(self):
        long_text = ("Build a tool for farmers. " * 4000).encode()
        r = parse_upload("long.txt", long_text, 5 * 2 ** 20)
        self.assertEqual((len(r["text"]), r["truncated"]), (60000, True))
        self.assertFalse(parse_upload("short.txt", b"Build a tool for farmers.", 5 * 2 ** 20)["truncated"])

    def test_utf16_text_files_from_windows_notepad_are_readable(self):
        text = "Build a tool for farmers.\nMust work offline.".encode("utf-16")
        self.assertIn("Must work offline.", parse_upload("notes.txt", text, 5 * 2 ** 20)["text"])


class TextPositioningTests(unittest.TestCase):
    """Spaces and line breaks come from page positions, not from how a generator groups text into blocks."""

    def text(self, content, **kw):
        from hacksolve.parsers import _Font, _page_text
        return _page_text(content, {b"F1": _Font()}, _Font(), **kw)

    def test_one_block_per_word_still_forms_lines_and_gaps(self):
        row = lambda x, y, w: b"BT /F1 10 Tf 1 0 0 1 %d %d Tm (%s) Tj ET " % (x, y, w)
        self.assertEqual(self.text(row(10, 100, b"foo") + row(40, 100, b"bar")), "foo bar")      # a visible gap: space
        self.assertEqual(self.text(row(10, 100, b"foo") + row(25, 100, b"bar")), "foobar")       # touching runs: no space
        self.assertEqual(self.text(row(10, 100, b"foo") + row(10, 80, b"bar")), "foo\nbar")       # next line

    def test_transform_matrices_are_applied_between_blocks(self):
        blocks = b"q 1 0 0 1 0 0 cm BT /F1 10 Tf 1 0 0 1 10 100 Tm (foo) Tj ET Q q 1 0 0 1 0 -50 cm BT /F1 10 Tf 1 0 0 1 10 100 Tm (bar) Tj ET Q"
        self.assertEqual(self.text(blocks), "foo\nbar")                                            # same Tm, different cm: another line

    def test_actual_text_replaces_glyph_decoding_once_per_span(self):
        c = b"BT /F1 10 Tf 1 0 0 1 10 100 Tm /Span <</ActualText <FEFF0041> >> BDC (xyz) Tj 5 0 Td (uvw) Tj EMC ET"
        self.assertEqual(self.text(c), "A")
        c2 = b"BT /F1 10 Tf 1 0 0 1 10 100 Tm /Span << /ActualText (Hi) >> BDC (xyz) Tj EMC ET"
        self.assertEqual(self.text(c2), "Hi")
        self.assertEqual(self.text(b"BT /F1 10 Tf 1 0 0 1 10 100 Tm /Span << /ActualText () >> BDC (watermark) Tj EMC ET"), "")


class HostileInputTests(unittest.TestCase):
    def test_pdf_operator_bomb_is_bounded_in_time_and_output(self):
        import zlib
        chunk = zlib.compress(b"BT /F1 9 Tf (a) Tj ET\n" * 330_000)
        objs = b"".join(b"%d 0 obj\n<< /Filter /FlateDecode >>\nstream\n%s\nendstream\nendobj\n" % (4 + i, chunk) for i in range(3))
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
               b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents [4 0 R 5 0 R 6 0 R] /Resources << >> >>\nendobj\n" + objs + b"trailer\n<< /Root 1 0 R >>\n")
        self.assertLess(len(pdf), 100_000)
        t0 = time.time()
        try:
            out = parse_pdf(pdf, time.monotonic() + 3)
            self.assertLess(len(out), 400_000)
        except ParseError as e:
            self.assertEqual(e.code, "timeout")
        self.assertLess(time.time() - t0, 8)
        t0 = time.time()
        with self.assertRaises(ParseError) as cm:
            parse_pdf(pdf, time.monotonic() + 0.2)
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLess(time.time() - t0, 2)

    def test_line_rejoining_is_linear_time(self):
        from hacksolve.parsers import _reflow
        t0 = time.time()
        out = _reflow("a\n" * 400_000)
        self.assertLess(time.time() - t0, 3)
        self.assertEqual(out.strip().count("\n"), 0)

    def test_reflow_joins_wrapped_lines_but_not_headings_or_bullets(self):
        from hacksolve.parsers import _reflow
        text = "Problem\nBuild a tool that helps nurses\nwork offline.\n\u2022 Must be fast\nShould export"
        self.assertEqual(_reflow(text), "Problem\nBuild a tool that helps nurses work offline.\n\u2022 Must be fast\nShould export")
        self.assertEqual(_reflow("it was decem-\nber 5", dehyphenate=True), "it was december 5")
        self.assertEqual(_reflow("a low-\nspec laptop", dehyphenate=False), "a low- spec laptop")


if __name__ == "__main__":
    unittest.main()
