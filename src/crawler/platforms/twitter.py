"""Scraper X (Twitter) menggunakan twscrape."""
import asyncio
import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from twscrape import API
from twscrape.models import Tweet

from ..models import CrawledMention
from ..utils.cookies import extract_twitter_tokens, find_platform_cookie

logger = logging.getLogger(__name__)


async def _init_twscrape_pool(api: API, cookie_file: Path | str | None = None) -> bool:
    """Memastikan minimal ada satu akun aktif di database pool twscrape."""
    stats = await api.pool.stats()
    if stats.get("total", 0) > 0 and stats.get("active", 0) > 0:
        return True

    # Jika belum ada akun, cari cookie
    if cookie_file is None:
        cookie_file = find_platform_cookie("x")

    if not cookie_file or not Path(cookie_file).is_file():
        logger.error(
            "Tidak ada akun aktif di twscrape pool dan file cookie 'cookies/x.txt' tidak ditemukan. "
            "Silakan taruh file export cookie dari X ke cookies/x.txt."
        )
        return False

    tokens = extract_twitter_tokens(cookie_file)
    auth_token = tokens.get("auth_token")
    ct0 = tokens.get("ct0")

    if not auth_token or not ct0:
        logger.error("File cookie %s tidak memuat auth_token atau ct0 yang valid.", cookie_file)
        return False

    cookies_str = f"auth_token={auth_token}; ct0={ct0}"
    # Gunakan placeholder username jika tidak diketahui; twscrape akan memperbarui info saat request
    username = f"user_{auth_token[:8]}"
    try:
        await api.pool.add_account_cookies(username, cookies_str)
        logger.info("Berhasil mendaftarkan akun X ke twscrape pool dari %s", cookie_file)
        return True
    except Exception as exc:
        logger.error("Gagal menambahkan akun ke twscrape pool: %s", exc)
        return False


def _tweet_to_mention(tweet: Tweet) -> CrawledMention:
    """Konversi objek Tweet twscrape ke CrawledMention pantaudev."""
    # Pastikan timezone UTC
    pub_date = tweet.date
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)

    username = tweet.user.username if tweet.user else "anonym"
    display_name = tweet.user.displayname if tweet.user else username
    likes = int(tweet.likeCount or 0)
    replies = int(tweet.replyCount or 0)
    retweets = int(tweet.retweetCount or 0)
    views = int(tweet.viewCount or 0)

    parent_id = None
    if tweet.inReplyToTweetIdStr:
        parent_id = f"tw-{tweet.inReplyToTweetIdStr}"

    return CrawledMention(
        platform="x",
        external_id=f"tw-{tweet.id_str}",
        url=tweet.url or f"https://x.com/{username}/status/{tweet.id_str}",
        content=tweet.rawContent or "",
        author=username,
        author_name=display_name,
        published_at=pub_date,
        reach=views,
        likes=likes,
        comments=replies,
        shares=retweets,
        engagement_count=likes + replies + retweets,
        parent_external_id=parent_id,
        raw={
            "conversation_id": tweet.conversationIdStr,
            "lang": tweet.lang,
            "hashtags": tweet.hashtags,
        },
    )


