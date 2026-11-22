"""Upload validation and text extraction for TXT, MD, DOCX and PDF using only the standard library.

Nothing in an uploaded file is ever executed. Limits: size, entry count, inflated size, compression
ratio, a parse-time budget, and no DOCTYPE/ENTITY in DOCX XML (blocks XXE and entity bombs).
"""
from __future__ import annotations

import base64
import bisect
import io
import re
import time
import unicodedata
import zipfile
import zlib
from xml.etree import ElementTree as ET

from .security import clean_text, safe_filename

ALLOWED = {
    ".txt": "text/plain", ".md": "text/markdown", ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PARSE_SECONDS = 10.0
MAX_BRIEF_CHARS = 60000
MAX_ZIP_ENTRIES, MAX_INFLATED, MAX_ENTRY, MAX_RATIO = 300, 30 * 2 ** 20, 20 * 2 ** 20, 100
MAX_STREAM = 8 * 2 ** 20
MAX_PDF_OBJECTS, MAX_MAP_ENTRIES, MAX_OPERANDS = 300_000, 200_000, 100_000
MAX_DOC_XML = 8 * 2 ** 20      # real documents are a few hundred KB
MAGIC = [(b"%PDF-", "pdf"), (b"PK\x03\x04", "zip"), (b"MZ", "exe"), (b"\x7fELF", "elf"),
         (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"\x1f\x8b", "gzip")]


class ParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def sniff(data: bytes):
    head = data[:1024].lstrip() if data[:1024].lstrip().startswith(b"%PDF-") else data[:16]
    for sig, kind in MAGIC:
        if head.startswith(sig):
            return kind
    return None


def parse_upload(filename: str, data: bytes, max_bytes: int) -> dict:
    name, ext = safe_filename(filename)
    if ext not in ALLOWED:
        raise ParseError("unsupported_type", "Only " + ", ".join(sorted(ALLOWED)) + " files are accepted")
    if not data:
        raise ParseError("empty_file", "The file is empty")
    if len(data) > max_bytes:
        raise ParseError("too_large", f"Files must be under {max_bytes // 2 ** 20} MB")
    kind = sniff(data)
    want = {".pdf": "pdf", ".docx": "zip"}.get(ext)
    if kind != want:
        raise ParseError("bad_file", "The file content doesn't match its extension")
    deadline = time.monotonic() + PARSE_SECONDS
    if ext == ".pdf":
        text = parse_pdf(data, deadline)
    elif ext == ".docx":
        text = parse_docx(data)
    else:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):          # UTF-16 with a BOM (Windows Notepad "Unicode")
            try:
                text = data.decode("utf-16")
            except UnicodeDecodeError:
                raise ParseError("bad_file", "This text file could not be decoded")
        elif b"\x00" in data:
            raise ParseError("bad_file", "This looks like a binary file, not text")
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("cp1252", "replace")
    truncated = len(text) > MAX_BRIEF_CHARS
    text = clean_text(text, MAX_BRIEF_CHARS)
    if len(text.strip()) < 10:
        raise ParseError("no_text", "No readable text found. Scanned PDFs need OCR, which isn't supported.")
    warnings = []
    if ext == ".pdf" and re.search(r"[\u0590-\u06ff\u0900-\u0dff]", text):
        warnings.append("This PDF contains Indic, Arabic or Hebrew text. Check that it was read in the right order before analyzing.")
    return {"name": name, "ext": ext, "mime": ALLOWED[ext], "text": text, "truncated": truncated, "warnings": warnings}


