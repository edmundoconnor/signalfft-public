"""Pure parsing logic for SEC filing HTML → named text sections.

No AWS dependencies — all I/O happens in service.py.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section heading patterns per form type
# ---------------------------------------------------------------------------

SECTION_PATTERNS_10K: dict[str, re.Pattern] = {
    "item_1": re.compile(r"item\s+1[\.\s:]+business", re.IGNORECASE),
    "item_1a": re.compile(r"item\s+1a[\.\s:]+risk\s+factors", re.IGNORECASE),
    "item_1b": re.compile(r"item\s+1b[\.\s:]+unresolved\s+staff", re.IGNORECASE),
    "item_2": re.compile(r"item\s+2[\.\s:]+properties", re.IGNORECASE),
    "item_3": re.compile(r"item\s+3[\.\s:]+legal\s+proceedings", re.IGNORECASE),
    "item_4": re.compile(r"item\s+4[\.\s:]+mine\s+safety", re.IGNORECASE),
    "item_5": re.compile(r"item\s+5[\.\s:]+market\s+for", re.IGNORECASE),
    "item_6": re.compile(r"item\s+6[\.\s:]+", re.IGNORECASE),
    "item_7": re.compile(r"item\s+7[\.\s:]+management", re.IGNORECASE),
    "item_7a": re.compile(r"item\s+7a[\.\s:]+quantitative", re.IGNORECASE),
    "item_8": re.compile(r"item\s+8[\.\s:]+financial\s+statements", re.IGNORECASE),
    "item_9": re.compile(r"item\s+9[\.\s:]+changes\s+in", re.IGNORECASE),
    "item_9a": re.compile(r"item\s+9a[\.\s:]+controls", re.IGNORECASE),
    "item_10": re.compile(r"item\s+10[\.\s:]+directors", re.IGNORECASE),
    "item_11": re.compile(r"item\s+11[\.\s:]+executive\s+compensation", re.IGNORECASE),
    "item_12": re.compile(r"item\s+12[\.\s:]+security\s+ownership", re.IGNORECASE),
    "item_13": re.compile(r"item\s+13[\.\s:]+certain\s+relationships", re.IGNORECASE),
    "item_14": re.compile(r"item\s+14[\.\s:]+principal\s+account", re.IGNORECASE),
    "item_15": re.compile(r"item\s+15[\.\s:]+exhibits", re.IGNORECASE),
}

SECTION_PATTERNS_10Q: dict[str, re.Pattern] = {
    "part1_item1": re.compile(r"item\s+1[\.\s:]+financial\s+statements", re.IGNORECASE),
    "part1_item2": re.compile(r"item\s+2[\.\s:]+management.+discussion", re.IGNORECASE),
    "part1_item3": re.compile(r"item\s+3[\.\s:]+quantitative", re.IGNORECASE),
    "part1_item4": re.compile(r"item\s+4[\.\s:]+controls", re.IGNORECASE),
    "part2_item1": re.compile(r"item\s+1[\.\s:]+legal\s+proceedings", re.IGNORECASE),
    "part2_item1a": re.compile(r"item\s+1a[\.\s:]+risk\s+factors", re.IGNORECASE),
    "part2_item2": re.compile(r"item\s+2[\.\s:]+unregistered", re.IGNORECASE),
    "part2_item3": re.compile(r"item\s+3[\.\s:]+defaults", re.IGNORECASE),
    "part2_item4": re.compile(r"item\s+4[\.\s:]+mine\s+safety", re.IGNORECASE),
    "part2_item5": re.compile(r"item\s+5[\.\s:]+other\s+information", re.IGNORECASE),
    "part2_item6": re.compile(r"item\s+6[\.\s:]+exhibits", re.IGNORECASE),
}

SECTION_PATTERNS_8K: dict[str, re.Pattern] = {
    "item_1_01": re.compile(r"item\s+1\.01", re.IGNORECASE),
    "item_1_02": re.compile(r"item\s+1\.02", re.IGNORECASE),
    "item_1_03": re.compile(r"item\s+1\.03", re.IGNORECASE),
    "item_2_01": re.compile(r"item\s+2\.01", re.IGNORECASE),
    "item_2_02": re.compile(r"item\s+2\.02", re.IGNORECASE),
    "item_2_03": re.compile(r"item\s+2\.03", re.IGNORECASE),
    "item_2_04": re.compile(r"item\s+2\.04", re.IGNORECASE),
    "item_2_05": re.compile(r"item\s+2\.05", re.IGNORECASE),
    "item_2_06": re.compile(r"item\s+2\.06", re.IGNORECASE),
    "item_3_01": re.compile(r"item\s+3\.01", re.IGNORECASE),
    "item_3_02": re.compile(r"item\s+3\.02", re.IGNORECASE),
    "item_3_03": re.compile(r"item\s+3\.03", re.IGNORECASE),
    "item_4_01": re.compile(r"item\s+4\.01", re.IGNORECASE),
    "item_4_02": re.compile(r"item\s+4\.02", re.IGNORECASE),
    "item_5_01": re.compile(r"item\s+5\.01", re.IGNORECASE),
    "item_5_02": re.compile(r"item\s+5\.02", re.IGNORECASE),
    "item_5_03": re.compile(r"item\s+5\.03", re.IGNORECASE),
    "item_5_04": re.compile(r"item\s+5\.04", re.IGNORECASE),
    "item_5_05": re.compile(r"item\s+5\.05", re.IGNORECASE),
    "item_5_06": re.compile(r"item\s+5\.06", re.IGNORECASE),
    "item_5_07": re.compile(r"item\s+5\.07", re.IGNORECASE),
    "item_5_08": re.compile(r"item\s+5\.08", re.IGNORECASE),
    "item_7_01": re.compile(r"item\s+7\.01", re.IGNORECASE),
    "item_8_01": re.compile(r"item\s+8\.01", re.IGNORECASE),
    "item_9_01": re.compile(r"item\s+9\.01", re.IGNORECASE),
}

FORM_TYPE_PATTERNS: dict[str, dict[str, re.Pattern]] = {
    "10-K": SECTION_PATTERNS_10K,
    "10-Q": SECTION_PATTERNS_10Q,
    "8-K": SECTION_PATTERNS_8K,
}

_LARGE_FILING_BYTES = 5 * 1024 * 1024  # 5 MB

# SGML marker tags found in SEC full-submission text files
_SGML_MARKERS = ("<DOCUMENT>", "<TYPE>", "<SEQUENCE>", "<SEC-DOCUMENT>")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_sections(html_content: str, form_type: str) -> dict[str, str]:
    """Parse filing HTML into named text sections.

    Returns a dict of ``{section_name: cleaned_text}``.
    Falls back to ``{"full_text": ...}`` if fewer than 2 sections are found.
    """
    if not html_content or not html_content.strip():
        return {}

    if len(html_content) > _LARGE_FILING_BYTES:
        logger.warning(
            "Large filing (%d bytes, %.1f MB) — parsing may be slow",
            len(html_content),
            len(html_content) / (1024 * 1024),
        )

    patterns = FORM_TYPE_PATTERNS.get(form_type, SECTION_PATTERNS_10K)

    if _detect_sgml(html_content):
        content = _strip_sgml(html_content)
        # After stripping SGML, content might be HTML or plain text
        if "<html" in content.lower() or "<body" in content.lower() or "<div" in content.lower():
            sections = _html_to_sections(content, patterns)
        else:
            sections = _extract_sections_from_text(content, patterns)
    else:
        sections = _html_to_sections(html_content, patterns)

    if len(sections) < 2:
        # Fallback: return entire document as a single section
        if _detect_sgml(html_content):
            text = _strip_sgml(html_content)
        else:
            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator="\n")
        cleaned = _clean_text(text)
        if cleaned:
            return {"full_text": cleaned}
        return {}

    return sections


# ---------------------------------------------------------------------------
# SGML detection & stripping
# ---------------------------------------------------------------------------

def _detect_sgml(content: str) -> bool:
    """Check the first 2000 chars for SEC SGML markers."""
    header = content[:2000].upper()
    return any(marker in header for marker in _SGML_MARKERS)


def _strip_sgml(content: str) -> str:
    """Remove SGML wrapper tags and extract the embedded document content."""
    # Remove SGML header/footer tags
    lines = content.split("\n")
    output_lines: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip().upper()
        # Skip SGML-only lines like <DOCUMENT>, <TYPE>xxx, <SEQUENCE>xxx, etc.
        if stripped.startswith(("<DOCUMENT>", "</DOCUMENT>", "<TYPE>", "<SEQUENCE>",
                                "<FILENAME>", "<DESCRIPTION>", "<SEC-DOCUMENT>",
                                "</SEC-DOCUMENT>", "<SEC-HEADER>", "</SEC-HEADER>")):
            continue
        # Skip the SEC header block
        if stripped == "<SEC-HEADER>":
            skip = True
            continue
        if stripped == "</SEC-HEADER>":
            skip = False
            continue
        if skip:
            continue
        output_lines.append(line)
    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# HTML-based section extraction
# ---------------------------------------------------------------------------

def _html_to_sections(html: str, patterns: dict[str, re.Pattern]) -> dict[str, str]:
    """Extract sections from HTML using BeautifulSoup heading detection."""
    soup = BeautifulSoup(html, "html.parser")

    # Collect candidate headings from multiple tag strategies
    headings: list[tuple[str, object]] = []  # (section_name, element)

    # Strategy 1: h1-h6 tags
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(strip=True)
        name = _match_heading(text, patterns)
        if name:
            headings.append((name, tag))

    # Strategy 2: bold/strong tags containing Item patterns (< 200 chars)
    if not headings:
        for tag in soup.find_all(["b", "strong"]):
            text = tag.get_text(strip=True)
            if len(text) > 200:
                continue
            name = _match_heading(text, patterns)
            if name:
                headings.append((name, tag))

    # Strategy 3: p tags starting with Item patterns (< 200 chars)
    if not headings:
        for tag in soup.find_all("p"):
            text = tag.get_text(strip=True)
            if len(text) > 200:
                continue
            name = _match_heading(text, patterns)
            if name:
                headings.append((name, tag))

    if not headings:
        return {}

    # Deduplicate: keep first occurrence of each section name
    seen: set[str] = set()
    unique_headings: list[tuple[str, object]] = []
    for name, tag in headings:
        if name not in seen:
            seen.add(name)
            unique_headings.append((name, tag))
    headings = unique_headings

    # Build a set of heading tag identities for fast stop detection
    heading_tag_ids = {id(tag) for _, tag in headings}

    # Extract text between consecutive headings using document-order traversal
    sections: dict[str, str] = {}
    for i, (name, tag) in enumerate(headings):
        next_tag = headings[i + 1][1] if i + 1 < len(headings) else None
        texts: list[str] = []

        for elem in tag.next_elements:
            # Skip children of the heading tag itself
            if elem is tag:
                continue
            # Check if elem is inside the heading tag
            if hasattr(elem, 'parent') and elem.parent is tag:
                continue
            # Stop when we reach the next heading tag
            if next_tag is not None and elem is next_tag:
                break
            # Also stop if we hit any other heading tag (guard against nesting)
            if id(elem) in heading_tag_ids:
                break
            # Only collect NavigableString (text nodes), not Tag objects
            if isinstance(elem, str) and elem.strip():
                texts.append(elem)

        cleaned = _clean_text("\n".join(texts))
        if cleaned:
            sections[name] = cleaned

    return sections


def _match_heading(text: str, patterns: dict[str, re.Pattern]) -> str | None:
    """Match heading text against section patterns, return section name or None."""
    for name, pattern in patterns.items():
        if pattern.search(text):
            return name
    return None


# ---------------------------------------------------------------------------
# Text-based section extraction (SGML fallback)
# ---------------------------------------------------------------------------

def _extract_sections_from_text(text: str, patterns: dict[str, re.Pattern]) -> dict[str, str]:
    """Extract sections from plain text using regex line matching."""
    lines = text.split("\n")
    matches: list[tuple[str, int]] = []  # (section_name, line_index)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 200:
            continue
        name = _match_heading(stripped, patterns)
        if name and name not in {m[0] for m in matches}:
            matches.append((name, i))

    if not matches:
        return {}

    sections: dict[str, str] = {}
    for i, (name, start) in enumerate(matches):
        end = matches[i + 1][1] if i + 1 < len(matches) else len(lines)
        section_text = "\n".join(lines[start + 1:end])
        cleaned = _clean_text(section_text)
        if cleaned:
            sections[name] = cleaned

    return sections


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

_PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?\d+\s*$", re.IGNORECASE)
_TOC_RE = re.compile(r"^\s*table\s+of\s+contents\s*$", re.IGNORECASE)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_NBSP = "\u00a0"


def _clean_text(text: str) -> str:
    """Normalize whitespace, remove page numbers, TOC lines, and collapse blank lines."""
    # Replace nbsp with regular space
    text = text.replace(_NBSP, " ")

    lines = text.split("\n")
    cleaned_lines: list[str] = []
    for line in lines:
        # Remove page number lines
        if _PAGE_NUMBER_RE.match(line):
            continue
        # Remove "Table of Contents" lines
        if _TOC_RE.match(line):
            continue
        # Normalize horizontal whitespace within each line
        cleaned_lines.append(" ".join(line.split()))

    result = "\n".join(cleaned_lines)
    # Collapse 3+ consecutive newlines to 2
    result = _MULTI_BLANK_RE.sub("\n\n", result)
    return result.strip()
