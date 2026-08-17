"""Reading a SearXNG instance the user runs themselves.

Aura has never had web search, and the reason was honest: a general search
engine means an API key, and Aura holds no credentials. A SearXNG on localhost
answers that without changing the answer — the search index belongs to a service
the user started, on their own machine, and Aura only reads it.

Three properties are worth stating, because they are what make this safe rather
than merely convenient:

* **Snippets only.** This module returns titles, links, and the excerpt the
  engine already produced. It never opens a result page. That is not a promise
  about behaviour — every result URL is an ungranted domain, so `_http_get`
  would refuse it. The restriction is enforced by the permission model that was
  already there.
* **Aura did not read what she links to.** The reply may cite the search, and
  must not claim to have read the pages behind it. `NOT_READ` says so in the
  tool result, because a model that quietly upgrades "a snippet said" into "I
  read that page" is the same inversion as a failed task reporting success.
* **The text is untrusted.** Snippets come from the open web and can contain
  anything, including text shaped like instructions. It is data. It is capped,
  stripped of control characters, and labelled.

Standard library only, like the rest of Aura, and no network of its own: the
caller passes in a fetch that already goes through the permission checks.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlparse

#: SearXNG's own documented default. Nothing runs here until the user starts it.
DEFAULT_ENDPOINT = "http://localhost:8888"

MAX_RESULTS = 8
SNIPPET_CHARS = 300
TITLE_CHARS = 140

#: Repeated in the tool result so the model cannot mistake a snippet for a page.
NOT_READ = ("These are search snippets. I did not open any of these pages, so do "
            "not describe their contents as if I had read them.")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE = re.compile(r"\s+")


class SearchUnavailable(RuntimeError):
    """The search service could not be used, with something actionable to say."""


def _clean(value: object, limit: int) -> str:
    text = _SPACE.sub(" ", _CONTROL.sub(" ", str(value or ""))).strip()
    return text[:limit].strip()


def endpoint_of(configured: object) -> str:
    """Normalise the configured endpoint, or refuse it with a reason."""
    endpoint = str(configured or "").strip().rstrip("/")
    if not endpoint:
        raise SearchUnavailable(
            "No search service is configured. Set the SearXNG address under "
            "Settings, or leave it empty to keep search switched off.")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SearchUnavailable(
            f"{endpoint!r} is not a usable address. It should look like "
            f"{DEFAULT_ENDPOINT}.")
    return endpoint


def build_url(endpoint: str, query: str, count: int = MAX_RESULTS) -> str:
    text = _SPACE.sub(" ", str(query or "")).strip()
    if not text:
        raise ValueError("Say what to search for.")
    # `format=json` is the part that usually is not switched on; see `parse`.
    return f"{endpoint}/search?" + urlencode(
        {"q": text, "format": "json", "safesearch": "1"})


def parse(response: dict, count: int = MAX_RESULTS) -> list[dict]:
    """Turn one SearXNG JSON response into a small, clean list of results."""
    body = str(response.get("content") or "")
    stripped = body.lstrip()
    if stripped[:1] in {"<", ""}:
        # By default SearXNG serves HTML and nothing else, so this is the first
        # thing anyone hits. Saying "unreadable response" would send the user
        # looking in the wrong place entirely.
        raise SearchUnavailable(
            "The search service answered with a web page instead of JSON. In "
            "SearXNG's settings.yml, add `- json` under `search: formats:` and "
            "restart it.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SearchUnavailable(
            "The search service returned something that is not JSON.") from exc
    if not isinstance(payload, dict):
        raise SearchUnavailable("The search service returned an unexpected shape.")

    wanted = max(1, min(int(count or MAX_RESULTS), MAX_RESULTS))
    results: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _clean(item.get("url"), 500)
        if not url or url in seen or urlparse(url).scheme not in {"http", "https"}:
            continue
        seen.add(url)
        results.append({
            "title": _clean(item.get("title"), TITLE_CHARS) or url,
            "url": url,
            "snippet": _clean(item.get("content"), SNIPPET_CHARS),
            "source": _clean((item.get("engines") or [item.get("engine")])[:1][0]
                             if (item.get("engines") or item.get("engine")) else "", 40),
        })
        if len(results) >= wanted:
            break
    return results


def unreachable(endpoint: str, detail: str) -> SearchUnavailable:
    """One message for the case that will happen most: nothing is listening."""
    host = urlparse(endpoint).hostname or endpoint
    running = ("Start it, or switch search off in Settings."
               if host in {"localhost", "127.0.0.1", "::1"}
               else "Check the address, and that you have granted its domain.")
    return SearchUnavailable(f"No search service answered at {endpoint}. {running} "
                             f"({detail})")