# ---------------------------------------------------------------- DOCX
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def parse_docx(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("bad_file", "Not a valid DOCX file")
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ParseError("archive_limits", "The document contains too many parts")
    total, names = 0, set()
    for i in infos:
        parts = i.filename.split("/")
        if i.filename.startswith("/") or ".." in parts or "\\" in i.filename or ":" in parts[0]:
            raise ParseError("unsafe_archive", "The document contains an unsafe path")
        total += i.file_size
        if i.file_size > MAX_ENTRY or (i.file_size > 2 ** 20 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO):
            raise ParseError("archive_limits", "The document expands to too much data")
        names.add(i.filename)
    if total > MAX_INFLATED:
        raise ParseError("archive_limits", "The document expands to too much data")
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ParseError("bad_file", "Missing DOCX parts")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ParseError("macros", "Macro-enabled documents are not accepted")
    with zf.open("word/document.xml") as f:
        xml = f.read(MAX_DOC_XML + 1)
    if len(xml) > MAX_DOC_XML:
        raise ParseError("archive_limits", "The document body is too large")
    if re.search(rb"<!\s*(doctype|entity)", xml, re.I):
        raise ParseError("unsafe_xml", "The document uses XML features that are blocked for safety")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ParseError("bad_file", "The document XML is malformed")
    paras = []
    for p in root.iter(W + "p"):
        buf = []
        for el in p.iter():
            if el.tag == W + "t":
                buf.append(el.text or "")
            elif el.tag == W + "tab":
                buf.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                buf.append("\n")
        paras.append("".join(buf))
    return "\n".join(paras)


# ---------------------------------------------------------------- PDF (best effort)
_WS = b" \t\r\n\x00\x0c"
_DELIM = b"()<>[]{}/%" + _WS


def _read_literal(buf: bytes, i: int):
    depth, i, out = 1, i + 1, bytearray()
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(buf) and depth:
        ch = buf[i]
        if ch == 0x5C:
            i += 1
            if i >= len(buf):
                break
            nx = buf[i]
            if nx in esc:
                out.append(esc[nx]); i += 1
            elif 0x30 <= nx <= 0x37:
                j = i
                while j < len(buf) and j < i + 3 and 0x30 <= buf[j] <= 0x37:
                    j += 1
                out.append(int(buf[i:j], 8) & 255); i = j
            elif nx in b"\r\n":
                i += 2 if buf[i:i + 2] == b"\r\n" else 1
            else:
                out.append(nx); i += 1
            continue
        if ch == 0x28:
            depth += 1
        elif ch == 0x29:
            depth -= 1
            if depth == 0:
                i += 1
                break
        out.append(ch); i += 1
    return bytes(out), i


def _ops(content: bytes, deadline: float = 0.0):
    """Yield (operator, operands) from a PDF content stream."""
    i, n, st, depth, steps = 0, len(content), [], 0, 0
    while i < n:
        steps += 1
        if not steps & 0x3FFF and deadline and time.monotonic() > deadline:      # operands alone never yield, so check here too
            raise ParseError("timeout", "The PDF took too long to read")
        if len(st) > 256:
            if depth == 0:
                del st[:128]                      # operands we will never look at (only the last few matter)
            elif len(st) > MAX_OPERANDS:
                raise ParseError("bad_file", "The PDF content is malformed")
        c = content[i]
        if c in _WS:
            i += 1
        elif c == 0x25:
            while i < n and content[i] not in b"\r\n":
                i += 1
        elif c == 0x28:
            s, i = _read_literal(content, i)
            st.append(("s", s))
        elif c == 0x3C and content[i + 1:i + 2] == b"<":
            j, dd = i + 2, 1
            while j < n and dd:
                two = content[j:j + 2]
                if two == b"<<":
                    dd, j = dd + 1, j + 2
                elif two == b">>":
                    dd, j = dd - 1, j + 2
                elif content[j:j + 1] == b"<":                      # a hex string inside the dictionary
                    k = content.find(b">", j)
                    j = k + 1 if k >= 0 else n
                else:
                    j += 1
            st.append(("d", content[i:j]))
            i = j
        elif c == 0x3C:
            j = content.find(b">", i)
            if j < 0:
                break
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", content[i + 1:j])
            st.append(("h", bytes.fromhex((hx + (b"0" if len(hx) % 2 else b"")).decode())))
            i = j + 1
        elif c == 0x5B:
            st.append(("[", None)); i += 1; depth += 1
        elif c == 0x5D:
            items = []
            while st and st[-1][0] != "[":
                items.append(st.pop())
            if st:
                st.pop()
            st.append(("a", items[::-1])); i += 1; depth = max(0, depth - 1)
        elif c == 0x2F:
            j = i + 1
            while j < n and content[j] not in _DELIM:
                j += 1
            st.append(("n", content[i + 1:j])); i = j
        elif c in b")>]}{":
            i += 1
        else:
            j = i
            while j < n and content[j] not in _DELIM:
                j += 1
            tok, i = content[i:j], max(j, i + 1)
            try:
                st.append(("#", float(tok)))
            except ValueError:
                yield tok.decode("latin-1"), st
                st = []


LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"})
GLYPHS = {"space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$", "percent": "%", "ampersand": "&", "quotesingle": "'",
          "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",", "hyphen": "-", "minus": "-", "period": ".", "slash": "/",
          "colon": ":", "semicolon": ";", "less": "<", "equal": "=", "greater": ">", "question": "?", "at": "@", "bracketleft": "[", "bracketright": "]",
          "backslash": "\\", "asciicircum": "^", "underscore": "_", "grave": "`", "braceleft": "{", "braceright": "}", "bar": "|", "asciitilde": "~",
          "bullet": "\u2022", "endash": "\u2013", "emdash": "\u2014", "quoteleft": "\u2018", "quoteright": "\u2019", "quotedblleft": "\u201c",
          "quotedblright": "\u201d", "ellipsis": "\u2026", "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl", "degree": "\u00b0",
          "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
ACCENTS = {"acute": "\u0301", "grave": "\u0300", "circumflex": "\u0302", "tilde": "\u0303", "dieresis": "\u0308", "ring": "\u030a", "cedilla": "\u0327", "caron": "\u030c"}


def _glyph(name: str) -> str:
    if name in GLYPHS:
        return GLYPHS[name]
    if len(name) == 1:
        return name
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})|u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1) or m.group(2), 16))
    m = re.fullmatch(r"([A-Za-z])(acute|grave|circumflex|tilde|dieresis|ring|cedilla|caron)", name)
    return unicodedata.normalize("NFC", m.group(1) + ACCENTS[m.group(2)]) if m else ""


