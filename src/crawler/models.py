"""Model data standar untuk hasil crawling multi-platform.

Struktur dirancang agar selaras dengan skema ingestion Mention dan Author
pada project pantaudev (apps.governance.ingestion.ingest_items).
"""
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class CrawledMention(BaseModel):
    platform: str
    external_id: str
    url: str
    content: str = ""
    author: str = ""
    author_name: str | None = None
    published_at: datetime | None = None
    crawled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    engagement_count: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    parent_external_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_pantaudev_payload(self) -> dict[str, Any]:
        """Format payload dictionary yang siap diterima oleh ingest_items di pantaudev."""
        calc_engagement = self.engagement_count or (self.likes + self.comments + self.shares)
        return {
            "external_id": self.external_id,
            "url": self.url,
            "content": self.content,
            "author": self.author,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "crawled_at": self.crawled_at.isoformat() if self.crawled_at else None,
            "engagement_count": calc_engagement,
            "reach": self.reach,
            "engagement": {
                "likes": self.likes,
                "comments": self.comments,
                "shares": self.shares,
            },
            "parent_external_id": self.parent_external_id,
        }
