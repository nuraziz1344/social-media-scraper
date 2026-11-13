"""Unit test untuk parsing target akun, loader JSON, resolusi CLI, dan parser SSR."""
import json
import tempfile
import unittest
from pathlib import Path

from crawler.cli import _resolve_max_results, _resolve_output_dir, _resolve_since_date
from crawler.platforms.tiktok import _extract_tiktok_ssr_items
from crawler.utils.accounts import (
    infer_platform_from_input,
    load_accounts_file,
    parse_account_target,
)


class TestAccountParsing(unittest.TestCase):
    def test_infer_platform_from_url(self):
        self.assertEqual(infer_platform_from_input("https://www.youtube.com/@bapperidakulonprogo"), "youtube")
        self.assertEqual(infer_platform_from_input("https://x.com/bapperida"), "x")
        self.assertEqual(infer_platform_from_input("https://twitter.com/bapperida"), "x")
        self.assertEqual(infer_platform_from_input("https://www.instagram.com/setdakp/"), "instagram")
        self.assertEqual(infer_platform_from_input("https://www.tiktok.com/@bapperidakp"), "tiktok")
        self.assertEqual(infer_platform_from_input("https://www.facebook.com/profile.php?id=61577862436420"), "facebook")
        self.assertEqual(infer_platform_from_input("https://www.threads.com/@irda.kulonprogo"), "threads")
        self.assertEqual(infer_platform_from_input("https://www.threads.net/@irda.kulonprogo"), "threads")
        self.assertIsNone(infer_platform_from_input("@setdakp"))
        self.assertIsNone(infer_platform_from_input("setdakp"))

    def test_youtube_urls_and_usernames(self):
        t1 = parse_account_target("https://www.youtube.com/channel/UC57VkBM0IVFaNMeeOl_XzFQ")
        self.assertEqual(t1.platform, "youtube")
        self.assertEqual(t1.kind, "channel_id")
        self.assertEqual(t1.identifier, "UC57VkBM0IVFaNMeeOl_XzFQ")
        self.assertEqual(t1.url, "https://www.youtube.com/channel/UC57VkBM0IVFaNMeeOl_XzFQ")

        t2 = parse_account_target("https://www.youtube.com/@bapperidakulonprogo/videos")
        self.assertEqual(t2.platform, "youtube")
        self.assertEqual(t2.kind, "username")
        self.assertEqual(t2.identifier, "bapperidakulonprogo")
        self.assertEqual(t2.url, "https://www.youtube.com/@bapperidakulonprogo")

        t3 = parse_account_target("@bapperidakulonprogo", default_platform="yt")
        self.assertEqual(t3.platform, "youtube")
        self.assertEqual(t3.identifier, "bapperidakulonprogo")

    def test_facebook_variants(self):
        t1 = parse_account_target("https://www.facebook.com/profile.php?id=61577862436420")
        self.assertEqual(t1.platform, "facebook")
        self.assertEqual(t1.kind, "profile_id")
        self.assertEqual(t1.identifier, "61577862436420")
        self.assertEqual(t1.url, "https://www.facebook.com/profile.php?id=61577862436420")

        t2 = parse_account_target("https://www.facebook.com/p/Inspektorat-Daerah-Kabupaten-Kulon-Progo-100083379702864/")
        self.assertEqual(t2.platform, "facebook")
        self.assertEqual(t2.kind, "page_p")
        self.assertEqual(t2.identifier, "Inspektorat-Daerah-Kabupaten-Kulon-Progo-100083379702864")

        t3 = parse_account_target("https://www.facebook.com/bkad.kulonprogo.7")
        self.assertEqual(t3.platform, "facebook")
        self.assertEqual(t3.identifier, "bkad.kulonprogo.7")

    def test_instagram_x_tiktok_threads(self):
        ig = parse_account_target("https://www.instagram.com/setdakp/?igsh=abc123")
        self.assertEqual(ig.platform, "instagram")
        self.assertEqual(ig.identifier, "setdakp")
        self.assertEqual(ig.url, "https://www.instagram.com/setdakp/")
        self.assertNotIn("/", ig.slug)

        x = parse_account_target("https://x.com/bapperida?s=21")
        self.assertEqual(x.platform, "x")
        self.assertEqual(x.identifier, "bapperida")

        tt = parse_account_target("https://www.tiktok.com/@bapperidakp")
        self.assertEqual(tt.platform, "tiktok")
        self.assertEqual(tt.identifier, "bapperidakp")
        self.assertEqual(tt.url, "https://www.tiktok.com/@bapperidakp")

        th = parse_account_target("https://www.threads.com/@irda.kulonprogo")
        self.assertEqual(th.platform, "threads")
        self.assertEqual(th.identifier, "irda.kulonprogo")

    def test_platform_mismatch_raises(self):
        with self.assertRaises(ValueError):
            parse_account_target("https://www.instagram.com/setdakp/", default_platform="tiktok")

    def test_load_accounts_file_both_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "dict_accounts.json"
            p1.write_text(
                json.dumps(
                    {
                        "instagram": ["setdakp", "https://www.instagram.com/setdakp/", "–"],
                        "x": ["https://x.com/bapperida"],
                        "youtube": ["UC57VkBM0IVFaNMeeOl_XzFQ"],
                    }
                ),
                encoding="utf-8",
            )
            grouped1 = load_accounts_file(p1)
            self.assertEqual(len(grouped1["instagram"]), 1)  # deduplicated
            self.assertEqual(grouped1["instagram"][0].identifier, "setdakp")
            self.assertEqual(grouped1["x"][0].identifier, "bapperida")
            self.assertEqual(grouped1["youtube"][0].kind, "channel_id")

            p2 = Path(tmp) / "list_accounts.json"
            p2.write_text(
                json.dumps(
                    [
                        {"platform": "tiktok", "account": "@bapperidakp"},
                        "https://www.threads.com/@irda.kulonprogo",
                    ]
                ),
                encoding="utf-8",
            )
            grouped2 = load_accounts_file(p2)
            self.assertEqual(grouped2["tiktok"][0].identifier, "bapperidakp")
            self.assertEqual(grouped2["threads"][0].identifier, "irda.kulonprogo")


