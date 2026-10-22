"""Unit test untuk parsing komentar multi-platform dan handling di merge.py."""
import unittest
from datetime import datetime, timezone
from crawler.models import CrawledMention
from crawler.platforms.instagram import _parse_ig_comment_nodes
from crawler.platforms.threads import _extract_threads_posts_and_replies, _parse_threads_post
from crawler.platforms.twitter import _tweet_to_mention
from merge import deduplicate_platform_items, transform_to_reference_json


class TestComments(unittest.TestCase):
    def test_crawled_mention_comment_payload(self):
        m = CrawledMention(
            platform="youtube",
            external_id="yt-cm-12345",
            url="https://youtube.com/watch?v=abc#pantau-comment-12345",
            content="Komentar bagus",
            author="komentator1",
            published_at=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
            likes=10,
            parent_external_id="yt-abc",
        )
        payload = m.to_pantaudev_payload()
        self.assertEqual(payload["parent_external_id"], "yt-abc")
        self.assertEqual(payload["engagement"]["likes"], 10)

    def test_instagram_comment_nodes_parsing(self):
        sample_node = {
            "edge_media_to_parent_comment": {
                "edges": [
                    {
                        "node": {
                            "id": "cm_999",
                            "text": "Semoga api segera padam pak",
                            "owner": {"username": "warga_jogja", "full_name": "Warga Jogja"},
                            "created_at": 1729000100,
                            "like_count": 5,
                        }
                    }
                ]
            }
        }
        comments = _parse_ig_comment_nodes(
            sample_node,
            parent_external_id="ig-DedwwbKxhGZ",
            post_url="https://www.instagram.com/p/DedwwbKxhGZ/",
        )
        self.assertEqual(len(comments), 1)
        cm = comments[0]
        self.assertEqual(cm.external_id, "ig-cm-cm_999")
        self.assertEqual(cm.parent_external_id, "ig-DedwwbKxhGZ")
        self.assertEqual(cm.author, "warga_jogja")
        self.assertEqual(cm.content, "Semoga api segera padam pak")
        self.assertEqual(cm.likes, 5)

    def test_threads_posts_and_replies_extraction(self):
        sample_data = {
            "thread_items": [
                {
                    "post": {
                        "pk": "3001",
                        "code": "ThPost01",
                        "user": {"username": "damkar"},
                        "caption": {"text": "Update kebakaran terkini"},
                    }
                },
                {
                    "post": {
                        "pk": "3002",
                        "code": "ThReply01",
                        "user": {"username": "relawan"},
                        "caption": {"text": "Bantuan armada meluncur!"},
                    }
                },
            ]
        }
        results = []
        _extract_threads_posts_and_replies(sample_data, results)
        self.assertEqual(len(results), 2)
        
        post_main, parent_main = results[0]
        self.assertIsNone(parent_main)
        m_main = _parse_threads_post(post_main, parent_external_id=parent_main)
        self.assertEqual(m_main.external_id, "th-ThPost01")
        self.assertIsNone(m_main.parent_external_id)

        post_reply, parent_reply = results[1]
        self.assertEqual(parent_reply, "th-ThPost01")
        m_reply = _parse_threads_post(post_reply, parent_external_id=parent_reply)
        self.assertEqual(m_reply.external_id, "th-ThReply01")
        self.assertEqual(m_reply.parent_external_id, "th-ThPost01")

    def test_merge_comments_grouping_and_orphan_handling(self):
        items = [
            ("kebakaran", {
                "external_id": "tw-100",
                "author": "berita_jogja",
                "content": "Terjadi kebakaran hutan di lereng gunung.",
                "url": "https://x.com/berita_jogja/status/100",
                "published_at": "2026-10-22T08:00:00+00:00",
                "crawled_at": "2026-10-22T09:00:00+00:00",
                "parent_external_id": None,
                "engagement": {"likes": 50, "comments": 1},
            }),
            ("kebakaran", {
                "external_id": "tw-101",
                "author": "warga1",
                "content": "Semoga segera padam!",
                "url": "https://x.com/warga1/status/101",
                "published_at": "2026-10-22T08:15:00+00:00",
                "crawled_at": "2026-10-22T09:00:00+00:00",
                "parent_external_id": "tw-100",
                "engagement": {"likes": 2},
            }),
            ("kebakaran", {
                "external_id": "tw-201",
                "author": "warga2",
                "content": "Komentar di tweet lain yang induknya tidak dicrawl",
                "url": "https://x.com/warga2/status/201",
                "published_at": "2026-10-22T08:20:00+00:00",
                "crawled_at": "2026-10-22T09:00:00+00:00",
                "parent_external_id": "tw-9999",  # Orphan parent
                "engagement": {"likes": 1},
            }),
        ]
        posts, comments_by_parent, raw_by_id, raw_total, duplicates = deduplicate_platform_items(items)
        self.assertIn("tw-100", comments_by_parent)
        self.assertEqual(len(comments_by_parent["tw-100"]), 1)
        self.assertEqual(comments_by_parent["tw-100"][0]["comment_content"], "Semoga segera padam!")
        
        # tw-201 adalah orphan comment, tetap ada di posts tapi parent_external_id tidak hilang
        self.assertIn("tw-201", posts)
        self.assertEqual(posts["tw-201"]["parent_external_id"], "tw-9999")

        ref_json = transform_to_reference_json("twitter", posts, comments_by_parent)
        # Cari item tw-100
        p_100 = next(it for it in ref_json if it["id"] == "100")
        self.assertEqual(len(p_100["daftar_comment"]), 1)
        self.assertEqual(p_100["total_comment"], 1)

        # Cari item tw-201 (orphan)
        p_201 = next(it for it in ref_json if it["id"] == "201")
        self.assertEqual(p_201["parent_external_id"], "tw-9999")


if __name__ == "__main__":
    unittest.main()
