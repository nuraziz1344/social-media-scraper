import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crawler.cli import _run_post_crawl_merge
from crawler.merger import merge_and_export


def _write_sample_crawl_json(folder: Path, filename: str, items: list[dict]) -> Path:
    path = folder / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    return path


class TestPostCrawlMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name) / "2026-11-14"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        _write_sample_crawl_json(
            self.out_dir,
            "x_kulon_progo_20261114_010000.json",
            [
                {
                    "external_id": "tw-1001",
                    "platform": "x",
                    "author": "@akun_a",
                    "content": "Post pertama",
                    "url": "https://x.com/akun_a/status/1001",
                    "published_at": "2026-11-13T10:00:00Z",
                    "crawled_at": "2026-11-14T01:00:00Z",
                    "engagement_count": 5,
                    "engagement": {"likes": 5, "comments": 1, "shares": 0},
                    "reach": 100,
                },
                {
                    "external_id": "tw-1002",
                    "platform": "x",
                    "author": "@komentator",
                    "content": "Komentar di post pertama",
                    "url": "https://x.com/komentator/status/1002",
                    "parent_external_id": "tw-1001",
                    "published_at": "2026-11-13T10:05:00Z",
                    "crawled_at": "2026-11-14T01:00:00Z",
                    "engagement_count": 1,
                    "engagement": {"likes": 1, "comments": 0, "shares": 0},
                    "reach": 10,
                },
            ],
        )
        _write_sample_crawl_json(
            self.out_dir,
            "youtube_kulon_progo_20261114_010100.json",
            [
                {
                    "external_id": "yt-abc",
                    "platform": "youtube",
                    "author": "Channel B",
                    "content": "Video Kulon Progo",
                    "url": "https://youtube.com/watch?v=abc",
                    "published_at": "2026-11-12T08:00:00Z",
                    "crawled_at": "2026-11-14T01:01:00Z",
                    "engagement_count": 10,
                    "engagement": {"likes": 10, "comments": 0, "shares": 0},
                    "reach": 500,
                },
            ],
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_merge_per_platform_and_combined(self):
        files = merge_and_export(
            input_dir=self.out_dir,
            fmt="json",
            combined=True,
            per_platform=True,
        )
        names = {f.name for f in files}
        self.assertEqual(
            names,
            {"20261114-twitter.json", "20261114-youtube.json", "20261114-combined.json"},
        )

        with open(self.out_dir / "20261114-twitter.json", "r", encoding="utf-8") as f:
            tw_data = json.load(f)
        self.assertEqual(len(tw_data), 1)
        self.assertEqual(tw_data[0]["id"], "1001")
        self.assertEqual(len(tw_data[0]["daftar_comment"]), 1)

        with open(self.out_dir / "20261114-combined.json", "r", encoding="utf-8") as f:
            combined_data = json.load(f)
        self.assertEqual(len(combined_data), 2)

    def test_cli_post_crawl_merge_both_formats(self):
        # Disabled -> no files generated
        self.assertEqual(_run_post_crawl_merge(self.out_dir, merge=False, merge_combined=False), [])

        files = _run_post_crawl_merge(
            self.out_dir,
            merge=True,
            merge_format="both",
            merge_combined=True,
        )
        names = {f.name for f in files}
        self.assertEqual(
            names,
            {
                "20261114-twitter.json",
                "20261114-youtube.json",
                "20261114-combined.json",
                "20261114-twitter.csv",
                "20261114-youtube.csv",
                "20261114-combined.csv",
            },
        )

    def test_cli_post_crawl_merge_empty_dir(self):
        empty_dir = Path(self._tmp.name) / "2026-11-15"
        empty_dir.mkdir()
        self.assertEqual(_run_post_crawl_merge(empty_dir, merge=True), [])

    def test_root_merge_script_backwards_compat(self):
        import merge as root_merge

        self.assertTrue(callable(root_merge.merge_and_export))


if __name__ == "__main__":
    unittest.main()
