"""Tests for backend.document_parser — parse_document and helpers."""
from __future__ import annotations

import io

import pytest

from backend.document_parser import parse_document, _split_paragraphs


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    """Generate a minimal .docx in memory using python-docx."""
    from docx import Document
    doc = Document()
    for para in paragraphs:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestSplitParagraphs:
    def test_crlf_endings_split_correctly(self):
        text = "Hello World\r\nThis is line 2\r\n\r\nSecond paragraph here"
        result = _split_paragraphs(text)
        assert result == ["Hello World\nThis is line 2", "Second paragraph here"]

    def test_empty_string_returns_empty_list(self):
        assert _split_paragraphs("") == []

    def test_only_blank_lines_returns_empty_list(self):
        assert _split_paragraphs("\n\n\n") == []


class TestParseTxt:
    def test_three_line_crlf_file_splits_correctly(self):
        content = b"Line one\r\nLine two\r\n\r\nParagraph two\r\n"
        result = parse_document("meeting.txt", content)
        assert len(result) == 2
        assert "Line one" in result[0]
        assert "Paragraph two" in result[1]

    def test_empty_txt_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            parse_document("empty.txt", b"")

    def test_whitespace_only_txt_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_document("blank.txt", b"   \n\n   \n")

    def test_text_extension_also_accepted(self):
        content = b"Some content here"
        result = parse_document("notes.text", content)
        assert len(result) >= 1


class TestParseDocx:
    def test_docx_with_blank_paragraph_splits_into_units(self):
        paragraphs = ["First sentence of meeting.", "", "Second unit begins here."]
        content = _make_docx_bytes(paragraphs)
        result = parse_document("notes.docx", content)
        assert len(result) >= 2
        assert any("First sentence" in u for u in result)
        assert any("Second unit" in u for u in result)

    def test_empty_docx_raises_value_error(self):
        content = _make_docx_bytes([])
        with pytest.raises(ValueError, match="empty"):
            parse_document("empty.docx", content)

    def test_docx_mixed_blank_nonblank_paragraphs(self):
        paragraphs = ["Para A", "Para B", "", "Para C", "", "", "Para D"]
        content = _make_docx_bytes(paragraphs)
        result = parse_document("meeting.docx", content)
        assert len(result) >= 3
        joined = "\n".join(result)
        for label in ("Para A", "Para B", "Para C", "Para D"):
            assert label in joined


class TestUnsupportedExtension:
    def test_unsupported_extension_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported"):
            parse_document("file.xyz", b"some bytes")

    def test_csv_extension_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_document("data.csv", b"a,b,c")
