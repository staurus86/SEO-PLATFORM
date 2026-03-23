import unittest
from unittest.mock import patch

import requests

from app.tools.site_pro.sitemap_scope import sample_site_urls_from_sitemaps


class _Resp:
    def __init__(self, url: str, status_code: int, body: str, headers=None):
        self.url = url
        self.status_code = status_code
        self.text = body
        self.content = body.encode("utf-8")
        self.headers = headers or {}


class SiteProSitemapScopeTests(unittest.TestCase):
    def test_sampling_caps_huge_sitemap_index_and_keeps_stable(self):
        sitemap_index = [
            "<sitemapindex xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
        ]
        for idx in range(1, 60):
            sitemap_index.append(f"<sitemap><loc>https://site.test/sm-{idx}.xml</loc></sitemap>")
        sitemap_index.append("</sitemapindex>")
        sitemap_index_xml = "".join(sitemap_index)

        child_urls = [
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
        ]
        for idx in range(1, 80):
            child_urls.append(f"<url><loc>https://site.test/page-{idx}.html</loc></url>")
        child_urls.append("</urlset>")
        child_xml = "".join(child_urls)

        mapping = {"https://site.test/sitemap.xml": _Resp("https://site.test/sitemap.xml", 200, sitemap_index_xml)}
        for idx in range(1, 60):
            mapping[f"https://site.test/sm-{idx}.xml"] = _Resp(f"https://site.test/sm-{idx}.xml", 200, child_xml)

        session = requests.Session()

        def fake_get(url, timeout=0, allow_redirects=True, headers=None):
            return mapping[url]

        with patch("app.tools.site_pro.sitemap_scope._discover_sitemap_candidates", return_value=(["https://site.test/sitemap.xml"], "robots.txt")), patch.object(session, "get", side_effect=fake_get):
            result = sample_site_urls_from_sitemaps(site_url="https://site.test", session=session, page_limit=10)

        self.assertEqual(result["source"], "robots.txt")
        self.assertLessEqual(result["sitemaps_scanned"], 20)
        self.assertLessEqual(len(result["sample_urls"]), 40)
        self.assertTrue(result["truncated"])
        self.assertTrue(any("capped" in note.lower() for note in result["notes"]))

    def test_sampling_falls_back_cleanly_when_sitemap_fetch_fails(self):
        session = requests.Session()

        with patch("app.tools.site_pro.sitemap_scope._discover_sitemap_candidates", return_value=(["https://site.test/sitemap.xml"], "robots.txt")), patch.object(session, "get", side_effect=RuntimeError("boom")):
            result = sample_site_urls_from_sitemaps(site_url="https://site.test", session=session, page_limit=10)

        self.assertEqual(result["sample_urls"], [])
        self.assertEqual(result["sitemaps_scanned"], 1)
        self.assertTrue(any("skipped" in note.lower() for note in result["notes"]))

    def test_sampling_skips_external_urls(self):
        urlset_xml = (
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
            "<url><loc>https://site.test/page-1.html</loc></url>"
            "<url><loc>https://evil.test/page-2.html</loc></url>"
            "</urlset>"
        )
        session = requests.Session()

        with patch("app.tools.site_pro.sitemap_scope._discover_sitemap_candidates", return_value=(["https://site.test/sitemap.xml"], "robots.txt")), patch.object(session, "get", return_value=_Resp("https://site.test/sitemap.xml", 200, urlset_xml)):
            result = sample_site_urls_from_sitemaps(site_url="https://site.test", session=session, page_limit=10)

        self.assertEqual(result["sample_urls"], ["https://site.test/page-1.html"])