def _uni(hexs: bytes) -> str:
    h = hexs.decode()
    if len(h) <= 2:
        return chr(int(h, 16))
    return bytes.fromhex(h if len(h) % 2 == 0 else h + "0").decode("utf-16-be", "ignore")


def _blocks(data: bytes, start: bytes, end: bytes):
    for part in data.split(start)[1:]:
        yield part.split(end, 1)[0]


def _parse_cmap(data: bytes):
    m, width = {}, 1
    sp = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", data)
    if sp:
        width = max(1, len(sp.group(1)) // 2)
    budget = MAX_MAP_ENTRIES                      # total assignments, so repeated overlapping ranges cannot burn CPU
    for blk in _blocks(data, b"beginbfchar", b"endbfchar"):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            m[int(a, 16)] = _uni(b)
            budget -= 1
        if budget <= 0:
            return m, width
    for blk in _blocks(data, b"beginbfrange", b"endbfrange"):
        for a, b, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", blk):
            lo, hi = int(a, 16), int(b, 16)
            if not 0 <= hi - lo < 65536:
                continue
            budget -= hi - lo + 1
            if budget <= 0:
                return m, width
            if rest.startswith(b"["):
                for k, dst in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", rest)[:hi - lo + 1]):
                    m[lo + k] = _uni(dst)
            else:
                base = _uni(rest[1:-1])
                for k in range(hi - lo + 1):
                    m[lo + k] = (base[:-1] + chr(ord(base[-1]) + k)) if base else ""
    return m, width


class _Font:
    """How to turn the bytes of a PDF string into text (and an advance width) for one font."""

    def __init__(self):
        self.cmap, self.width, self.diff, self.base, self.type0 = {}, 1, {}, "cp1252", False
        self.widths, self.dw = {}, 500.0

    def glyphs(self, b: bytes, kind: str):
        two = len(b) % 2 == 0 and (self.type0 or (self.width == 2 and (kind == "h" or b"\x00" in b)))
        codes = [int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2)] if two else list(b)
        if self.cmap:
            return [(c, self.cmap.get(c) or ("" if two else chr(c) if 32 <= c < 127 else "")) for c in codes], two
        if self.type0:
            return [(c, "") for c in codes], two     # CID font without a ToUnicode map: cannot be decoded reliably
        return [(c, self.diff.get(c) or bytes([c]).decode(self.base, "replace")) for c in codes], two


