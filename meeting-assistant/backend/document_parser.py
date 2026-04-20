"""Parse meeting-minutes documents (PDF, DOCX, TXT) into discrete text units.

Each unit is a non-empty paragraph / exchange that can be independently
processed for entity extraction and Salesforce lookup.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".text"}


def parse_document(filename: str, content: bytes) -> list[str]:
    """Return a list of non-empty conversation units parsed from *content*.

    Dispatches to the correct parser based on the file extension of *filename*.
    Raises ValueError for unsupported types.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(content)
    if ext == ".docx":
        return _parse_docx(content)
    if ext in (".txt", ".text", ""):
        return _parse_txt(content)
    raise ValueError(
        f"Unsupported file type '{ext}'. Supported: PDF, DOCX, TXT."
    )


def _split_paragraphs(text: str) -> list[str]:
    """Split *text* on blank lines; return trimmed, non-empty paragraphs."""
    units = []
    for block in text.split("\n\n"):
        stripped = block.strip()
        if stripped:
            units.append(stripped)
    return units


def _parse_pdf(content: bytes) -> list[str]:
    try:
        import pdfplumber  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required to parse PDF files. "
            "Install it with: pip install pdfplumber"
        ) from exc

    units: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            units.extend(_split_paragraphs(text))
    if not units:
        raise ValueError("No readable text found in PDF. The file may be scanned, image-only, or empty.")
    return units


def _parse_docx(content: bytes) -> list[str]:
    try:
        from docx import Document  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required to parse DOCX files. "
            "Install it with: pip install python-docx"
        ) from exc

    doc = Document(io.BytesIO(content))
    units: list[str] = []
    current_block: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            current_block.append(text)
        else:
            if current_block:
                units.append("\n".join(current_block))
                current_block = []

    if current_block:
        units.append("\n".join(current_block))

    if not units:
        raise ValueError("No readable text found in DOCX. The file may be empty or contain only images/tables.")
    return units


def _parse_txt(content: bytes) -> list[str]:
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    units = _split_paragraphs(text)
    if not units:
        raise ValueError("The text file appears to be empty or contains no readable content.")
    return units
