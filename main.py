import time
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx
import json
import os
from bs4 import BeautifulSoup
load_dotenv()

api_key = os.getenv("SERPER_API_KEY")
if not api_key:
    raise ValueError("SERPER_API_KEY environment variable is required")

mcp = FastMCP("docs")

USER_AGENT = "docs-app/1.0"
SERPER_URL="https://google.serper.dev/search"

# Rate limiting: max requests per minute for fetch_url
_FETCH_RATE_LIMIT = int(os.getenv("FETCH_RATE_LIMIT", "20"))
_fetch_timestamps: list[float] = []


def _check_fetch_rate() -> bool:
    """Return True if the request is allowed under the rate limit."""
    now = time.time()
    _fetch_timestamps[:] = [t for t in _fetch_timestamps if now - t < 60]
    if len(_fetch_timestamps) >= _FETCH_RATE_LIMIT:
        return False
    _fetch_timestamps.append(now)
    return True

docs_urls = {
    "langchain": "python.langchain.com/docs",
    "llama-index": "docs.llamaindex.ai/en/stable",
    "openai": "platform.openai.com/docs",
}

async def search_web(query: str) -> dict | None:
    payload = json.dumps({"q": query, "num": 2})

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                SERPER_URL, headers=headers, data=payload, timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            return {"organic": []}
  
# Allowed domains for fetch_url (only fetch from known doc sites)
ALLOWED_DOMAINS = {urlparse("https://" + v).netloc for v in docs_urls.values()}


def _is_allowed_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS)


async def fetch_url(url: str):
  if not _is_allowed_domain(url):
      return f"Blocked: {urlparse(url).netloc} is not in the allowed domain list"
  if not _check_fetch_rate():
      return "Rate limit exceeded for URL fetching"
  async with httpx.AsyncClient() as client:
        try:
            current_url = url
            for _ in range(5):
                response = await client.get(current_url, timeout=30.0, follow_redirects=False)
                if response.is_redirect:
                    current_url = str(response.next_request.url)
                    if not _is_allowed_domain(current_url):
                        return f"Blocked: redirect to {urlparse(current_url).netloc} is not allowed"
                    continue
                break
            soup = BeautifulSoup(response.text, "html.parser")
            text = soup.get_text()
            return text
        except httpx.TimeoutException:
            return "Timeout error"

@mcp.tool()  
async def get_docs(query: str, library: str):
  """
  Search the latest docs for a given query and library.
  Supports langchain, openai, and llama-index.

  Args:
    query: The query to search for (e.g. "Chroma DB")
    library: The library to search in (e.g. "langchain")

  Returns:
    Text from the docs
  """
  if library not in docs_urls:
    raise ValueError(f"Library {library} not supported by this tool")
  
  query = f"site:{docs_urls[library]} {query}"
  results = await search_web(query)
  if not results or "organic" not in results or len(results["organic"]) == 0:
    return "No results found"
  
  text = ""
  for result in results["organic"]:
    text += await fetch_url(result["link"])
  text = text[:50000]
  return text


if __name__ == "__main__":
    mcp.run(transport="stdio")