def _mul(m, n):
    """PDF matrix product m x n (both [a b c d e f])."""
    return [m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2], m[2] * n[1] + m[3] * n[3],
            m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5]]


def _actual_text(d: bytes):
    """The logical text a PDF attaches to a glyph cluster (/ActualText), or None."""
    m = re.search(rb"/ActualText\s*(?:<([0-9A-Fa-f\s]*)>|\(((?:\\.|[^\\)])*)\))", d, re.S)
    if not m:
        return None
    if m.group(1) is not None:
        hx = re.sub(rb"\s", b"", m.group(1))
        raw = bytes.fromhex((hx + b"0" * (len(hx) % 2)).decode())
    else:
        raw = re.sub(rb"\\(.)", rb"\1", m.group(2), flags=re.S)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "ignore")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "ignore")
    return raw.decode("cp1252", "ignore")


MAX_TEXT_CHARS = 150_000     # we only ever use the first 60,000 characters; stop reading well before pathological files


def _page_text(content: bytes, fonts: dict, default: "_Font", deadline: float = 0.0) -> str:
    """Interpret the text operators. Spaces and line breaks come from real page positions (so it does not
    matter whether a generator uses one text block per line, per word or per glyph); /ActualText wins over glyphs."""
    out, cur, count, size = [], default, 0, 0
    fs = tc = tw = tl = 0.0
    tz = 1.0
    tm, tlm, ctm, cstack, prev, mc = [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0], [], None, []

    def put(t):
        nonlocal size
        if t:
            out.append(t)
            size += len(t)

    def span():
        return next((m for m in reversed(mc) if m is not None), None)

    def pos():
        return (tm[4] * ctm[0] + tm[5] * ctm[2] + ctm[4], tm[4] * ctm[1] + tm[5] * ctm[3] + ctm[5])

    def sep():
        """Before showing text: newline if we moved to another line, space if there is a visible gap."""
        sp = span()
        if prev is None or not out or (sp is not None and sp[1]):
            return                                  # inside a cluster that was already written: no separators
        x, y = pos()
        size_ = abs(fs * (tm[3] * ctm[3] + tm[2] * ctm[1])) or abs(fs) or 10.0
        em = abs(fs * (tm[0] * ctm[0] + tm[1] * ctm[2])) or size_
        if abs(y - prev[1]) > 0.5 * size_:
            if out[-1] != "\n":
                put("\n")
        elif x - prev[0] > 0.12 * em and out[-1] not in (" ", "\n"):
            put(" ")

    def show(val, kind):
        nonlocal prev
        gl, two = cur.glyphs(val, kind)
        sp = span()
        if sp is None:
            put("".join(t for _, t in gl))
        elif not sp[1]:
            put(sp[0])
            sp[1] = True
        adv = sum((cur.widths.get(c, cur.dw) / 1000 * fs + tc + (tw if c == 32 and not two else 0)) * tz for c, _ in gl)
        tm[4] += adv * tm[0]
        tm[5] += adv * tm[1]
        prev = pos()

    def move(tx, ty):
        tlm[4] += tx * tlm[0] + ty * tlm[2]
        tlm[5] += tx * tlm[1] + ty * tlm[3]
        tm[:] = tlm

    for op, st in _ops(content, deadline):
        count += 1
        if count % 2048 == 0:
            if deadline and time.monotonic() > deadline:
                raise ParseError("timeout", "The PDF took too long to read")
            if size > MAX_TEXT_CHARS or count > 1_500_000:
                break
        top = st[-1] if st else None
        nums = [v for k, v in st if k == "#"]
        if op == "BT":
            tm[:], tlm[:] = [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0]
        elif op == "q":
            if len(cstack) < 64:
                cstack.append(ctm[:])
        elif op == "Q":
            if cstack:
                ctm[:] = cstack.pop()
        elif op == "cm" and len(nums) >= 6:
            ctm[:] = _mul(nums[-6:], ctm)
        elif op == "BDC":
            at = _actual_text(top[1]) if top and top[0] == "d" else None
            mc.append([at, False] if at is not None and len(mc) < 64 else None)
        elif op == "BMC":
            mc.append(None) if len(mc) < 64 else None
        elif op == "EMC":
            if mc:
                mc.pop()
        elif op == "Tf" and len(st) >= 2 and st[-2][0] == "n" and top[0] == "#":
            cur, fs = fonts.get(st[-2][1], default), top[1]
        elif op == "Tc" and nums:
            tc = nums[-1]
        elif op == "Tw" and nums:
            tw = nums[-1]
        elif op == "Tz" and nums:
            tz = nums[-1] / 100
        elif op == "TL" and nums:
            tl = nums[-1]
        elif op == "Tm" and len(nums) >= 6:
            tm[:] = tlm[:] = nums[-6:]
        elif op in ("Td", "TD") and len(nums) >= 2:
            if op == "TD":
                tl = -nums[-1]
            move(nums[-2], nums[-1])
        elif op == "T*":
            move(0, -tl)
        elif op == "Tj" and top and top[0] in "sh":
            sep(); show(top[1], top[0])
        elif op in ("'", '"') and top and top[0] in "sh":
            if op == '"' and len(nums) >= 2:
                tw, tc = nums[-2], nums[-1]
            move(0, -tl); sep(); show(top[1], top[0])
        elif op == "TJ" and top and top[0] == "a":
            sep()
            for kind, val in top[1]:
                if kind in ("s", "h"):
                    show(val, kind)
                elif kind == "#":
                    tm[4] -= val / 1000 * fs * tz * tm[0]
                    sp = span()
                    if val < -150 and out and out[-1] not in (" ", "\n") and not (sp is not None and sp[1]):
                        put(" ")
                    prev = pos()
    return "".join(out)


