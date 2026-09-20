"""HTTP client for the wowauctions.net ChromieCraft Next.js data endpoint."""
import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.wowauctions.net"
_BUILDID_RE = re.compile(r'"buildId":"([^"]+)"')
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# Latched once the page answers where the data endpoint 404d, see fetch_item.
_page_mode = False


def _raw_get(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _get(url: str, timeout: int = 20, retries: int = 5, backoff: float = 1.0) -> str:
    """GET with exponential backoff on 429/5xx and network errors. 404 is raised immediately."""
    attempt = 0
    while True:
        try:
            return _raw_get(url, timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404 or e.code < 500 and e.code != 429:
                raise
            last = e
        except urllib.error.URLError as e:
            last = e
        attempt += 1
        if attempt > retries:
            raise last
        time.sleep(backoff * (2 ** (attempt - 1)))


def resolve_build_id() -> str:
    html = _get(BASE + "/")
    m = _BUILDID_RE.search(html)
    if not m:
        raise RuntimeError("buildId not found on wowauctions.net homepage")
    return m.group(1)


def data_url(build_id: str, item_id: int) -> str:
    return ("{}/_next/data/{}/auctionHouse/chromie-craft/chromiecraft/mergedAh/"
            "x-{}.json").format(BASE, build_id, item_id)


def page_url(item_id: int) -> str:
    return ("{}/auctionHouse/chromie-craft/chromiecraft/mergedAh/"
            "x-{}").format(BASE, item_id)


def fetch_item_from_page(item_id: int, timeout: int = 20) -> Optional[dict]:
    """Read the item out of the page's __NEXT_DATA__ blob.

    Same payload as the data endpoint, one level deeper. Used when the data
    endpoint is gone, which it was site-wide on 2026-09-20: every id 404d
    under a freshly resolved buildId, the sentinel included.
    """
    try:
        body = _get(page_url(item_id), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    m = _NEXT_DATA_RE.search(body)
    if not m:
        return None
    data = json.loads(m.group(1))
    return data.get("props", {}).get("pageProps", {}).get("item")


def fetch_item(build_id: str, item_id: int, timeout: int = 20) -> Optional[dict]:
    global _page_mode

    if _page_mode:
        return fetch_item_from_page(item_id, timeout=timeout)

    try:
        body = _get(data_url(build_id, item_id), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        # A 404 here means either that the item has no data or that the data
        # endpoint itself has moved. The page still answers in both cases, so
        # ask it before concluding there is nothing. Once the page answers
        # where the endpoint did not, stay on the page for the rest of the
        # run: otherwise every unpriced item costs two requests.
        item = fetch_item_from_page(item_id, timeout=timeout)
        if item is not None:
            _page_mode = True
        return item

    data = json.loads(body)
    return data.get("pageProps", {}).get("item")
