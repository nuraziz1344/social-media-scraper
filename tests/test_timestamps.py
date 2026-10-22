"""Unit test untuk verifikasi decoding timestamp Snowflake, fallback None, dan filtering tanggal."""
import unittest
from datetime import datetime, timezone
import json

from crawler.models import CrawledMention
from crawler.utils.snowflake import decode_instagram_shortcode
from crawler.exporter import export_to_twitter_dataset_json
from merge import format_iso_utc


class TestTimestampAndFiltering(unittest.TestCase):
    def test_decode_instagram_shortcode(self):
        # Sample shortcodes from real dataset
        dt1 = decode_instagram_shortcode("DCkUaSszIPR")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.year, 2024)
        self.assertEqual(dt1.month, 11)
        self.assertEqual(dt1.day, 19)

        dt2 = decode_instagram_shortcode("DAuund6Skn7")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2.year, 2024)
        self.assertEqual(dt2.month, 10)
        self.assertEqual(dt2.day, 5)

        # Threads shortcode
        dt3 = decode_instagram_shortcode("DeY3PB_jiEJ")
        self.assertIsNotNone(dt3)
        self.assertEqual(dt3.year, 2026)
        self.assertEqual(dt3.month, 10)
        self.assertEqual(dt3.day, 12)

        # Invalid shortcodes
        self.assertIsNone(decode_instagram_shortcode(""))
        self.assertIsNone(decode_instagram_shortcode(None))
        self.assertIsNone(decode_instagram_shortcode("invalid!chars$"))

    def test_crawled_mention_published_at_optional(self):
        # Verify published_at can be None and serializes to None
        m = CrawledMention(
            platform="instagram",
            external_id="ig-test123",
            url="https://instagram.com/p/test123",
            content="Testing without timestamp",
            published_at=None,
        )
        self.assertIsNone(m.published_at)
        self.assertIsNotNone(m.crawled_at)

        payload = m.to_pantaudev_payload()
        self.assertIsNone(payload["published_at"])
        self.assertIsNotNone(payload["crawled_at"])

    def test_format_iso_utc_null(self):
        # None and empty string should return None (serializes to null in json)
        self.assertIsNone(format_iso_utc(None))
        self.assertIsNone(format_iso_utc(""))

        # Valid date formats properly
        res = format_iso_utc("2026-10-12T09:00:00Z")
        self.assertEqual(res, "2026-10-12T09:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
