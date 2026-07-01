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


def fetch_item(build_id: str, item_id: int, timeout: int = 20) -> Optional[dict]:
    try:
        body = _get(data_url(build_id, item_id), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    data = json.loads(body)
    return data.get("pageProps", {}).get("item")
