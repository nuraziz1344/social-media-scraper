"""Scraper TikTok menggunakan Playwright dan Network Interception."""
import asyncio
import json
import logging
import random
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Response, async_playwright

from ..models import CrawledMention
from ..utils.accounts import parse_account_target
from ..utils.cookies import find_platform_cookie, to_playwright_storage_state

logger = logging.getLogger(__name__)


def _parse_tiktok_item(item: dict[str, Any]) -> CrawledMention | None:
    """Ekstraksi item data JSON TikTok menjadi CrawledMention."""
    # TikTok memuat data di item["item"] atau langsung di item
    node = item.get("item") if isinstance(item.get("item"), dict) else item
    item_id = node.get("id") or node.get("aweme_id")
    if not item_id:
        return None

    author_info = node.get("author") or {}
    author_handle = author_info.get("uniqueId") or author_info.get("secUid") or "tiktok_user"
    author_name = author_info.get("nickname") or author_handle

    create_time = node.get("createTime")
    pub_date = None
    if create_time:
        try:
            pub_date = datetime.fromtimestamp(int(create_time), tz=timezone.utc)
        except (ValueError, TypeError):
            pass

    stats = node.get("stats") or {}
    likes = int(stats.get("diggCount", 0) or 0)
    comments = int(stats.get("commentCount", 0) or 0)
    shares = int(stats.get("shareCount", 0) or 0)
    plays = int(stats.get("playCount", 0) or 0)

    url = f"https://www.tiktok.com/@{author_handle}/video/{item_id}"
    caption = node.get("desc") or ""

    image_post = node.get("imagePost")
    if isinstance(image_post, dict):
        imgs = image_post.get("images") or []
        media_type = "carousel" if len(imgs) > 1 else "image"
    else:
        media_type = "video"

    return CrawledMention(
        platform="tiktok",
        external_id=f"tt-{item_id}",
        url=url,
        content=caption,
        media_type=media_type,
        author=author_handle,
        author_name=author_name,
        published_at=pub_date,
        reach=plays,
        likes=likes,
        comments=comments,
        shares=shares,
        engagement_count=likes + comments + shares,
        raw={"video_id": item_id},
    )


