import math
import re
import sys
import time
from pathlib import Path
 
from google import genai
from google.genai import types

ARTICLES_DIR = Path("articles")
STORE_FILE = Path(".store_name")
MODEL = "gemini-3.8-flash"

# Chunking strategy (explain this in your README):
# ~400-token chunks with 40-token overlap. Articles are short, step-by-step
# how-tos, so small chunks keep one procedure/FAQ answer per chunk and the
# overlap stops a step list being cut mid-sentence.
MAX_TOKENS_PER_CHUNK = 400
MAX_OVERLAP_TOKENS = 40

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

client = genai.Client()

def source_url(md: str, fallback: str) -> str:
    m = re.search(r"^source:\s*(\S+)", md, re.M)
    return m.group(1) if m else fallback

def upload():
    files = sorted(ARTICLES_DIR.glob("*.md"))
    if not files:
        sys.exit(f"No .md files in {ARTICLES_DIR}/")
 
    store = client.file_search_stores.create(config={"display_name": "optisigns-support"})
    STORE_FILE.write_text(store.name)
    print(f"Created store {store.name}")
 
    ops, est_chunks = [], 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        # display_name shows up as the citation title -> use the article URL
        op = client.file_search_stores.upload_to_file_search_store(
            file=str(f),
            file_search_store_name=store.name,
            config={
                "display_name": source_url(text, f.name),
                "mime_type": "text/markdown",
                "chunking_config": {
                    "white_space_config": {
                        "max_tokens_per_chunk": MAX_TOKENS_PER_CHUNK,
                        "max_overlap_tokens": MAX_OVERLAP_TOKENS,
                    }
                },
            },
        )
        ops.append((f.name, op))
        # rough estimate: ~4 chars/token (the API doesn't report chunk counts)
        est_chunks += max(1, math.ceil(len(text) / 4 / (MAX_TOKENS_PER_CHUNK - MAX_OVERLAP_TOKENS)))
 
    for name, op in ops:                  # wait for indexing to finish
        while not op.done:
            time.sleep(3)
            op = client.operations.get(op)
        print(f"  indexed {name}")
 
    print(f"\nFiles uploaded: {len(files)}")
    print(f"Chunks embedded (estimated): ~{est_chunks}")


def ask(question: str):
    print("asking")
    store_name = STORE_FILE.read_text().strip()
    resp = client.models.generate_content(
        model=MODEL,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
        ),
    )
    print(resp.text)
    gm = resp.candidates[0].grounding_metadata
    urls = []
    for c in (gm.grounding_chunks if gm else []) or []:
        if c.retrieved_context and c.retrieved_context.title not in urls:
            urls.append(c.retrieved_context.title)
    print("\nSources retrieved:")
    for u in urls[:3]:
        print("Article URL:", u)
 
 
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "upload":
        upload()
    elif len(sys.argv) >= 3 and sys.argv[1] == "ask":
        ask(" ".join(sys.argv[2:]))
    else:
        sys.exit(__doc__)