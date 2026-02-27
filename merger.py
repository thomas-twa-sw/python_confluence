"""merger.py — Convert exported Confluence HTML pages to merged plain-text files.

One .txt file is produced per top-level section in the export directory.
Output is written to upload/txt/ for upload to Azure AI Search.

Usage:
    python merger.py
"""

import logging
import os
import re
import sys

from bs4 import BeautifulSoup

from config import EXPORT_DIR

UPLOAD_DIR = os.path.join("upload", "txt")

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


def html_to_text(html: str) -> str:
    """Strip HTML tags and collapse excessive whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    # Collapse 3+ consecutive newlines to 2
    text = _WHITESPACE.sub("\n\n", text)
    return text.strip()

# ---------------------------------------------------------------------------
# Section collection
# ---------------------------------------------------------------------------

def collect_sections(export_dir: str) -> dict:
    """Return a dict mapping section_name -> list of (page_title, html_path).

    Sections are the immediate subdirectories of export_dir.
    Pages within each section are collected recursively and sorted by path
    so that parent pages appear before their children.
    """
    sections = {}
    # Strip extended-length prefix if present for os.scandir compatibility
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
    """Convert and merge all pages in a section into one .txt file."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, section_name + ".txt")

    parts = []
    for page_title, html_path in html_files:
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            text = html_to_text(html)
            parts.append(f"=== Page: {page_title} ===\n\n{text}")
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

    # Strip extended-length prefix for display; use plain path for I/O
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
