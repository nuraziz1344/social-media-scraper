import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crawler.utils.cookies import cookies_to_dict, merge_and_save_cookies, parse_netscape_cookies
from crawler.utils.session_check import check_all_sessions, check_platform_session


class TestSessionCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmp.name)
        self.cookies_dir = self.base_dir / "cookies"
        self.cookies_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_cookie_files(self):
        results = check_all_sessions(base_dir=self.base_dir, live=False)
        by_plat = {r.platform: r for r in results}
        self.assertEqual(by_plat["youtube"].status, "optional")
        self.assertEqual(by_plat["x"].status, "missing")
        self.assertEqual(by_plat["instagram"].status, "missing")

    def test_valid_and_expired_netscape_cookies_offline(self):
        future_exp = int(time.time()) + 86400 * 30
        past_exp = int(time.time()) - 3600

        (self.cookies_dir / "twitter.txt").write_text(
            f"# Netscape HTTP Cookie File\n"
            f".x.com\tTRUE\t/\tTRUE\t{future_exp}\tauth_token\tdummy_auth\n"
            f".x.com\tTRUE\t/\tTRUE\t{future_exp}\tct0\tdummy_ct0\n",
            encoding="utf-8",
        )
        (self.cookies_dir / "instagram.txt").write_text(
            f"# Netscape HTTP Cookie File\n"
            f".instagram.com\tTRUE\t/\tTRUE\t{past_exp}\tsessionid\tdummy_sess\n"
            f".instagram.com\tTRUE\t/\tTRUE\t{future_exp}\tcsrftoken\tdummy_csrf\n",
            encoding="utf-8",
        )
        (self.cookies_dir / "facebook.txt").write_text(
            f"# Netscape HTTP Cookie File\n"
            f".facebook.com\tTRUE\t/\tTRUE\t{future_exp}\tc_user\t12345\n",
            encoding="utf-8",
        )

        x_res = check_platform_session("x", base_dir=self.base_dir, live=False)
        self.assertEqual(x_res.status, "valid")
        self.assertTrue(x_res.has_required_tokens)

        ig_res = check_platform_session("instagram", base_dir=self.base_dir, live=False)
        self.assertEqual(ig_res.status, "expired")
        self.assertIn("sessionid", ig_res.expired_tokens)

        fb_res = check_platform_session("facebook", base_dir=self.base_dir, live=False)
        self.assertEqual(fb_res.status, "invalid")
        self.assertIn("xs", fb_res.missing_tokens)

    def test_playwright_json_cookie_support(self):
        future_exp = time.time() + 86400
        (self.cookies_dir / "tiktok.json").write_text(
            json.dumps({
                "cookies": [
                    {"name": "sessionid", "value": "abc", "domain": ".tiktok.com", "path": "/", "expires": future_exp}
                ],
                "origins": [],
            }),
            encoding="utf-8",
        )
        res = check_platform_session("tiktok", base_dir=self.base_dir, live=False)
        self.assertEqual(res.status, "valid")

    @patch("crawler.utils.session_check._probe_live_session")
    def test_live_probe_integration(self, mock_probe):
        future_exp = int(time.time()) + 86400
        (self.cookies_dir / "instagram.txt").write_text(
            f".instagram.com\tTRUE\t/\tTRUE\t{future_exp}\tsessionid\tabc\n"
            f".instagram.com\tTRUE\t/\tTRUE\t{future_exp}\tcsrftoken\txyz\n",
            encoding="utf-8",
        )
        mock_probe.return_value = (True, "@pemkabkp", "Session Instagram aktif")
        res = check_platform_session("instagram", base_dir=self.base_dir, live=True)
        self.assertEqual(res.status, "valid")
        self.assertEqual(res.username, "@pemkabkp")
        self.assertTrue(res.live_valid)

    def test_merge_and_save_cookies_updates_safely(self):
        future_exp = int(time.time()) + 86400 * 30
        ig_path = self.cookies_dir / "instagram.txt"
        ig_path.write_text(
            f"# Netscape HTTP Cookie File\n"
            f".instagram.com\tTRUE\t/\tTRUE\t{future_exp}\tsessionid\torig_sess\n"
            f".instagram.com\tTRUE\t/\tTRUE\t{future_exp}\tcsrftoken\told_csrf\n"
            f".other.com\tTRUE\t/\tTRUE\t{future_exp}\tkeep_me\tuntouched\n",
            encoding="utf-8",
        )

        # 1. Valid update refreshes csrftoken and adds rur, preserving .other.com
        saved = merge_and_save_cookies(
            ig_path,
            [
                {"name": "sessionid", "value": "orig_sess", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp},
                {"name": "csrftoken", "value": "new_csrf", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp + 100},
                {"name": "rur", "value": "ASH", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp},
            ],
            platform="instagram",
        )
        self.assertTrue(saved)
        parsed = cookies_to_dict(parse_netscape_cookies(ig_path))
        self.assertEqual(parsed["csrftoken"], "new_csrf")
        self.assertEqual(parsed["rur"], "ASH")
        self.assertEqual(parsed["keep_me"], "untouched")

        # 2. No-op update returns False
        saved_again = merge_and_save_cookies(
            ig_path,
            [
                {"name": "sessionid", "value": "orig_sess", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp},
                {"name": "csrftoken", "value": "new_csrf", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp + 100},
            ],
            platform="instagram",
        )
        self.assertFalse(saved_again)

        # 3. Logged-out / missing sessionid update MUST NOT overwrite file
        clobber = merge_and_save_cookies(
            ig_path,
            [
                {"name": "sessionid", "value": "", "domain": ".instagram.com", "path": "/", "secure": True, "expires": 0},
                {"name": "csrftoken", "value": "guest_csrf", "domain": ".instagram.com", "path": "/", "secure": True, "expires": future_exp},
            ],
            platform="instagram",
        )
        self.assertFalse(clobber)
        after_clobber = cookies_to_dict(parse_netscape_cookies(ig_path))
        self.assertEqual(after_clobber["sessionid"], "orig_sess")
        self.assertEqual(after_clobber["csrftoken"], "new_csrf")

    def test_merge_and_save_cookies_json_preserves_origins(self):
        future_exp = int(time.time()) + 86400
        tk_path = self.cookies_dir / "tiktok.json"
        tk_path.write_text(
            json.dumps({
                "cookies": [
                    {"name": "sessionid", "value": "s1", "domain": ".tiktok.com", "path": "/", "expires": future_exp}
                ],
                "origins": [{"origin": "https://www.tiktok.com", "localStorage": []}],
            }),
            encoding="utf-8",
        )
        saved = merge_and_save_cookies(
            tk_path,
            [
                {"name": "sessionid", "value": "s1", "domain": ".tiktok.com", "path": "/", "expires": future_exp},
                {"name": "ttwid", "value": "t123", "domain": ".tiktok.com", "path": "/", "expires": future_exp},
            ],
            platform="tiktok",
        )
        self.assertTrue(saved)
        data = json.loads(tk_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["origins"]), 1)
        self.assertEqual({c["name"]: c["value"] for c in data["cookies"]}["ttwid"], "t123")


if __name__ == "__main__":
    unittest.main()
