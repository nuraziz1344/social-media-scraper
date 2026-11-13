"""Test parser Instagram GraphQL dan Facebook DOM/GraphQL."""
import unittest
from datetime import datetime, timezone
from crawler.platforms.instagram import _find_media_nodes_recursive, _parse_ig_media_node
from crawler.platforms.facebook import _extract_facebook_story_details
from crawler.platforms.threads import _parse_threads_post
from crawler.platforms.tiktok import _parse_tiktok_item


class TestParsers(unittest.TestCase):
    def test_instagram_modern_graphql_node(self):
        # Sample node from xdt_fbsearch__top_serp_graphql
        sample_node = {
            "__typename": "XDTMediaDict",
            "pk": "1234567890",
            "id": "1234567890_999",
            "code": "DedwwbKxhGZ",
            "media_type": 1,
            "user": {
                "pk": "999",
                "username": "damkar_jogja",
                "full_name": "Damkar Kota Yogyakarta",
                "is_verified": False,
            },
            "taken_at": 1729000000,
            "caption": {
                "text": "Laporan kebakaran rumah warga di kawasan Malioboro"
            },
            "like_count": 350,
            "comment_count": 25,
            "image_versions2": {}
        }
        
        # Test recursive search
        results = []
        payload = {"data": {"xdt_fbsearch__top_serp_graphql": {"edges": [{"node": {"items": [sample_node]}}]}}}
        _find_media_nodes_recursive(payload, results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "DedwwbKxhGZ")
        
        # Test parse
        m = _parse_ig_media_node(sample_node)
        self.assertIsNotNone(m)
        self.assertEqual(m.author, "damkar_jogja")
        self.assertEqual(m.author_name, "Damkar Kota Yogyakarta")
        self.assertEqual(m.url, "https://www.instagram.com/p/DedwwbKxhGZ/")
        self.assertEqual(m.content, "Laporan kebakaran rumah warga di kawasan Malioboro")
        self.assertEqual(m.media_type, "image")
        self.assertEqual(m.likes, 350)
        self.assertEqual(m.comments, 25)
        self.assertEqual(m.published_at.year, 2024)

    def test_facebook_story_details(self):
        sample_story = {
            "actors": [{"name": "Relawan Kebakaran Jogja", "url": "https://www.facebook.com/relawan.kebakaran"}],
            "story_post_url": "https://www.facebook.com/relawan.kebakaran/posts/987654321",
            "creation_time": 1729000000,
        }
        details = _extract_facebook_story_details(sample_story)
        self.assertEqual(details["author_name"], "Relawan Kebakaran Jogja")
        self.assertEqual(details["author_handle"], "relawan_kebakaran_jogja")
        self.assertEqual(details["post_url"], "https://www.facebook.com/relawan.kebakaran/posts/987654321")
        self.assertIsNotNone(details["published_at"])

    def test_media_type_detection_across_platforms(self):
        # Instagram video & carousel
        ig_vid = _parse_ig_media_node({"code": "V1", "media_type": 2, "user": {"username": "u"}, "caption": {"text": "Video caption"}})
        self.assertEqual(ig_vid.media_type, "video")
        self.assertEqual(ig_vid.content, "Video caption")
        ig_car = _parse_ig_media_node({"code": "C1", "media_type": 8, "user": {"username": "u"}, "carousel_media": [{}, {}], "caption": {"text": "Slide"}})
        self.assertEqual(ig_car.media_type, "carousel")

        # TikTok video & imagePost
        tt_vid = _parse_tiktok_item({"id": "111", "desc": "TikTok caption", "author": {"uniqueId": "u1"}})
        self.assertEqual(tt_vid.media_type, "video")
        self.assertEqual(tt_vid.content, "TikTok caption")
        tt_img = _parse_tiktok_item({"id": "222", "desc": "Photo mode", "author": {"uniqueId": "u1"}, "imagePost": {"images": [{}, {}]}})
        self.assertEqual(tt_img.media_type, "carousel")

        # Threads text, image, video
        th_txt = _parse_threads_post({"code": "T1", "user": {"username": "u"}, "caption": {"text": "Hello"}})
        self.assertEqual(th_txt.media_type, "text")
        th_img = _parse_threads_post({"code": "T2", "user": {"username": "u"}, "caption": {"text": "Pic"}, "image_versions2": {"candidates": [{}]}})
        self.assertEqual(th_img.media_type, "image")
        th_vid = _parse_threads_post({"code": "T3", "user": {"username": "u"}, "caption": {"text": "Clip"}, "video_versions": [{}]})
        self.assertEqual(th_vid.media_type, "video")
        self.assertEqual(th_vid.to_pantaudev_payload()["media_type"], "video")


if __name__ == "__main__":
    unittest.main()