# --- tiny PDF object helpers (regex based; enough for text extraction, not a general PDF library)
def _balanced(b: bytes, i: int) -> int:
    two = b[i:i + 2] == b"<<"
    op, cl = (b"<<", b">>") if two else (b"[", b"]")
    depth, j, n = 0, i, len(b)
    while j < n:
        if b[j:j + len(op)] == op:
            depth, j = depth + 1, j + len(op)
        elif b[j:j + len(cl)] == cl:
            depth, j = depth - 1, j + len(cl)
            if depth == 0:
                return j
        elif b[j] == 0x28:
            j = _read_literal(b, j)[1]
        else:
            j += 1
    return n


def _val(d: bytes, key: bytes) -> bytes:
    m = re.search(rb"/" + re.escape(key) + rb"(?![\w.#+-])\s*", d)
    if not m:
        return b""
    i = m.end()
    if d[i:i + 2] == b"<<" or d[i:i + 1] == b"[":
        return d[i:_balanced(d, i)]
    r = re.match(rb"\d+\s+\d+\s+R", d[i:i + 40])
    if r:
        return r.group(0)
    t = re.match(rb"/?[^\s/<>\[\]()]+", d[i:i + 200])
    return t.group(0) if t else b""


def _ref(v: bytes):
    m = re.match(rb"\s*(\d+)\s+\d+\s+R", v)
    return int(m.group(1)) if m else None


def _refs(v: bytes) -> list:
    return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", v)]


