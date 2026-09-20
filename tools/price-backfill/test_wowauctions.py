import io
import json
import unittest
import urllib.error
from unittest import mock

import wowauctions


class UrlTest(unittest.TestCase):
    def test_data_url_uses_id_only(self):
        url = wowauctions.data_url("BUILD123", 4389)
        self.assertEqual(
            url,
            "https://www.wowauctions.net/_next/data/BUILD123/auctionHouse/"
            "chromie-craft/chromiecraft/mergedAh/x-4389.json",
        )


class ResolveBuildIdTest(unittest.TestCase):
    def test_extracts_build_id(self):
        html = '<script>{"props":{},"buildId":"pWPDRVbFX9AWIBQ1u1tR8"}</script>'
        with mock.patch.object(wowauctions, "_get", return_value=html):
            self.assertEqual(wowauctions.resolve_build_id(), "pWPDRVbFX9AWIBQ1u1tR8")

    def test_raises_when_missing(self):
        with mock.patch.object(wowauctions, "_get", return_value="<html></html>"):
            with self.assertRaises(RuntimeError):
                wowauctions.resolve_build_id()


class FetchItemTest(unittest.TestCase):
    def test_returns_item_dict(self):
        payload = json.dumps({"pageProps": {"item": {"stats": {"avg_price": 5}}}})
        with mock.patch.object(wowauctions, "_get", return_value=payload):
            item = wowauctions.fetch_item("BUILD123", 4389)
        self.assertEqual(item, {"stats": {"avg_price": 5}})

    def test_404_returns_none(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_get", side_effect=err):
            self.assertIsNone(wowauctions.fetch_item("BUILD123", 999999))

    def test_other_http_error_reraised(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_get", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                wowauctions.fetch_item("BUILD123", 4389)


class PageFallbackTest(unittest.TestCase):
    def setUp(self):
        wowauctions._page_mode = False
        self.addCleanup(setattr, wowauctions, "_page_mode", False)

    @staticmethod
    def _page(item):
        payload = json.dumps({"props": {"pageProps": {"item": item}}})
        return ('<html><script id="__NEXT_DATA__" type="application/json">'
                + payload + "</script></html>")

    def test_page_url_uses_id_only(self):
        self.assertEqual(
            wowauctions.page_url(4389),
            "https://www.wowauctions.net/auctionHouse/chromie-craft/"
            "chromiecraft/mergedAh/x-4389",
        )

    def test_data_404_falls_back_to_page(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        page = self._page({"stats": {"avg_price": 7}})
        with mock.patch.object(wowauctions, "_get", side_effect=[err, page]):
            item = wowauctions.fetch_item("BUILD123", 4389)
        self.assertEqual(item, {"stats": {"avg_price": 7}})

    def test_page_mode_latches_after_a_successful_fallback(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        page = self._page({"stats": {"avg_price": 7}})
        with mock.patch.object(wowauctions, "_get", side_effect=[err, page]):
            wowauctions.fetch_item("BUILD123", 4389)

        # Second call goes straight to the page: one request, no data URL.
        with mock.patch.object(wowauctions, "_get", return_value=page) as get:
            wowauctions.fetch_item("BUILD123", 8170)
        self.assertEqual(get.call_count, 1)
        self.assertIn("/auctionHouse/", get.call_args[0][0])

    def test_no_data_anywhere_returns_none_without_latching(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        page = self._page(None)
        with mock.patch.object(wowauctions, "_get", side_effect=[err, page]):
            self.assertIsNone(wowauctions.fetch_item("BUILD123", 999999))
        self.assertFalse(wowauctions._page_mode)

    def test_page_without_next_data_returns_none(self):
        with mock.patch.object(wowauctions, "_get", return_value="<html></html>"):
            self.assertIsNone(wowauctions.fetch_item_from_page(4389))


class RetryTest(unittest.TestCase):
    def test_retries_on_500_then_succeeds(self):
        err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b""))
        calls = [err, err, "ok"]

        def fake_raw(url, timeout):
            v = calls.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

        with mock.patch.object(wowauctions, "_raw_get", side_effect=fake_raw):
            with mock.patch.object(wowauctions.time, "sleep", return_value=None):
                self.assertEqual(wowauctions._get("http://x", retries=3, backoff=0), "ok")

    def test_404_not_retried(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
        with mock.patch.object(wowauctions, "_raw_get", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                wowauctions._get("http://x", retries=3, backoff=0)


if __name__ == "__main__":
    unittest.main()
