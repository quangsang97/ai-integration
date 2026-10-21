from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from scrape import Article, fetch_articles, get_article_slug, to_markdown


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", default="articles")
    args = ap.parse_args()

    out: Path = Path(args.out)
    out.mkdir(exist_ok=True)

    articles: list[Article] = fetch_articles(args.limit)
    id_to_file: dict[int, str] = {a["id"]: f"{get_article_slug(a)}.md" for a in articles}

    for a in articles:
        body: str = to_markdown(a["body"] or "", id_to_file)
        # replace " to '
        front: str = (
            f"---\ntitle: \"{a['title'].replace(chr(34), chr(39))}\"\n"
            f"source: {a['html_url']}\nupdated_at: {a['updated_at']}\n---\n\n"
        )
        (out / id_to_file[a["id"]]).write_text(
            front + f"# {a['title']}\n\n" + body, encoding="utf-8"
        )

    print(f"Saved {len(articles)} articles to {out}/")


if __name__ == "__main__":
    main()
