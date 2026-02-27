import json
import logging
import os
import re
import sys

import requests

from config import (
    ARCHIVE_PAGE_TITLES,
    ATTACHMENT_CHUNK_SIZE,
    BASE_URL,
    BEARER_TOKEN,
    EXCLUDE_ATTACHMENT_EXTENSIONS,
    EXCLUDE_ATTACHMENT_MIME_PREFIXES,
    EXCLUDE_TITLE_KEYWORDS,
    EXPORT_DIR,
    REQUEST_TIMEOUT,
    SPACE_KEY,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    log_file = os.path.join(EXPORT_DIR, "export.log")

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console)
    logger.addHandler(file_handler)

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {BEARER_TOKEN}"})
    return session

# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

PROGRESS_FILE = os.path.join(EXPORT_DIR, "progress.json")


def load_progress() -> dict:
    if not os.path.exists(PROGRESS_FILE):
        return {"completed": set(), "failed": {}}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "completed": set(data.get("completed", [])),
        "failed": data.get("failed", {}),
    }


def save_progress(progress: dict):
    data = {
        "completed": sorted(progress["completed"]),
        "failed": progress["failed"],
    }
    # Write to a temp file first, then rename for atomicity
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PROGRESS_FILE)

# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(title: str) -> str:
    sanitized = _UNSAFE_CHARS.sub("_", title).strip(". ")
    return sanitized[:80]  # cap length to avoid OS path-length issues

# ---------------------------------------------------------------------------
# Hierarchy path builder
# ---------------------------------------------------------------------------

def build_page_paths(pages: list) -> dict:
    """Return a dict mapping page_id → absolute export directory path.

    The path mirrors the Confluence ancestor hierarchy:
        export/Ancestor/.../Parent/Page Title/

    Uses the \\?\ extended-length path prefix on Windows to bypass the
    260-character MAX_PATH limit for deeply nested hierarchies.

    Ancestor titles are resolved from the fetched pages list first (most
    reliable), then from whatever the API returned on the ancestor object,
    and finally fall back to the ancestor ID to avoid dropping path segments.
    """
    id_to_title = {p["id"]: p["title"] for p in pages}
    # Extended-length prefix bypasses Windows 260-char MAX_PATH limit
    base = "\\\\?\\" + os.path.abspath(EXPORT_DIR)
    id_to_path = {}
    for page in pages:
        parts = []
        for ancestor in page.get("ancestors", []):
            ancestor_id = ancestor.get("id", "")
            title = (
                id_to_title.get(ancestor_id)
                or ancestor.get("title")
                or ancestor_id
            )
            if title:
                parts.append(safe_name(title))
        parts.append(safe_name(page["title"]))
        id_to_path[page["id"]] = os.path.join(base, *parts)
    return id_to_path


# ---------------------------------------------------------------------------
# Confluence API helpers
# ---------------------------------------------------------------------------