async def _scrape_twitter_async(
    query: str,
    since_date: date | datetime | None = None,
    until_date: date | datetime | None = None,
    max_tweets: int = 100,
    include_comments: bool = True,
    max_comments_per_tweet: int = 20,
    cookie_file: Path | str | None = None,
    db_file: Path | str | None = None,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Crawl tweet dan balasan/komentarnya menggunakan operator pencarian tanggal."""
    if db_file is None:
        # Simpan database akun di folder cookies/ atau root crawler
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        db_file = base_dir / "cookies" / "twscrape.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)

    api = API(str(db_file))
    ready = await _init_twscrape_pool(api, cookie_file=cookie_file)
    if not ready:
        return []

    # Susun search query dengan filter tanggal
    search_parts = [query]
    if since_date:
        d_str = since_date.strftime("%Y-%m-%d") if hasattr(since_date, "strftime") else str(since_date)
        search_parts.append(f"since:{d_str}")
    if until_date:
        d_str = until_date.strftime("%Y-%m-%d") if hasattr(until_date, "strftime") else str(until_date)
        search_parts.append(f"until:{d_str}")

    search_query = " ".join(search_parts)
    logger.info("Menjalankan pencarian X dengan query: '%s' (limit: %d, comments: %s)...", search_query, max_tweets, include_comments)

    mentions: list[CrawledMention] = []
    seen_ids: set[str] = set()

    try:
        async for tweet in api.search(search_query, limit=max_tweets):
            if stop_event and stop_event.is_set():
                logger.info("[X/Twitter] Proses dihentikan oleh sinyal pengguna.")
                break
            mention = _tweet_to_mention(tweet)
            if since_date and mention.published_at:
                limit_date = since_date.date() if isinstance(since_date, datetime) else since_date
                if mention.published_at.date() < limit_date:
                    logger.debug("[X/Twitter] Melewati tweet usang (%s < %s): %s", mention.published_at.date(), limit_date, mention.external_id)
                    continue

            if mention.external_id not in seen_ids:
                seen_ids.add(mention.external_id)
                mentions.append(mention)
                if on_mention:
                    try:
                        on_mention(mention)
                    except Exception as exc:
                        logger.debug("Error saat menjalankan on_mention callback: %s", exc)

            # Ekstraksi balasan / komentar aktif jika tweet memiliki replyCount > 0
            if include_comments and getattr(tweet, "replyCount", 0) and tweet.replyCount > 0:
                reply_count = 0
                try:
                    async for reply in api.tweet_replies(tweet.id, limit=max_comments_per_tweet):
                        if stop_event and stop_event.is_set():
                            break
                        reply_mention = _tweet_to_mention(reply)
                        reply_mention.parent_external_id = f"tw-{tweet.id_str}"
                        if reply_mention.external_id not in seen_ids:
                            seen_ids.add(reply_mention.external_id)
                            mentions.append(reply_mention)
                            if on_mention:
                                try:
                                    on_mention(reply_mention)
                                except Exception as exc:
                                    logger.debug("Error saat on_mention reply X: %s", exc)
                            reply_count += 1
                        if reply_count >= max_comments_per_tweet:
                            break
                    if reply_count > 0:
                        logger.info("  [X] Mengambil %d balasan/komentar untuk tweet %s", reply_count, tweet.id_str)
                except Exception as exc:
                    logger.debug("Gagal mengambil balasan tweet %s: %s", tweet.id_str, exc)

            if len(mentions) % 10 == 0:
                jitter = round(random.uniform(2.0, 4.0), 2)
                logger.debug("Jeda aman anti-bot X: %.2fs", jitter)
                for _ in range(int(jitter * 10)):
                    if stop_event and stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)
            if len(mentions) % 25 == 0 or len(mentions) == max_tweets:
                logger.info("Terkumpul %d item dari X (termasuk komentar)...", len(mentions))
    except Exception as exc:
        logger.error("Terjadi error saat search di X: %s", exc)

    logger.info("Selesai crawl X. Total didapatkan: %d item (termasuk komentar).", len(mentions))
    return mentions


def scrape_twitter(
    query: str,
    since_date: date | datetime | None = None,
    until_date: date | datetime | None = None,
    max_tweets: int = 100,
    include_comments: bool = True,
    max_comments_per_tweet: int = 20,
    cookie_file: Path | str | None = None,
    db_file: Path | str | None = None,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Wrapper sinkron untuk _scrape_twitter_async."""
    return asyncio.run(
        _scrape_twitter_async(
            query=query,
            since_date=since_date,
            until_date=until_date,
            max_tweets=max_tweets,
            include_comments=include_comments,
            max_comments_per_tweet=max_comments_per_tweet,
            cookie_file=cookie_file,
            db_file=db_file,
            on_mention=on_mention,
            stop_event=stop_event,
        )
    )
