import argparse
from pathlib import Path
from scrape import fetch_articles, get_article_slug, to_markdown

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--limit", type=int, default=30)
  ap.add_argument("--out", default="articles")
  args = ap.parse_args()

  out = Path(args.out)
  out.mkdir(exist_ok=True)

  articles = fetch_articles(args.limit)
  id_to_file = {a["id"]: f"{get_article_slug(a)}.md" for a in articles}

  for a in articles:
    body = to_markdown(a["body"] or "", id_to_file)
    # replace " to '
    front = (
      f"---\ntitle: \"{a['title'].replace(chr(34), chr(39))}\"\n"
      f"source: {a['html_url']}\nupdated_at: {a['updated_at']}\n---\n\n"
    )
    (out / id_to_file[a["id"]]).write_text(
      front + f"# {a['title']}\n\n" + body, encoding="utf-8"
    )
  
  print(f"Saved {len(articles)} articles to {out}/")


if __name__ == "__main__":
  main()