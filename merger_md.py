"""merger_md.py — Convert exported Confluence HTML pages to merged Markdown files.

One .md file is produced per top-level section in the export directory.
Output is written to upload/md/ for upload to Azure AI Search.

Usage:
    python merger_md.py
"""

import logging
import os
import re
import sys

from bs4 import BeautifulSoup, Tag

from config import EXPORT_DIR

UPLOAD_DIR = os.path.join("upload", "md")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    logger.addHandler(console)

# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\n{3,}")

# Maps HTML heading tags to Markdown heading levels (shifted down by 2
# so page content headings start at ### beneath the ## page heading).
_HEADING_MAP = {"h1": "###", "h2": "###", "h3": "####", "h4": "#####", "h5": "#####", "h6": "#####"}


def _convert_node(node) -> str:
    """Recursively convert a BeautifulSoup node to Markdown text."""
    if isinstance(node, str):
        return node

    if not isinstance(node, Tag):
        return ""

    tag = node.name.lower() if node.name else ""

    if tag in _HEADING_MAP:
        text = node.get_text(" ", strip=True)
        return f"\n{_HEADING_MAP[tag]} {text}\n"

    if tag == "p":
        text = node.get_text(" ", strip=True)
        return f"\n{text}\n" if text else ""

    if tag in ("ul", "ol"):
        items = []
        for li in node.find_all("li", recursive=False):
            items.append(f"- {li.get_text(' ', strip=True)}")
        return "\n" + "\n".join(items) + "\n" if items else ""

    if tag == "br":
        return "\n"

    if tag in ("strong", "b"):
        text = node.get_text(" ", strip=True)
        return f"**{text}**" if text else ""

    if tag in ("em", "i"):
        text = node.get_text(" ", strip=True)
        return f"*{text}*" if text else ""

    if tag == "code":
        text = node.get_text("", strip=True)
        return f"`{text}`" if text else ""

    if tag == "pre":
        text = node.get_text("", strip=False)
        return f"\n```\n{text}\n```\n"

    if tag == "a":
        text = node.get_text(" ", strip=True)
        href = node.get("href", "")
        return f"[{text}]({href})" if text else ""

    if tag == "table":
        # Flatten tables to plain text rows
        rows = []
        for row in node.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            rows.append(" | ".join(cells))
        return "\n" + "\n".join(rows) + "\n" if rows else ""

    if tag in ("script", "style", "head"):
        return ""

    # For all other tags, recurse into children
    return "".join(_convert_node(child) for child in node.children)


def html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown, preserving basic structure."""
    soup = BeautifulSoup(html, "html.parser")
    md = _convert_node(soup)
    md = _WHITESPACE.sub("\n\n", md)
    return md.strip()

# ---------------------------------------------------------------------------
# Section collection (identical logic to merger.py)
# ---------------------------------------------------------------------------

def collect_sections(export_dir: str) -> dict:
    """Return a dict mapping section_name -> list of (page_title, html_path)."""
    sections = {}
    scan_dir = export_dir.lstrip("\\\\?\\")

    if not os.path.isdir(scan_dir):
        logging.error("Export directory not found: %s", scan_dir)
        return sections

    for entry in sorted(os.scandir(scan_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        section_name = entry.name
        html_files = []
        for root, _dirs, files in os.walk(entry.path):
            for fname in sorted(files):
                if fname.endswith(".html"):
                    page_title = os.path.splitext(fname)[0]
                    html_files.append((page_title, os.path.join(root, fname)))
        if html_files:
            sections[section_name] = html_files
            logging.debug("Section '%s': %d pages", section_name, len(html_files))

    return sections

# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_section(section_name: str, html_files: list, output_dir: str):
    """Convert and merge all pages in a section into one .md file."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, section_name + ".md")

    parts = [f"# {section_name}\n"]

    for page_title, html_path in html_files:
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            md = html_to_markdown(html)
            parts.append(f"## {page_title}\n\n{md}")
            logging.debug("Converted: %s", html_path)
        except Exception as exc:
            logging.warning("Failed to convert %s — %s", html_path, exc)

    content = "\n\n".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    logging.info("Written: %s (%d pages, %d chars)", out_path, len(html_files), len(content))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    setup_logging()

    export_dir = EXPORT_DIR

    logging.info("Scanning export directory: %s", export_dir)
    sections = collect_sections(export_dir)

    if not sections:
        logging.error("No sections found. Run exporter.py first.")
        sys.exit(1)

    logging.info("Found %d top-level section(s). Writing to %s ...", len(sections), UPLOAD_DIR)

    for section_name, html_files in sections.items():
        merge_section(section_name, html_files, UPLOAD_DIR)

    logging.info("--- Done. %d file(s) written to %s ---", len(sections), UPLOAD_DIR)


if __name__ == "__main__":
    main()