def _unpredict(data: bytes, cols: int) -> bytes:
    row, out, prev = cols + 1, bytearray(), bytearray(cols)
    for i in range(0, len(data) - row + 1, row):
        ft, cur = data[i], bytearray(data[i + 1:i + row])
        for k in range(cols):
            a, b, c = (cur[k - 1] if k else 0), prev[k], (prev[k - 1] if k else 0)
            if ft == 1:
                cur[k] = (cur[k] + a) & 255
            elif ft == 2:
                cur[k] = (cur[k] + b) & 255
            elif ft == 3:
                cur[k] = (cur[k] + (a + b) // 2) & 255
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                cur[k] = (cur[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += cur
        prev = cur
    return bytes(out)


class _Pdf:
    def __init__(self, data: bytes, deadline: float):
        self.data, self.deadline, self.budget = data, deadline, [MAX_INFLATED]
        self.objs, self.cache, self.fonts = {}, {}, {}
        starts = [(m.end(), int(m.group(1))) for _, m in zip(range(MAX_PDF_OBJECTS), re.finditer(rb"(\d{1,7})\s+\d{1,5}\s+obj\b", data))]
        ends = [m.start() for m in re.finditer(rb"endobj", data)]
        for k, (s1, num) in enumerate(starts):
            if time.monotonic() > deadline:
                raise ParseError("timeout", "The PDF took too long to read")
            nxt = starts[k + 1][0] - 4 if k + 1 < len(starts) else len(data)     # an object never runs into the next one
            i = bisect.bisect_left(ends, s1)
            body = data[s1:min(ends[i] if i < len(ends) else len(data), max(nxt, s1))]
            sm = re.search(rb"stream\r?\n", body)
            if not sm:
                self.objs[num] = (body, None)
                continue
            raw = body[sm.end():]
            end = raw.rfind(b"endstream")
            raw = raw[:end] if end >= 0 else raw
            raw = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
            self.objs[num] = (body[:sm.start()], raw)
        for num in [n for n, (d, r) in self.objs.items() if r is not None and re.search(rb"/Type\s*/ObjStm", d)]:
            d = self.objs[num][0]
            blob = self.stream(num)
            first, count = _val(d, b"First"), _val(d, b"N")
            if not blob or not first.isdigit() or not count.isdigit():
                continue
            nums = [int(x) for x in re.findall(rb"\d+", blob[:int(first)])][:int(count) * 2]
            pairs = list(zip(nums[0::2], nums[1::2]))
            for k, (onum, off) in enumerate(pairs):
                end = pairs[k + 1][1] if k + 1 < len(pairs) else len(blob) - int(first)
                self.objs.setdefault(onum, (blob[int(first) + off:int(first) + end], None))

    def dictb(self, v: bytes) -> bytes:
        if v[:2] == b"<<":
            return v
        n = _ref(v)
        return self.objs[n][0] if n in self.objs else b""

    def stream(self, num):
        if num in self.cache:
            return self.cache[num]
        d, raw = self.objs.get(num, (b"", None))
        out = None
        if raw is not None:
            out = raw
            flt = _val(d, b"Filter")
            for name in re.findall(rb"/(\w+)", flt):
                if name in (b"FlateDecode", b"Fl"):
                    z = zlib.decompressobj()
                    try:
                        out = z.decompress(out, MAX_STREAM)
                    except zlib.error:
                        out = None
                        break
                    if z.unconsumed_tail:
                        raise ParseError("archive_limits", "The PDF expands to too much data")
                    pm = self.dictb(_val(d, b"DecodeParms"))
                    if pm and _val(pm, b"Predictor").isdigit() and int(_val(pm, b"Predictor")) >= 10:
                        cols = _val(pm, b"Columns")
                        out = _unpredict(out, int(cols) if cols.isdigit() else 1)
                elif name in (b"ASCII85Decode", b"A85"):
                    try:
                        out = base64.a85decode(out.strip().removesuffix(b"~>"))
                    except ValueError:
                        out = None
                        break
                elif name in (b"ASCIIHexDecode", b"AHx"):
                    try:
                        out = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", out.split(b">")[0]).decode().ljust(len(out) // 2 * 2, "0"))
                    except ValueError:
                        out = None
                        break
                else:
                    out = None
                    break
            if out is not None:
                self.budget[0] -= len(out)
                if self.budget[0] < 0:
                    raise ParseError("archive_limits", "The PDF expands to too much data")
        self.cache[num] = out
        return out

    def font(self, num: int) -> _Font:
        if num in self.fonts:
            return self.fonts[num]
        f = self.fonts[num] = _Font()
        d = self.objs.get(num, (b"", None))[0]
        f.type0 = bool(re.search(rb"/Subtype\s*/Type0", d))
        tu = _ref(_val(d, b"ToUnicode"))
        blob = self.stream(tu) if tu in self.objs else None
        if blob and (b"beginbfchar" in blob or b"beginbfrange" in blob):
            f.cmap, f.width = _parse_cmap(blob)
        self._widths(d, f)
        if re.search(rb"/BaseFont\s*/[A-Z]{6}\+(?:CM|LM)", d) and not f.cmap and not f.type0:
            f.diff.update({c: t for c, t in OT1.items()})
        enc = _val(d, b"Encoding")
        ed = self.dictb(enc)
        name = enc if enc.startswith(b"/") else _val(ed, b"BaseEncoding") if ed else b""
        if b"MacRoman" in name:
            f.base = "mac_roman"
        if ed:
            code = 0
            for num_, nm in re.findall(rb"(\d+)|/([^\s/\[\]()<>]+)", _val(ed, b"Differences")):
                if num_:
                    code = int(num_)
                else:
                    f.diff[code] = _glyph(nm.decode("latin-1"))
                    code += 1
        return f

    def _widths(self, d: bytes, f: _Font) -> None:
        def body(v):
            return v if v[:1] == b"[" else self.objs.get(_ref(v), (b"", None))[0]
        try:
            if f.type0:
                kids = _refs(body(_val(d, b"DescendantFonts")))
                kd = self.objs[kids[0]][0] if kids and kids[0] in self.objs else b""
                dw = _val(kd, b"DW")
                f.dw = float(dw) if re.fullmatch(rb"[-\d.]+", dw or b"x") else 1000.0
                toks = re.findall(rb"\[|\]|-?[\d.]+", body(_val(kd, b"W")))[1:-1]
                i, budget = 0, MAX_MAP_ENTRIES
                while i < len(toks) - 1 and budget > 0:
                    c = int(float(toks[i])); i += 1
                    if toks[i] == b"[":
                        i += 1
                        while i < len(toks) and toks[i] != b"]" and budget > 0:
                            f.widths[c] = float(toks[i]); c += 1; i += 1; budget -= 1
                        i += 1
                    elif i + 1 < len(toks):
                        c2, w = int(float(toks[i])), float(toks[i + 1]); i += 2
                        n = max(0, min(c2, c + 65535) - c + 1)
                        budget -= n
                        if budget > 0:
                            for k in range(c, c + n):
                                f.widths[k] = w
            else:
                first = _val(d, b"FirstChar")
                ws = [float(x) for x in re.findall(rb"-?[\d.]+", body(_val(d, b"Widths")))]
                f.widths = {(int(first) if first.isdigit() else 0) + i: w for i, w in enumerate(ws)}
        except (ValueError, IndexError):
            pass

    def pages(self) -> list:
        out, seen = [], set()

        def walk(num, res, depth):
            if num is None or num in seen or depth > 40 or len(out) > 3000 or num not in self.objs:
                return
            seen.add(num)
            d = self.objs[num][0]
            res = _val(d, b"Resources") or res
            kids = _val(d, b"Kids")
            if kids:
                for k in _refs(kids):
                    walk(k, res, depth + 1)
            elif re.search(rb"/Type\s*/Page(?!\w)", d):
                out.append((d, res))

        root = re.search(rb"/Root\s+(\d+)\s+\d+\s+R", self.data)
        if root and int(root.group(1)) in self.objs:
            walk(_ref(_val(self.objs[int(root.group(1))][0], b"Pages")), b"", 0)
        if not out:
            out = [(d, _val(d, b"Resources")) for n, (d, r) in sorted(self.objs.items()) if re.search(rb"/Type\s*/Page(?!\w)", d)]
        return out

    def page_text(self, d: bytes, res: bytes) -> str:
        cv, parts = _val(d, b"Contents"), []
        refs = _refs(cv) if cv[:1] == b"[" else ([_ref(cv)] if _ref(cv) else [])
        if len(refs) == 1 and refs[0] in self.objs and self.objs[refs[0]][1] is None:
            refs = _refs(self.objs[refs[0]][0]) or refs
        for n in refs:
            blob = self.stream(n)
            if blob:
                parts.append(blob)
        fonts = {}
        for name, num in re.findall(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", self.dictb(_val(self.dictb(res), b"Font"))):
            fonts[name] = self.font(int(num))
        return _page_text(b"\n".join(parts), fonts, _Font(), self.deadline)

    def legacy_text(self) -> str:
        """Fallback when the page tree is unusable: every text-looking stream, one merged character map."""
        merged, contents = _Font(), []
        for num in sorted(self.objs):
            d, raw = self.objs[num]
            if raw is None or re.search(rb"/(Subtype\s*/Image|Length1|Length2|FontFile|Type\s*/(XRef|ObjStm|Metadata|EmbeddedFile))", d):
                continue
            blob = self.stream(num)
            if not blob:
                continue
            if b"beginbfchar" in blob or b"beginbfrange" in blob:
                cm, w = _parse_cmap(blob)
                merged.cmap.update(cm)
                merged.width = max(merged.width, w)
            elif b"BT" in blob and b"ET" in blob and sum(32 <= c < 127 or c in b"\r\n\t" for c in blob[:4000]) / max(1, len(blob[:4000])) > 0.8:
                contents.append(blob)
        return "\n".join(_page_text(c, {}, merged, self.deadline) for c in contents)


def _reflow(text: str, dehyphenate: bool = False) -> str:
    """Join soft-wrapped lines: a line that ends mid-sentence and is followed by a lowercase start.
    TeX hyphenates automatically, so for TeX output a trailing hyphen is dropped when the word is rejoined.
    Linear time: parts are collected and joined once per output line."""
    out, parts = [], []

    def flush():
        if parts:
            out.append("".join(parts))
            parts.clear()

    for ln in text.split("\n"):
        ln = ln.strip()
        if parts and ln and ln[0].islower() and not re.search(r"[.!?:;\u2022]$", parts[-1]):
            if dehyphenate and re.search(r"[A-Za-z]{2}-$", parts[-1]):
                parts[-1] = parts[-1][:-1]
                parts.append(ln)
            else:
                parts.append(" " + ln)
        else:
            flush()
            parts.append(ln)
    flush()
    return "\n".join(out)


def parse_pdf(data: bytes, deadline: float) -> str:
    if b"%PDF-" not in data[:1024]:
        raise ParseError("bad_file", "This doesn't look like a PDF")
    if re.search(rb"/Encrypt\b", data):
        raise ParseError("encrypted", "Encrypted PDFs aren't supported. Export an unlocked copy.")
    pdf = _Pdf(data, deadline)
    text = ""
    try:
        chunks, total = [], 0
        for d, r in pdf.pages():
            chunks.append(pdf.page_text(d, r))
            total += len(chunks[-1])
            if total > MAX_TEXT_CHARS or time.monotonic() > deadline:
                break
        text = "\n".join(chunks)
    except RecursionError:
        pass
    if len(re.sub(r"\s", "", text)) < 10:
        text = pdf.legacy_text()
    text = unicodedata.normalize("NFC", text).translate(LIGATURES)
    tex = bool(re.search(rb"pdfTeX|XeTeX|LuaTeX", data[:8192] + data[-8192:]))
    text = _reflow(re.sub(r"[ \t]+", " ", re.sub(r"[ \t]*\n[ \t]*", "\n", text)), tex)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    body = re.sub(r"\s", "", text)
    if not body or sum(ch.isalpha() for ch in body) / len(body) < 0.5:
        raise ParseError("no_text", "No readable text found. Scanned PDFs need OCR, which isn't supported. Paste the text instead, or export the document to a new PDF.")
    return text
