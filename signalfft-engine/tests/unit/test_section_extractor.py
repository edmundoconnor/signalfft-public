"""Tests for the filing section extractor (pure parsing logic)."""

from __future__ import annotations

import logging

import pytest

from engine.filing_processing.extractor import (
    _clean_text,
    _detect_sgml,
    _strip_sgml,
    extract_sections,
)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

SIMPLE_10K_HTML = """
<html><body>
<h2>Item 1. Business</h2>
<p>We are a technology company that provides services worldwide.</p>
<p>Our revenue grew 15% year over year.</p>

<h2>Item 1A. Risk Factors</h2>
<p>Investing in our securities involves a high degree of risk.</p>
<p>Market conditions may adversely affect our business.</p>

<h2>Item 7. Management's Discussion and Analysis</h2>
<p>The following discussion should be read in conjunction with our financial statements.</p>
</body></html>
"""

BOLD_HEADING_HTML = """
<html><body>
<p><b>Item 1. Business</b></p>
<p>We manufacture widgets for industrial applications.</p>

<p><strong>Item 1A. Risk Factors</strong></p>
<p>Our business is subject to various risks and uncertainties.</p>

<p><b>Item 7. Management's Discussion and Analysis</b></p>
<p>Results of operations discussion follows.</p>
</body></html>
"""

P_HEADING_HTML = """
<html><body>
<p>Item 1. Business</p>
<p>We are a financial services company.</p>

<p>Item 1A. Risk Factors</p>
<p>Risks include credit risk and market risk.</p>
</body></html>
"""

SIMPLE_8K_HTML = """
<html><body>
<h3>Item 2.02 Results of Operations and Financial Condition</h3>
<p>The company reported quarterly earnings of $1.50 per share.</p>

<h3>Item 9.01 Financial Statements and Exhibits</h3>
<p>Exhibit 99.1 - Press release dated February 15, 2026.</p>
</body></html>
"""

SGML_WRAPPED_HTML = """<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>10-K
<SEQUENCE>1
<FILENAME>filing.htm
<html><body>
<h2>Item 1. Business</h2>
<p>Our company was incorporated in Delaware.</p>

<h2>Item 1A. Risk Factors</h2>
<p>We face intense competition.</p>
</body></html>
</DOCUMENT>
</SEC-DOCUMENT>
"""

SGML_PLAIN_TEXT = """<SEC-DOCUMENT>
<DOCUMENT>
<TYPE>10-K
<SEQUENCE>1
Item 1. Business
Our company was founded in 2010.
We provide cloud computing services.

Item 1A. Risk Factors
Our business faces competition.
Regulatory changes may affect us.

Item 7. Management's Discussion and Analysis
Revenue increased by 20% in fiscal year 2025.
</DOCUMENT>
</SEC-DOCUMENT>
"""


# ---------------------------------------------------------------------------
# TestExtractSections
# ---------------------------------------------------------------------------


