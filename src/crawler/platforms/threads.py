"""Scraper Threads (Meta) menggunakan Playwright dan GraphQL interception."""
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
from ..utils.cookies import find_platform_cookie, persist_playwright_cookies, to_playwright_storage_state
from ..utils.snowflake import decode_instagram_shortcode

logger = logging.getLogger(__name__)


def _parse_threads_post(post: dict[str, Any], parent_external_id: str | None = None) -> CrawledMention | None:
    """Konversi objek post GraphQL Threads ke CrawledMention."""
    pk = post.get("pk") or post.get("id")
    code = post.get("code")
    if not pk and not code:
        return None

    post_code = code or str(pk)
    user_info = post.get("user") or {}
    username = user_info.get("username") or "threads_user"
    full_name = user_info.get("full_name") or username

    caption_obj = post.get("caption") or {}
    text = caption_obj.get("text") if isinstance(caption_obj, dict) else str(caption_obj or "")

    taken_at = post.get("taken_at")
    pub_date = None
    if taken_at:
        try:
            pub_date = datetime.fromtimestamp(int(taken_at), tz=timezone.utc)
        except (ValueError, TypeError):
            pass
    if pub_date is None and post_code:
        pub_date = decode_instagram_shortcode(post_code)

    likes = int(post.get("like_count", 0) or 0)
    replies = int(post.get("reply_count", 0) or 0)
    shares = int(post.get("repost_count", 0) or 0)

    url = f"https://www.threads.net/@{username}/post/{post_code}"

    if post.get("carousel_media"):
        media_type = "carousel"
    elif post.get("video_versions"):
        media_type = "video"
    elif (post.get("image_versions2") or {}).get("candidates") or post.get("media_type") == 1:
        media_type = "image"
    elif (post.get("text_post_app_info") or {}).get("link_preview_attachment"):
        media_type = "link"
    else:
        media_type = "text"

    return CrawledMention(
        platform="threads",
        external_id=f"th-{post_code}",
        url=url,
        content=text,
        media_type=media_type,
        author=username,
        author_name=full_name,
        published_at=pub_date,
        likes=likes,
        comments=replies,
        shares=shares,
        engagement_count=likes + replies + shares,
        parent_external_id=parent_external_id,
        raw={"pk": pk, "code": code},
    )


def _extract_threads_posts_and_replies(obj: Any, results: list[tuple[dict, str | None]]):
    """Mencari thread_items atau post untuk membedakan postingan utama dan komentar balasan."""
    if isinstance(obj, dict):
        if "thread_items" in obj and isinstance(obj["thread_items"], list) and obj["thread_items"]:
            parent_id = None
            for idx, ti in enumerate(obj["thread_items"]):
                if isinstance(ti, dict) and "post" in ti and isinstance(ti["post"], dict):
                    p = ti["post"]
                    if idx == 0:
                        code = p.get("code") or p.get("pk") or p.get("id")
                        parent_id = f"th-{code}" if code else None
                        results.append((p, None))
                    else:
                        results.append((p, parent_id))
        elif "post" in obj and isinstance(obj["post"], dict):
            results.append((obj["post"], None))
        else:
            for v in obj.values():
                _extract_threads_posts_and_replies(v, results)
    elif isinstance(obj, list):
        for item in obj:
            _extract_threads_posts_and_replies(item, results)


