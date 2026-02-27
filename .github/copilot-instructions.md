# Confluence Exporter — Copilot Instructions

## Running the exporter

```bash
pip install -r requirements.txt
python exporter.py
```

## Architecture

This is a two-file tool: **`config.py`** holds all user-editable settings, and **`exporter.py`** contains all logic. There are no other modules or packages.

**Execution flow in `exporter.py`:**
1. `main()` sets up logging (console + `export/export.log`), creates a shared `requests.Session` with the Bearer token, and loads `export/progress.json`.
2. `find_archive_page_ids()` queries Confluence CQL to get IDs of pages matching `ARCHIVE_PAGE_TITLES`.
3. `get_all_pages()` builds a CQL query that excludes archive roots/descendants and `EXCLUDE_TITLE_KEYWORDS`, then paginates through all pages in the space (with `expand=ancestors`).
4. `build_page_paths()` maps each page ID to its mirrored export directory path (e.g. `export/Ancestor/Parent/Page Title/`), using the `\\?\` extended-length prefix on Windows to bypass the 260-char MAX_PATH limit.
5. For each page not already in `progress["completed"]`, `export_page()` calls `export_page_content()` (saves `<sanitized_title>.html`) and `download_attachments()` (saves files into `<page_dir>/attachments/`).
6. Progress is written atomically after every page (write to `.tmp`, then `os.replace`). Failed pages are stored in `progress["failed"]` and retried on the next run.

## Key conventions

- **All configuration lives in `config.py`** — never hardcode values in `exporter.py`. When adding a new configurable behaviour, add the constant to `config.py` with a comment, then import it explicitly in `exporter.py`.
- **Page directory naming:** `safe_name(title)` sanitizes the title (strips unsafe filesystem chars, caps at 80 chars). `build_page_paths()` nests directories to mirror the Confluence ancestor hierarchy — each page's folder is placed inside its parent's folder. Ancestor titles are resolved from the fetched pages list first, then from the API ancestor object, then fall back to the ancestor ID. Page IDs are **not** included in folder or file names. On Windows, paths are prefixed with `\\?\` to bypass the 260-char MAX_PATH limit.
- **HTML filename:** each page is saved as `<sanitized_title>.html` inside its own folder (e.g., `export/Parent/Child/Child.html`).
- **Pagination pattern:** all Confluence API list calls use `limit=50` / `start` offset loop and break when `len(results) < limit`.
- **Attachment filtering** uses two independent checks: MIME type prefix (`EXCLUDE_ATTACHMENT_MIME_PREFIXES`) and file extension (`EXCLUDE_ATTACHMENT_EXTENSIONS`). Both must be consulted when adding new skip logic.
- **Logging levels:** `logging.info` for user-visible progress, `logging.debug` for per-file detail, `logging.warning` for skipped/failed items. Console shows INFO+; the log file captures DEBUG+.
- The `requests.Session` is created once in `main()` and threaded through every function — do not create ad-hoc sessions inside helper functions.