class TestExtractSections:
    def test_10k_h2_headings(self):
        """10-K with h2 headings should extract item_1, item_1a, item_7."""
        sections = extract_sections(SIMPLE_10K_HTML, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections
        assert "item_7" in sections
        assert "technology company" in sections["item_1"]
        assert "high degree of risk" in sections["item_1a"]

    def test_8k_item_format(self):
        """8-K should use item_X_XX section names."""
        sections = extract_sections(SIMPLE_8K_HTML, "8-K")
        assert "item_2_02" in sections
        assert "item_9_01" in sections
        assert "quarterly earnings" in sections["item_2_02"]

    def test_bold_headings(self):
        """Sections with <b>/<strong> headings should be detected."""
        sections = extract_sections(BOLD_HEADING_HTML, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections
        assert "widgets" in sections["item_1"]

    def test_p_headings(self):
        """Sections with <p> tag headings should be detected."""
        sections = extract_sections(P_HEADING_HTML, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections

    def test_case_insensitive_matching(self):
        """Headings should match regardless of case."""
        html = """
        <html><body>
        <h2>ITEM 1. BUSINESS</h2>
        <p>Upper case heading content.</p>

        <h2>ITEM 1A. RISK FACTORS</h2>
        <p>More upper case content.</p>
        </body></html>
        """
        sections = extract_sections(html, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections

    def test_fallback_to_full_text(self):
        """When fewer than 2 sections found, should return full_text."""
        html = "<html><body><p>Just a single paragraph of text.</p></body></html>"
        sections = extract_sections(html, "10-K")
        assert "full_text" in sections
        assert "single paragraph" in sections["full_text"]

    def test_empty_html_returns_empty(self):
        """Empty input should return empty dict."""
        assert extract_sections("", "10-K") == {}
        assert extract_sections("   ", "10-K") == {}

    def test_sgml_wrapped_html(self):
        """SGML-wrapped HTML should be stripped and sections extracted."""
        sections = extract_sections(SGML_WRAPPED_HTML, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections
        assert "incorporated in Delaware" in sections["item_1"]

    def test_sgml_plain_text(self):
        """SGML-wrapped plain text should use regex fallback."""
        sections = extract_sections(SGML_PLAIN_TEXT, "10-K")
        assert "item_1" in sections
        assert "item_1a" in sections
        assert "item_7" in sections
        assert "cloud computing" in sections["item_1"]

    def test_large_filing_warning(self, caplog):
        """Filings > 5MB should emit a warning."""
        large_html = "<html><body>" + ("x" * (6 * 1024 * 1024)) + "</body></html>"
        with caplog.at_level(logging.WARNING):
            extract_sections(large_html, "10-K")
        assert any("Large filing" in r.message for r in caplog.records)

    def test_unknown_form_type_defaults_to_10k(self):
        """Unknown form types should use 10-K patterns."""
        sections = extract_sections(SIMPLE_10K_HTML, "20-F")
        assert "item_1" in sections
        assert "item_1a" in sections

    def test_10q_sections(self):
        """10-Q should use 10-Q specific patterns."""
        html = """
        <html><body>
        <h2>Item 2. Management's Discussion and Analysis</h2>
        <p>Operating results for the quarter were strong.</p>

        <h2>Item 1A. Risk Factors</h2>
        <p>New risks have emerged this quarter.</p>
        </body></html>
        """
        sections = extract_sections(html, "10-Q")
        assert "part1_item2" in sections
        assert "part2_item1a" in sections


# ---------------------------------------------------------------------------
# TestDetectSgml
# ---------------------------------------------------------------------------


class TestDetectSgml:
    def test_sgml_detected(self):
        """SGML markers in first 2000 chars should be detected."""
        assert _detect_sgml("<DOCUMENT>\n<TYPE>10-K\n<html>...</html>") is True

    def test_html_not_detected(self):
        """Plain HTML without SGML markers should not be detected."""
        assert _detect_sgml("<html><body><p>Hello</p></body></html>") is False

    def test_sec_document_tag(self):
        """<SEC-DOCUMENT> tag should be detected."""
        assert _detect_sgml("<SEC-DOCUMENT>\n<DOCUMENT>...") is True


# ---------------------------------------------------------------------------
# TestStripSgml
# ---------------------------------------------------------------------------


class TestStripSgml:
    def test_strips_sgml_tags(self):
        """SGML header tags like <DOCUMENT>, <TYPE>, etc. should be removed."""
        content = "<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<p>Hello world</p>\n</DOCUMENT>"
        result = _strip_sgml(content)
        assert "<DOCUMENT>" not in result
        assert "<TYPE>" not in result
        assert "<SEQUENCE>" not in result
        assert "<p>Hello world</p>" in result

    def test_preserves_embedded_html(self):
        """HTML content within SGML wrapper should be preserved."""
        content = "<DOCUMENT>\n<TYPE>10-K\n<html><body><h1>Title</h1></body></html>\n</DOCUMENT>"
        result = _strip_sgml(content)
        assert "<html>" in result
        assert "<h1>Title</h1>" in result


# ---------------------------------------------------------------------------
# TestCleanText
# ---------------------------------------------------------------------------


class TestCleanText:
    def test_whitespace_collapse(self):
        """Multiple spaces should collapse to single space."""
        assert _clean_text("hello    world") == "hello world"

    def test_blank_line_collapse(self):
        """3+ blank lines should collapse to 2 newlines."""
        assert _clean_text("a\n\n\n\nb") == "a\n\nb"

    def test_page_number_removal(self):
        """Lines that are just page numbers should be removed."""
        text = "Some text\n42\nMore text\nPage 7\n"
        result = _clean_text(text)
        assert "42" not in result
        assert "Page 7" not in result
        assert "Some text" in result
        assert "More text" in result

    def test_toc_removal(self):
        """'Table of Contents' lines should be removed."""
        text = "Hello\nTable of Contents\nWorld"
        result = _clean_text(text)
        assert "Table of Contents" not in result
        assert "Hello" in result
        assert "World" in result

    def test_nbsp_handling(self):
        """Non-breaking spaces should become regular spaces."""
        text = "hello\u00a0world"
        result = _clean_text(text)
        assert result == "hello world"

    def test_strip_leading_trailing(self):
        """Leading and trailing whitespace should be stripped."""
        assert _clean_text("  \n hello \n  ") == "hello"
