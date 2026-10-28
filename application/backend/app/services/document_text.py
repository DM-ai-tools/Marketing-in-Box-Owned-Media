"""Text out of an uploaded file: the clip button on any `file_attach` question.

The answer to a file question stays plain text (the INPUTS block, the Context Store, exports and
Refine all read text), so an upload is turned into text here and handed back to the input box,
where the operator sees it before sending. Nothing is stored.

- `.pdf` via `pypdf`, `.docx` via `python-docx`, `.md` / `.markdown` / `.txt` decoded (UTF-8, then
  cp1252).
- `.doc` (Word 97-2003) is refused with the fix: there is no reliable pure-Python reader, and
  LibreOffice is not on the deployment.
- A PDF with no text layer (a scan) is refused rather than OCR'd.
- The type is checked by content as well as name, a `.docx` is refused past
  `MAX_UNPACKED_BYTES` unpacked (a zip bomb), and the text is capped at `MAX_CHARS`, with a note
  when cut.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_CHARS = 60_000

ACCEPTED = (".pdf", ".docx", ".doc", ".md", ".markdown", ".txt")

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class DocumentTextError(ValueError):
    """One sentence the operator can act on."""


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    kind: str
    text: str
    chars: int
    pages: int | None
    truncated: bool

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "kind": self.kind,
            "text": self.text,
            "chars": self.chars,
            "pages": self.pages,
            "truncated": self.truncated,
        }


def _extension(filename: str) -> str:
    name = filename.lower().strip()
    for ext in sorted(ACCEPTED, key=len, reverse=True):
        if name.endswith(ext):
            return ext
    return ""


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data.startswith(_PDF_MAGIC):
        raise DocumentTextError("This file is named .pdf but is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise DocumentTextError("This PDF is password-protected. Remove the password and upload it again.") from exc
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentTextError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise DocumentTextError("This PDF could not be read. It may be damaged; try exporting it again.") from exc
    return "\n\n".join(pages), len(pages)


def _docx(data: bytes) -> str:
    import docx

    if not data.startswith(_ZIP_MAGIC):
        if data.startswith(_OLE_MAGIC):
            raise DocumentTextError("This is an old Word (.doc) file named .docx. Save it as .docx or PDF and upload it again.")
        raise DocumentTextError("This file is named .docx but is not a Word document.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise DocumentTextError("This Word file is too large once unpacked.")
        document = docx.Document(io.BytesIO(data))
    except DocumentTextError:
        raise
    except Exception as exc:
        raise DocumentTextError("This Word file could not be read. It may be damaged; try saving it again.") from exc

    lines: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = docx.text.paragraph.Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                lines.append("")
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            level = re.match(r"Heading (\d)", style)
            if level:
                lines.append("#" * min(int(level.group(1)), 6) + " " + text)
            elif style.startswith("List"):
                lines.append("- " + text)
            else:
                lines.append(text)
        elif tag == "tbl":
            table = docx.table.Table(child, document)
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines)


def _plain(data: bytes) -> str:
    if data.startswith(_PDF_MAGIC) or data.startswith(_ZIP_MAGIC) or data.startswith(_OLE_MAGIC):
        raise DocumentTextError("This file is named as text but holds a binary document. Upload it with its real extension.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentTextError("This text file's encoding could not be read. Save it as UTF-8 and upload it again.")


def extract(filename: str, data: bytes) -> ExtractedDocument:
    """The file's text, or `DocumentTextError` saying what to do instead."""
    ext = _extension(filename)
    if not ext:
        raise DocumentTextError("Upload a PDF, Word (.docx), Markdown or text file.")
    if not data:
        raise DocumentTextError("This file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentTextError(f"This file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Upload a smaller one.")
    if ext == ".doc":
        raise DocumentTextError("Old Word (.doc) files can't be read. Save it as .docx or PDF and upload it again.")

    pages: int | None = None
    if ext == ".pdf":
        raw, pages = _pdf(data)
        kind = "pdf"
    elif ext == ".docx":
        raw = _docx(data)
        kind = "docx"
    else:
        raw = _plain(data)
        kind = "markdown" if ext in (".md", ".markdown") else "text"

    text = _tidy(raw)
    if not text:
        if kind == "pdf":
            raise DocumentTextError("This PDF has no text layer (it looks scanned). Upload a PDF with selectable text, or a Word file.")
        raise DocumentTextError("No text was found in this file.")

    chars = len(text)
    truncated = chars > MAX_CHARS
    if truncated:
        cut = text.rfind("\n", 0, MAX_CHARS)
        text = text[: cut if cut > MAX_CHARS // 2 else MAX_CHARS].rstrip()
    return ExtractedDocument(filename=filename, kind=kind, text=text, chars=chars, pages=pages, truncated=truncated)