def find_archive_page_ids(session: requests.Session) -> set:
    """Return IDs of pages whose titles match ARCHIVE_PAGE_TITLES."""
    if not ARCHIVE_PAGE_TITLES:
        return set()

    # Build a CQL title filter:  title in ("Archive", "Old stuff")
    titles_cql = ", ".join(f'"{t}"' for t in ARCHIVE_PAGE_TITLES)
    cql = f'space = "{SPACE_KEY}" AND type = page AND title in ({titles_cql})'

    ids = set()
    limit = 50
    start = 0
    url = f"{BASE_URL}/rest/api/content/search"

    while True:
        resp = session.get(url, params={"cql": cql, "limit": limit, "start": start}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for page in results:
            ids.add(page["id"])
            logging.info("Archive root found: '%s' (id=%s)", page["title"], page["id"])
        if len(results) < limit:
            break
        start += limit

    return ids


def get_all_pages(session: requests.Session, archive_ids: set) -> list:
    """Fetch every non-archived page in the space using CQL, handling pagination."""
    pages = []
    limit = 50
    start = 0

    # Base CQL: all pages in this space
    cql = f'space = "{SPACE_KEY}" AND type = page'

    # Exclude archive roots and everything beneath them
    if archive_ids:
        ids_list = ", ".join(archive_ids)
        cql += f" AND ancestor not in ({ids_list}) AND id not in ({ids_list})"
        logging.info("Excluding %d archive root(s) and their descendants.", len(archive_ids))

    # Exclude pages whose titles contain any of the configured keywords
    if EXCLUDE_TITLE_KEYWORDS:
        keyword_clauses = " OR ".join(f'title ~ "{kw}"' for kw in EXCLUDE_TITLE_KEYWORDS)
        cql += f" AND NOT ({keyword_clauses})"
        logging.info("Excluding pages with title keywords: %s", EXCLUDE_TITLE_KEYWORDS)

    logging.info("Discovering pages in space '%s'...", SPACE_KEY)
    url = f"{BASE_URL}/rest/api/content/search"

    while True:
        resp = session.get(url, params={"cql": cql, "limit": limit, "start": start, "expand": "ancestors"}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        results = resp.json().get("results", [])
        pages.extend(results)

        logging.debug("Fetched %d pages (start=%d)", len(results), start)

        if len(results) < limit:
            break
        start += limit

    logging.info("Total pages found: %d", len(pages))
    return pages


def export_page_content(session: requests.Session, page_id: str, title: str, page_dir: str):
    """Download the HTML export view of a page and save it."""
    url = f"{BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "body.export_view"}
    resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    html = resp.json()["body"]["export_view"]["value"]
    html_path = os.path.join(page_dir, safe_name(title) + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    logging.debug("Saved HTML: %s", html_path)


def download_attachments(session: requests.Session, page_id: str, page_dir: str):
    """Download all attachments for a page, with pagination and resume support."""
    att_dir = os.path.join(page_dir, "attachments")
    os.makedirs(att_dir, exist_ok=True)

    limit = 50
    start = 0

    while True:
        url = f"{BASE_URL}/rest/api/content/{page_id}/child/attachment"
        params = {"limit": limit, "start": start}
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        results = resp.json().get("results", [])

        for att in results:
            filename = _UNSAFE_CHARS.sub("_", att["title"])
            dest = os.path.join(att_dir, filename)

            media_type = att.get("metadata", {}).get("mediaType", "")
            if EXCLUDE_ATTACHMENT_MIME_PREFIXES and any(media_type.startswith(p) for p in EXCLUDE_ATTACHMENT_MIME_PREFIXES):
                logging.debug("Skipping attachment (type=%s): %s", media_type, filename)
                continue

            ext = os.path.splitext(filename)[1].lower()
            if EXCLUDE_ATTACHMENT_EXTENSIONS and ext in {e.lower() for e in EXCLUDE_ATTACHMENT_EXTENSIONS}:
                logging.debug("Skipping attachment (ext=%s): %s", ext, filename)
                continue

            if os.path.exists(dest):
                logging.debug("Skipping existing attachment: %s", filename)
                continue

            download_path = att["_links"]["download"]
            file_url = f"{BASE_URL}{download_path}"

            logging.debug("Downloading attachment: %s", filename)
            with session.get(file_url, stream=True, timeout=REQUEST_TIMEOUT) as dl:
                dl.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=ATTACHMENT_CHUNK_SIZE):
                        f.write(chunk)
            logging.debug("Saved attachment: %s", dest)

        if len(results) < limit:
            break
        start += limit

# ---------------------------------------------------------------------------
# Per-page export orchestration
# ---------------------------------------------------------------------------

def export_page(session: requests.Session, page: dict, page_dir: str, progress: dict, index: int, total: int):
    page_id = page["id"]
    title = page["title"]

    # Remove from failed so a retry is attempted cleanly
    progress["failed"].pop(page_id, None)

    logging.info("[%d/%d] Exporting: %s (id=%s)", index, total, title, page_id)

    try:
        os.makedirs(page_dir, exist_ok=True)

        export_page_content(session, page_id, title, page_dir)
        download_attachments(session, page_id, page_dir)

        progress["completed"].add(page_id)
        save_progress(progress)
        logging.debug("Completed page id=%s", page_id)

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logging.warning("Failed page id=%s title=%r — %s", page_id, title, msg)
        progress["failed"][page_id] = msg
        save_progress(progress)

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    setup_logging()
    session = make_session()
    progress = load_progress()

    already_done = len(progress["completed"])
    if already_done:
        logging.info("Resuming: %d pages already completed.", already_done)

    try:
        archive_ids = find_archive_page_ids(session)
        pages = get_all_pages(session, archive_ids)
    except Exception as exc:
        logging.error("Failed to fetch page list: %s", exc)
        sys.exit(1)

    total = len(pages)
    page_paths = build_page_paths(pages)
    skipped = 0
    exported = 0

    for i, page in enumerate(pages, start=1):
        page_id = page["id"]
        if page_id in progress["completed"]:
            skipped += 1
            logging.debug("Skipping completed page id=%s", page_id)
            continue

        export_page(session, page, page_paths[page_id], progress, i, total)
        exported += 1

    # Summary
    failed_count = len(progress["failed"])
    logging.info("--- Export complete ---")
    logging.info("  Exported : %d", exported)
    logging.info("  Skipped  : %d (already done)", skipped)
    logging.info("  Failed   : %d", failed_count)

    if progress["failed"]:
        logging.warning("Failed pages (will be retried on next run):")
        for pid, reason in progress["failed"].items():
            logging.warning("  id=%s  %s", pid, reason)


if __name__ == "__main__":
    main()