async def _scrape_threads_async(
    query: str = "",
    since_date: datetime | None = None,
    max_posts: int | None = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
    account: str | None = None,
) -> list[CrawledMention]:
    """Scrape postingan Threads via Playwright dan GraphQL/SSR interception."""
    if cookie_file is None:
        cookie_file = find_platform_cookie("threads") or find_platform_cookie("instagram")

    storage_state = None
    if cookie_file and Path(cookie_file).is_file():
        logger.debug("Memuat cookie Threads dari: %s", cookie_file)
        storage_state = to_playwright_storage_state(cookie_file)

    if account:
        target = parse_account_target(account, "threads")
        target_url = target.url
    else:
        if not query:
            logger.error("Threads memerlukan argumen --query atau --account.")
            return []
        encoded_query = urllib.parse.quote(query)
        target_url = f"https://www.threads.com/search?q={encoded_query}"

    mentions: list[CrawledMention] = []
    seen_ids: set[str] = set()
    main_post_count = 0

    def _record_raw_posts(raw_posts: list[tuple[dict, str | None]]):
        nonlocal main_post_count
        for p_dict, parent_id in raw_posts:
            if parent_id is None and max_posts is not None and main_post_count >= max_posts:
                break
            mention = _parse_threads_post(p_dict, parent_external_id=parent_id)
            if mention and mention.external_id not in seen_ids:
                if since_date and mention.published_at and mention.published_at < since_date:
                    continue
                seen_ids.add(mention.external_id)
                mentions.append(mention)
                if parent_id is None:
                    main_post_count += 1
                if on_mention:
                    try:
                        on_mention(mention)
                    except Exception as exc:
                        logger.debug("Error on_mention Threads: %s", exc)
                if parent_id:
                    logger.debug("[Threads] Menangkap balasan/komentar: %s (@%s -> %s)", mention.external_id, mention.author, parent_id)
                else:
                    logger.debug("[Threads] Menangkap post: %s (@%s)", mention.external_id, mention.author)

    async def handle_response(response: Response):
        url = response.url
        if "graphql/query" in url:
            try:
                data = await response.json()
                data_obj = data.get("data", {})
                # Cari struktur thread_items di data_obj
                raw_posts: list[tuple[dict, str | None]] = []
                _extract_threads_posts_and_replies(data_obj, raw_posts)
                _record_raw_posts(raw_posts)
            except Exception as exc:
                logger.debug("Error saat membaca payload GraphQL Threads: %s", exc)

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

        logger.debug("Membuka Threads: %s", target_url)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)

            # Ekstrak post awal yang tertanam di SSR <script type="application/json"> jika ada
            try:
                script_texts = await page.locator('script[type="application/json"]').all_text_contents()
                for st in script_texts:
                    if "thread_items" not in st:
                        continue
                    try:
                        parsed = json.loads(st)
                        ssr_posts: list[tuple[dict, str | None]] = []
                        _extract_threads_posts_and_replies(parsed, ssr_posts)
                        _record_raw_posts(ssr_posts)
                    except Exception:
                        pass
            except Exception:
                pass

            # Scroll ke bawah secara bertahap
            scroll_count = 0
            max_scrolls = max(8, (max_posts // 5) * 2) if max_posts is not None else None
            stagnant_limit = 5 if max_posts is None else 3
            stagnant_count = 0
            last_count = 0

            while (max_posts is None or main_post_count < max_posts) and (max_scrolls is None or scroll_count < max_scrolls):
                if stop_event and stop_event.is_set():
                    logger.info("[Threads] Proses dihentikan oleh sinyal pengguna.")
                    break
                await page.evaluate("window.scrollBy(0, 1000)")
                scroll_count += 1
                jitter = round(random.uniform(2.5, 4.5), 2)
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
                            "Menghentikan scrolling Threads (total didapatkan: %d).",
                            stagnant_limit,
                            current_count,
                        )
                        break
                else:
                    stagnant_count = 0
                    last_count = current_count

        except Exception as exc:
            logger.error("Terjadi error saat otomasi Threads: %s", exc)
        finally:
            await persist_playwright_cookies(context, cookie_file, "threads")
            await browser.close()

    logger.debug("Total item Threads yang berhasil dikumpulkan: %d", len(mentions))
    return mentions


def scrape_threads(
    query: str = "",
    since_date: datetime | None = None,
    max_posts: int | None = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
    account: str | None = None,
) -> list[CrawledMention]:
    """Wrapper sinkron untuk _scrape_threads_async."""
    return asyncio.run(
        _scrape_threads_async(
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
