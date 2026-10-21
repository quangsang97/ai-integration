from __future__ import annotations

import time
from typing import Any, TypedDict

import requests

BASE: str = "https://support.optisigns.com"
API: str = f"{BASE}/api/v2/help_center/en-us/articles.json"


class Article(TypedDict, total=False):
    id: int
    title: str
    html_url: str
    updated_at: str
    body: str | None
    draft: bool


def fetch_articles(limit: int = 0) -> list[Article]:
    articles: list[Article] = []
    url: str | None = f"{API}?per_page=100"

    while url and (limit == 0 or len(articles) < limit):
        r: requests.Response | None = None
        for attempt in range(5):
            r = requests.get(url, timeout=30)
            if r.status_code == 429:  # When reached rate limited
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            break

        if r is None:
            break

        data: dict[str, Any] = r.json()
        raw_articles: list[Article] = data.get("articles", [])
        articles += [a for a in raw_articles if not a.get("draft")]
        url = data.get("next_page")
        time.sleep(0.5)
    return articles if limit == 0 else articles[:limit]