def _extract_tiktok_ssr_items(data: Any) -> list[dict[str, Any]]:
    """Ekstrak item video TikTok dari JSON SSR (__UNIVERSAL_DATA_FOR_REHYDRATION__ / SIGI_STATE)."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _walk(obj: Any):
        if isinstance(obj, dict):
            if "ItemModule" in obj and isinstance(obj["ItemModule"], dict):
                for val in obj["ItemModule"].values():
                    if isinstance(val, dict):
                        vid = str(val.get("id") or val.get("aweme_id") or "")
                        if vid and vid not in seen:
                            seen.add(vid)
                            items.append(val)
            for key in ("itemList", "item_list"):
                if key in obj and isinstance(obj[key], list):
                    for val in obj[key]:
                        if isinstance(val, dict):
                            vid = str(val.get("id") or val.get("aweme_id") or "")
                            if vid and vid not in seen:
                                seen.add(vid)
                                items.append(val)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for entry in obj:
                _walk(entry)

    _walk(data)
    return items


async def _scrape_tiktok_async(
    query: str = "",
    since_date: datetime | None = None,
    max_posts: int | None = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
    account: str | None = None,
) -> list[CrawledMention]:
    """Menjalankan Playwright untuk intercept API pencarian atau profil akun TikTok."""
    if cookie_file is None:
        cookie_file = find_platform_cookie("tiktok")

    storage_state = None
    if cookie_file and Path(cookie_file).is_file():
        logger.debug("Memuat cookie TikTok dari: %s", cookie_file)
        storage_state = to_playwright_storage_state(cookie_file)

    mentions: list[CrawledMention] = []
    seen_ids: set[str] = set()
    main_post_count = 0

    def _record_item(raw_item: dict[str, Any]):
        nonlocal main_post_count
        if max_posts is not None and main_post_count >= max_posts:
            return
        mention = _parse_tiktok_item(raw_item)
        if mention and mention.external_id not in seen_ids:
            if since_date and mention.published_at and mention.published_at < since_date:
                return
            seen_ids.add(mention.external_id)
            mentions.append(mention)
            main_post_count += 1
            if on_mention:
                try:
                    on_mention(mention)
                except Exception as exc:
                    logger.debug("Error on_mention TikTok: %s", exc)
            logger.debug(
                "[TikTok] Terambil video: %s oleh @%s",
                mention.external_id,
                mention.author,
            )

    async def handle_response(response: Response):
        url = response.url
        if "/api/search/" in url or "/item_list" in url:
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct:
                    data = await response.json()
                    # Periksa struktur list (search_item_list atau data)
                    items = (
                        data.get("data")
                        or data.get("item_list")
                        or data.get("itemList")
                        or []
                    )
                    for raw_item in items:
                        _record_item(raw_item)
            except Exception as exc:
                logger.debug("Error saat membaca response TikTok JSON: %s", exc)
        elif "/api/comment/list" in url:
            try:
                ct = response.headers.get("content-type", "")
                if "application/json" in ct:
                    data = await response.json()
                    comments = data.get("comments") or []
                    for c in comments:
                        c_id = c.get("cid")
                        if not c_id:
                            continue
                        c_text = c.get("text", "")
                        user = c.get("user") or {}
                        c_author = user.get("unique_id") or "tiktok_user"
                        c_author_name = user.get("nickname") or c_author
                        c_likes = int(c.get("digg_count", 0) or 0)
                        create_time = c.get("create_time")
                        c_pub = datetime.fromtimestamp(int(create_time), tz=timezone.utc) if create_time else None
                        aweme_id = c.get("aweme_id")
                        parent_id = f"tt-{aweme_id}" if aweme_id else None
                        cm = CrawledMention(
                            platform="tiktok",
                            external_id=f"tt-cm-{c_id}",
                            url=f"https://www.tiktok.com/@{c_author}/video/{aweme_id}#comment-{c_id}" if aweme_id else "",
                            content=c_text,
                            author=c_author,
                            author_name=c_author_name,
                            published_at=c_pub,
                            likes=c_likes,
                            engagement_count=c_likes,
                            parent_external_id=parent_id,
                        )
                        if cm.external_id not in seen_ids:
                            seen_ids.add(cm.external_id)
                            mentions.append(cm)
                            if on_mention:
                                try:
                                    on_mention(cm)
                                except Exception as exc:
                                    logger.debug("Error on_mention TikTok comment: %s", exc)
                            logger.debug("[TikTok] Terambil komentar: %s oleh @%s", cm.external_id, cm.author)
            except Exception as exc:
                logger.debug("Error saat membaca komentar TikTok: %s", exc)

    if account:
        target = parse_account_target(account, "tiktok")
        target_url = target.url
    else:
        if not query:
            logger.error("TikTok memerlukan argumen --query atau --account.")
            return []
        encoded_query = urllib.parse.quote(query)
        target_url = f"https://www.tiktok.com/search?q={encoded_query}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 800},
        }
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.on("response", handle_response)

        logger.debug("Membuka TikTok: %s", target_url)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)

            # Ekstrak video awal yang tertanam di SSR JSON jika ada
            for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
                try:
                    raw_json = await page.locator(f"script#{script_id}").text_content(timeout=1500)
                    if raw_json:
                        for raw_item in _extract_tiktok_ssr_items(json.loads(raw_json)):
                            _record_item(raw_item)
                except Exception:
                    pass

            # Scroll ke bawah secara bertahap untuk memicu infinite scroll
            scroll_count = 0
            max_scrolls = max(8, (max_posts // 5) * 2) if max_posts is not None else None
            stagnant_limit = 5 if max_posts is None else 3
            stagnant_count = 0
            last_count = 0

            while (max_posts is None or main_post_count < max_posts) and (max_scrolls is None or scroll_count < max_scrolls):
                if stop_event and stop_event.is_set():
                    logger.info("[TikTok] Proses dihentikan oleh sinyal pengguna.")
                    break
                await page.evaluate("window.scrollBy(0, 1000)")
                scroll_count += 1
                jitter = round(random.uniform(2.5, 5.0), 2)
                for _ in range(int(jitter * 10)):
                    if stop_event and stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)

                current_count = main_post_count
                if current_count == last_count:
                    stagnant_count += 1
                    if stagnant_count >= stagnant_limit:
                        logger.debug(
                            "[Circuit Breaker] Tidak ada data baru setelah %d kali scroll berturut-turut. "
                            "Menghentikan scrolling TikTok (total didapatkan: %d).",
                            stagnant_limit,
                            current_count,
                        )
                        break
                else:
                    stagnant_count = 0
                    last_count = current_count

        except Exception as exc:
            logger.error("Terjadi error saat otomasi TikTok: %s", exc)
        finally:
            await browser.close()

    logger.debug("Total item TikTok yang berhasil dikumpulkan: %d", len(mentions))
    return mentions


def scrape_tiktok(
    query: str = "",
    since_date: datetime | None = None,
    max_posts: int | None = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
    account: str | None = None,
) -> list[CrawledMention]:
    """Wrapper sinkron untuk _scrape_tiktok_async."""
    return asyncio.run(
        _scrape_tiktok_async(
            query=query,
            since_date=since_date,
            max_posts=max_posts,
            cookie_file=cookie_file,
            headless=headless,
            on_mention=on_mention,
            stop_event=stop_event,
            account=account,
        )
    )
