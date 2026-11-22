"""Hostile and pathological inputs must finish quickly. Each case runs in a subprocess with a hard timeout,
so a regression to quadratic behaviour fails fast instead of hanging the test run."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRELUDE = "import sys, time; sys.path.insert(0, %r); from hacksolve import parsers as P, security as S\n" % str(ROOT)


def bounded(code: str, seconds: float = 20):
    try:
        r = subprocess.run([sys.executable, "-c", PRELUDE + code], capture_output=True, text=True, timeout=seconds, cwd=ROOT)
    except subprocess.TimeoutExpired:
        raise AssertionError(f"still running after {seconds}s: {code[:70]}")
    return r


class HostileInputTests(unittest.TestCase):
    def test_injection_scanner_is_not_quadratic(self):
        r = bounded("S.split_injection('send ' * 15000); S.split_injection('curl ' * 12000); S.split_injection('word ' * 12000)", 10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pdf_with_many_objects_and_no_endobj_is_bounded(self):
        r = bounded("""
try: P.parse_pdf(b"%PDF-1.4\\n" + b"1 0 obj " * 450000, time.monotonic() + 30)
except P.ParseError: pass""", 15)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_tounicode_maps_without_terminators_are_bounded(self):
        r = bounded("P._parse_cmap(b'beginbfchar ' * 300000); P._parse_cmap(b'beginbfrange ' * 300000)", 10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_huge_width_tables_and_operand_floods_are_bounded(self):
        r = bounded("""
import tracemalloc
w = b"[" + b"0 65535 500 " * 20000 + b"]"
pdf = (b"%PDF-1.4\\n1 0 obj\\n<< /Type /Catalog /Pages 2 0 R >>\\nendobj\\n2 0 obj\\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\\nendobj\\n"
       b"3 0 obj\\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\\nendobj\\n"
       b"4 0 obj\\n<< >>\\nstream\\nBT /F1 9 Tf (hello world) Tj ET\\nendstream\\nendobj\\n"
       b"5 0 obj\\n<< /Type /Font /Subtype /Type0 /DescendantFonts [6 0 R] >>\\nendobj\\n6 0 obj\\n<< /W " + w + b" >>\\nendobj\\ntrailer\\n<< /Root 1 0 R >>\\n")
t0 = time.time()
try: P.parse_pdf(pdf, time.monotonic() + 10)
except P.ParseError: pass
assert time.time() - t0 < 5, "width table"
tracemalloc.start()
list(P._ops(b"1 " * 1_000_000))
peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
assert peak < 40_000_000, "operand stack used %d bytes" % peak
t0 = time.time()
try: P._page_text(b"1 " * 6_000_000, {}, P._Font(), time.monotonic() + 1)
except P.ParseError as e: assert e.code == "timeout"
assert time.time() - t0 < 4, "number flood ignored the deadline" """, 40)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
