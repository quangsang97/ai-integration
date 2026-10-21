#!/usr/bin/env python3
"""
Daily job: re-scrape support.optisigns.com, detect new/updated articles and
upload ONLY the delta to the Gemini File Search store (vector store).

State lives in the store itself: every document's display_name is
    "<article url> [id=<article id> h=<12-char content hash>]"
so the job is stateless and works on any ephemeral container (cron job).

Env: GEMINI_API_KEY (required), STORE_DISPLAY_NAME, ARTICLE_LIMIT (0 = all),
     UPLOAD_WORKERS, OUT_DIR, DELETE_REMOVED (1/0)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from google import genai

from scrape import fetch_articles, get_article_slug, to_markdown

STORE_DISPLAY_NAME: str = os.getenv("STORE_DISPLAY_NAME", "optisigns-support")
ARTICLE_LIMIT: int = int(os.getenv("ARTICLE_LIMIT", "30"))
WORKERS: int = int(os.getenv("UPLOAD_WORKERS", "2"))
OUT_DIR: Path = Path(os.getenv("OUT_DIR", "out"))
DELETE_REMOVED: bool = os.getenv("DELETE_REMOVED", "1") == "1"
MAX_TOKENS_PER_CHUNK: int = 400
MAX_OVERLAP_TOKENS: int = 40

TAG_RE: re.Pattern[str] = re.compile(r"\[id=(\d+) h=([0-9a-f]{12})\]\s*$")
log: logging.Logger = logging.getLogger("optibot")


# ---------- Type Definitions ----------
ArticleId = int
ContentHash = str
DocumentName = str
ExistingDoc = tuple[ContentHash, DocumentName]


class Article(TypedDict, total=False):
    id: int
    title: str
    html_url: str
    updated_at: str
    body: str | None
    draft: bool


class DeltaPlan(TypedDict):
    added: list[ArticleId]
    updated: list[ArticleId]
    skipped: list[ArticleId]
    removed: list[ArticleId]
    stale_docs: list[DocumentName]


class RunSummary(TypedDict):
    run_at: str
    store: str
    scraped: int
    added: int
    updated: int
    skipped: int
    removed: int
    failed: int
    files_uploaded: int
    chunks_embedded_estimate: int


# ---------- pure logic (unit-testable) ----------
def content_hash(md: str) -> str:
    return hashlib.sha256(md.encode("utf-8")).hexdigest()[:12]


def make_display_name(url: str, art_id: int, h: str) -> str:
    return f"{url} [id={art_id} h={h}]"


def parse_display_name(name: str | None) -> tuple[int, str] | None:
    m = TAG_RE.search(name or "")
    return (int(m.group(1)), m.group(2)) if m else None


def plan_delta(
    current: dict[ArticleId, ContentHash],
    existing: dict[ArticleId, list[ExistingDoc]],
) -> DeltaPlan:
    """current:  {article_id: hash}
    existing: {article_id: [(hash, doc_name), ...]}   (what is in the store)
    -> dict of lists: added, updated, skipped, removed (ids) and
       stale_docs (doc names to delete: old versions / duplicates / removed)."""
    out: DeltaPlan = {"added": [], "updated": [], "skipped": [], "removed": [], "stale_docs": []}
    for aid, h in current.items():
        docs = existing.get(aid, [])
        same = [d for d in docs if d[0] == h]
        if same:
            out["skipped"].append(aid)
            out["stale_docs"] += [d[1] for d in docs if d is not same[0]]  # dupes/old
        elif docs:
            out["updated"].append(aid)  # old docs deleted AFTER the new one is up
            out["stale_docs"] += [d[1] for d in docs]
        else:
            out["added"].append(aid)
    for aid, docs in existing.items():
        if aid not in current:
            out["removed"].append(aid)
            out["stale_docs"] += [d[1] for d in docs]
    return out


# ---------- Gemini helpers ----------
def get_or_create_store(client: genai.Client) -> str:
    for s in client.file_search_stores.list():
        if s.display_name == STORE_DISPLAY_NAME:
            return s.name
    s = client.file_search_stores.create(config={"display_name": STORE_DISPLAY_NAME})
    log.info("created store %s", s.name)
    return s.name


def list_existing(client: genai.Client, store: str) -> dict[ArticleId, list[ExistingDoc]]:
    existing: dict[ArticleId, list[ExistingDoc]] = {}
    for d in client.file_search_stores.documents.list(parent=store):
        parsed = parse_display_name(d.display_name)
        if parsed:
            existing.setdefault(parsed[0], []).append((parsed[1], d.name))
    return existing


def upload_one(
    client: genai.Client,
    store: str,
    path: Path,
    disp: str,
    retries: int = 3,
) -> bool:
    for attempt in range(1, retries + 1):
        try:
            op = client.file_search_stores.upload_to_file_search_store(
                file=str(path),
                file_search_store_name=store,
                config={
                    "display_name": disp,
                    "mime_type": "text/markdown",
                    "chunking_config": {"white_space_config": {
                        "max_tokens_per_chunk": MAX_TOKENS_PER_CHUNK,
                        "max_overlap_tokens": MAX_OVERLAP_TOKENS}},
                },
            )
            while not op.done:
                time.sleep(3)
                op = client.operations.get(op)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("upload %s failed (try %d/%d): %s", path.name, attempt, retries, e)
            time.sleep(2 * attempt)
    return False


def run(client: genai.Client | None = None) -> RunSummary:
    from google import genai
    from google.genai import types
    client = client or genai.Client(http_options=types.HttpOptions(timeout=120_000))

    OUT_DIR.mkdir(exist_ok=True)
    articles: list[Article] = fetch_articles(ARTICLE_LIMIT)
    id_to_file: dict[int, str] = {a["id"]: f"{get_article_slug(a)}.md" for a in articles}

    docs: dict[int, tuple[Path, str, int]] = {}
    current: dict[int, str] = {}
    for a in articles:
        body = to_markdown(a["body"] or "", id_to_file)
        md = (f"---\ntitle: \"{a['title'].replace(chr(34), chr(39))}\"\n"
              f"source: {a['html_url']}\nupdated_at: {a['updated_at']}\n---\n\n"
              f"# {a['title']}\n\n{body}")
        path = OUT_DIR / id_to_file[a["id"]]
        path.write_text(md, encoding="utf-8")
        h = content_hash(md)
        current[a["id"]] = h
        docs[a["id"]] = (path, make_display_name(a["html_url"], a["id"], h), len(md))

    store = get_or_create_store(client)
    existing = list_existing(client, store)
    plan = plan_delta(current, existing)
    to_upload: list[int] = plan["added"] + plan["updated"]
    log.info("scraped=%d  added=%d updated=%d skipped=%d removed=%d",
             len(articles), len(plan["added"]), len(plan["updated"]),
             len(plan["skipped"]), len(plan["removed"]))

    # 1) upload new versions first (so a failure never leaves an article missing)
    failed: list[int] = []
    with ThreadPoolExecutor(WORKERS) as ex:
        results: list[tuple[int, bool]] = list(
            ex.map(lambda i: (i, upload_one(client, store, docs[i][0], docs[i][1])),
                   to_upload)
        )
    ok: set[int] = {i for i, good in results if good}
    failed = [i for i, good in results if not good]

    # 2) delete stale docs, but only for articles whose new upload succeeded
    #    (+ duplicates of skipped articles, + removed articles if enabled)
    delete: set[str] = set()
    for aid in plan["updated"]:
        if aid in ok:
            delete |= {d[1] for d in existing[aid]}
    for aid in plan["skipped"]:
        keep = [d for d in existing[aid] if d[0] == current[aid]]
        delete |= {d[1] for d in existing[aid] if d is not keep[0]}
    if DELETE_REMOVED:
        for aid in plan["removed"]:
            delete |= {d[1] for d in existing[aid]}
    for name in delete:
        try:
            client.file_search_stores.documents.delete(name=name)
        except Exception as e:  # noqa: BLE001
            log.warning("delete %s failed: %s", name, e)

    est_chunks: int = sum(max(1, math.ceil(docs[i][2] / 4 / (MAX_TOKENS_PER_CHUNK - MAX_OVERLAP_TOKENS)))
                         for i in ok)
    summary: RunSummary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "store": store,
        "scraped": len(articles),
        "added": len([i for i in plan["added"] if i in ok]),
        "updated": len([i for i in plan["updated"] if i in ok]),
        "skipped": len(plan["skipped"]),
        "removed": len(plan["removed"]) if DELETE_REMOVED else 0,
        "failed": len(failed),
        "files_uploaded": len(ok),
        "chunks_embedded_estimate": est_chunks,
    }
    (OUT_DIR / "last_run.json").write_text(json.dumps(summary, indent=2))
    log.info("SUMMARY %s", json.dumps(summary))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        sys.exit("Set GEMINI_API_KEY")
    sys.exit(1 if run()["failed"] else 0)
