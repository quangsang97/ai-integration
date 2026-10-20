import time
import requests

BASE = "https://support.optisigns.com"
API = f"{BASE}/api/v2/help_center/en-us/articles.json"

def fetch_articles(limit):
    articles, url = [], f"{API}?per_page=100"
    
    while url and (limit == 0 or len(articles) < limit):
        for attempt in range(5):
            r = requests.get(url, timeout=30)
            if r.status_code == 429:  # When reached rate limited
                time.sleep(int(r.headers.get("Retry-After", 5)))
                continue
            r.raise_for_status()
            break

        data = r.json()
        articles += [a for a in data["articles"] if not a.get("draft")]
        url = data.get("next_page")
        time.sleep(0.5)
    return articles if limit == 0 else articles[:limit]
