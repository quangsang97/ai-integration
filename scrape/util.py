import re
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

BASE = "https://support.optisigns.com"
ARTICLE_ID_RE = re.compile(r"/hc/[\w-]+/articles/(\d+)")

class Converter(MarkdownConverter):
    """Fenced code blocks; keep image alt text."""
 
    def convert_pre(self, el, text, parent_tags=None, **kw):
        code = el.get_text()
        return f"\n\n```\n{code.strip(chr(10))}\n```\n\n"
    
def get_article_slug(article):
    # html_url looks like .../articles/48626115821459-How-to-Use-the-YouTube-Dashboard-App
    tail = article["html_url"].rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"^\d+-?", "", tail).lower() or str(article["id"])
    return f"{slug}-{article['id']}"  # id suffix guarantees uniqueness

def clean_html(html, id_to_file):
    soup = BeautifulSoup(html, "html.parser")
 
    # Remove empty <a name="..."></a> anchors used by the in-page TOC
    for a in soup.find_all("a"):
        if not a.get("href") and not a.get_text(strip=True):
            a.decompose()
 
    # Remove the in-article table of contents (lists made only of #anchors)
    for ul in soup.find_all("ul"):
        links = ul.find_all("a")
        if links and all((l.get("href") or "").startswith("#") for l in links) \
                and len(ul.get_text(strip=True)) == sum(len(l.get_text(strip=True)) for l in links):
            ul.decompose()
 
    # Unwrap styling-only spans, drop scripts/styles/iframes
    for t in soup(["script", "style", "iframe"]):
        t.decompose()
    for span in soup.find_all("span"):
        span.unwrap()
 
    # Links: article links -> relative ./<slug>.md when we have that file,
    # other site-internal links -> root-relative path, external left alone.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(BASE):
            href = href[len(BASE):] or "/"
        m = ARTICLE_ID_RE.search(href)
        if m and int(m.group(1)) in id_to_file:
            href = f"./{id_to_file[int(m.group(1))]}"
        a["href"] = href
    for img in soup.find_all("img", src=True):
        if img["src"].startswith("/"):
            img["src"] = BASE + img["src"]  # keep images loadable
    return str(soup)

def to_markdown(html, id_to_file):
    md = Converter(heading_style="ATX", bullets="-", strip=["figure"]).convert(
        clean_html(html, id_to_file)
    )
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"