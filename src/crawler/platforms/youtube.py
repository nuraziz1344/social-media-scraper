"""Scraper YouTube menggunakan scrapetube (pencarian) dan yt-dlp (metadata & komentar)."""
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator

import scrapetube
import yt_dlp

from ..models import CrawledMention
from ..utils.cookies import find_platform_cookie

logger = logging.getLogger(__name__)


def scrape_youtube(
    query: str,
    since_date: datetime | None = None,
    max_videos: int = 50,
    include_comments: bool = True,
    max_comments_per_video: int = 50,
    cookie_file: Path | str | None = None,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Crawl video YouTube dan komentarnya berdasarkan kata kunci pencarian."""
    if cookie_file is None:
        cookie_file = find_platform_cookie("youtube")

    logger.info("Mencari video YouTube untuk query: '%s'...", query)
    
    # 1. Cari video dengan scrapetube
    try:
        videos_generator: Generator = scrapetube.get_search(query, results_type="video")
    except Exception as exc:
        logger.error("Gagal melakukan pencarian video di YouTube: %s", exc)
        return []

    mentions: list[CrawledMention] = []
    processed_count = 0
    inspected_count = 0
    consecutive_out_of_date = 0
    max_consecutive_out_of_date = 10
    max_inspected = max(30, max_videos * 3)

    ydl_opts = {
        "skip_download": True,
        "getcomments": include_comments,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    if cookie_file and Path(cookie_file).is_file():
        ydl_opts["cookiefile"] = str(cookie_file)
        logger.info("Menggunakan cookie file YouTube: %s", cookie_file)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for item in videos_generator:
            if processed_count >= max_videos:
                break

            inspected_count += 1
            if inspected_count > max_inspected:
                logger.info(
                    "[Circuit Breaker] Mencapai batas maksimal inspeksi video (%d). "
                    "Menghentikan pencarian YouTube agar tidak looping tak terbatas.",
                    max_inspected,
                )
                break

            if stop_event and stop_event.is_set():
                logger.info("[YouTube] Proses dihentikan oleh sinyal pengguna.")
                break

            video_id = item.get("videoId")
            if not video_id:
                continue

            video_url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                info = ydl.extract_info(video_url, download=False)
                if not info:
                    continue
            except Exception as exc:
                logger.warning("Gagal mengambil detail video %s: %s", video_id, exc)
                continue

            # Parsing tanggal tayang
            pub_date = None
            if info.get("timestamp"):
                pub_date = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc)
            elif info.get("upload_date"):
                try:
                    pub_date = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            # Filter tanggal & Circuit Breaker
            if since_date and pub_date and pub_date < since_date:
                consecutive_out_of_date += 1
                logger.debug(
                    "Video %s (%s) melewati batas tanggal (%s). [Out of date: %d/%d]",
                    video_id,
                    pub_date.date(),
                    since_date.date(),
                    consecutive_out_of_date,
                    max_consecutive_out_of_date,
                )
                if consecutive_out_of_date >= max_consecutive_out_of_date:
                    logger.info(
                        "[Circuit Breaker] Menemukan %d video berturut-turut yang sudah melewati batas waktu %s. "
                        "Pencarian YouTube dihentikan karena hasil telah memasuki riwayat lama.",
                        consecutive_out_of_date,
                        since_date.date(),
                    )
                    break
                continue

            # Reset counter out of date jika menemukan video valid
            consecutive_out_of_date = 0

            title = info.get("title") or ""
            desc = info.get("description") or ""
            content = f"{title}\n\n{desc}".strip()
            uploader = info.get("uploader") or info.get("channel") or ""
            uploader_id = info.get("uploader_id") or info.get("channel_id") or uploader
            view_count = int(info.get("view_count") or 0)
            like_count = int(info.get("like_count") or 0)
            comment_count = int(info.get("comment_count") or 0)

            video_mention = CrawledMention(
                platform="youtube",
                external_id=f"yt-{video_id}",
                url=video_url,
                content=content,
                author=uploader_id,
                author_name=uploader,
                published_at=pub_date,
                reach=view_count,
                likes=like_count,
                comments=comment_count,
                engagement_count=like_count + comment_count,
                raw={
                    "duration": info.get("duration"),
                    "tags": info.get("tags", []),
                    "categories": info.get("categories", []),
                },
            )
            mentions.append(video_mention)
            if on_mention:
                try:
                    on_mention(video_mention)
                except Exception as exc:
                    logger.debug("Error saat menjalankan on_mention callback: %s", exc)
            processed_count += 1
            logger.info("[%d/%d] Terambil video: %s (%s)", processed_count, max_videos, title[:50], pub_date.date())

            # Ekstraksi Komentar jika ada
            if include_comments and info.get("comments"):
                c_list = info.get("comments", [])
                extracted_comments = 0
                for c in c_list:
                    if extracted_comments >= max_comments_per_video:
                        break
                    cm_id = c.get("id")
                    cm_text = c.get("text") or ""
                    cm_author = c.get("author") or "komentator"
                    cm_author_id = c.get("author_id") or cm_author
                    cm_likes = int(c.get("like_count") or 0)
                    cm_time = None
                    if c.get("timestamp"):
                        cm_time = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc)
                    else:
                        cm_time = pub_date

                    cm_mention = CrawledMention(
                        platform="youtube",
                        external_id=f"yt-cm-{cm_id}",
                        url=f"{video_url}#pantau-comment-{cm_id}",
                        content=cm_text,
                        author=cm_author_id,
                        author_name=cm_author,
                        published_at=cm_time,
                        likes=cm_likes,
                        engagement_count=cm_likes,
                        parent_external_id=video_mention.external_id,
                    )
                    mentions.append(cm_mention)
                    if on_mention:
                        try:
                            on_mention(cm_mention)
                        except Exception as exc:
                            logger.debug("Error saat menjalankan on_mention callback: %s", exc)
                    extracted_comments += 1

            # Jeda anti-bot / rate limit antar video pada platform yang sama
            jitter = round(random.uniform(2.0, 4.0), 2)
            logger.debug("Jeda aman anti-bot YouTube: %.2fs", jitter)
            if stop_event:
                if stop_event.wait(timeout=jitter):
                    break
            else:
                time.sleep(jitter)

    logger.info("Total item YouTube yang berhasil dikumpulkan: %d (termasuk komentar)", len(mentions))
    return mentions