class TestCliResolutionAndParsers(unittest.TestCase):
    def test_cli_date_and_limit_resolution(self):
        # Account mode defaults: no date limit, unlimited posts
        self.assertIsNone(_resolve_since_date(None, None, is_account_mode=True))
        self.assertIsNone(_resolve_max_results(None, is_account_mode=True))

        # Query mode defaults: 6 months, 50 posts
        self.assertIsNotNone(_resolve_since_date(None, None, is_account_mode=False))
        self.assertEqual(_resolve_max_results(None, is_account_mode=False), 50)

        # Explicit 0 disables limits in any mode
        self.assertIsNone(_resolve_since_date(0, None, is_account_mode=False))
        self.assertIsNone(_resolve_max_results(0, is_account_mode=False))

        # Explicit since/months/limit in account mode
        dt = _resolve_since_date(None, "2026-01-01", is_account_mode=True)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 1, 1))
        self.assertEqual(_resolve_max_results(25, is_account_mode=True), 25)

        # Default output dir: output/<YYYY-MM-DD>
        from datetime import datetime
        expected_date = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(_resolve_output_dir(None), Path("output") / expected_date)
        self.assertEqual(_resolve_output_dir("custom_out"), Path("custom_out"))

    def test_extract_tiktok_ssr_items(self):
        ssr = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {},
                "ItemModule": {
                    "7123456": {"id": "7123456", "desc": "Video SSR", "createTime": 1729000000}
                },
            }
        }
        items = _extract_tiktok_ssr_items(ssr)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "7123456")

    def test_parse_platforms_arg_and_interactive_filter(self):
        from unittest.mock import patch
        from crawler.cli import _parse_platforms_arg
        from crawler.interactive import prompt_filter_account_platforms

        self.assertEqual(_parse_platforms_arg("ig, twitter, yt"), ["instagram", "x", "youtube"])
        with self.assertRaises(ValueError):
            _parse_platforms_arg("x,unknown_plat")

        grouped = {
            "youtube": [parse_account_target("@chan", "youtube")],
            "instagram": [parse_account_target("@iguser", "instagram")],
            "tiktok": [parse_account_target("@ttuser", "tiktok")],
        }
        with patch("builtins.input", return_value="1,3"):
            filtered = prompt_filter_account_platforms(grouped)
        self.assertEqual(list(filtered.keys()), ["youtube", "tiktok"])

        with patch("builtins.input", return_value="A"):
            all_kept = prompt_filter_account_platforms(grouped)
        self.assertEqual(list(all_kept.keys()), ["youtube", "instagram", "tiktok"])


if __name__ == "__main__":
    unittest.main()
