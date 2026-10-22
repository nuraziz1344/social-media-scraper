"""Scraper Instagram menggunakan Playwright (Browser Session) dengan fallback ke instagrapi."""
import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Response, async_playwright

from ..models import CrawledMention
from ..utils.cookies import find_platform_cookie, to_playwright_storage_state
from ..utils.snowflake import decode_instagram_shortcode

logger = logging.getLogger(__name__)


def _find_media_nodes_recursive(data: Any, results: list[dict]):
    """Mencari objek media/post dalam respon GraphQL Instagram secara rekursif."""
    if isinstance(data, dict):
        if ("shortcode" in data or "code" in data) and (
            "edge_media_to_caption" in data
            or "caption" in data
            or "taken_at_timestamp" in data
            or "taken_at" in data
            or "user" in data
            or "owner" in data
            or "display_url" in data
            or "image_versions2" in data
            or "edge_liked_by" in data
            or "like_count" in data
        ):
            results.append(data)
        for v in data.values():
            _find_media_nodes_recursive(v, results)
    elif isinstance(data, list):
        for item in data:
            _find_media_nodes_recursive(item, results)


def _parse_ig_media_node(node: dict[str, Any]) -> CrawledMention | None:
    """Konversi node GraphQL Instagram menjadi CrawledMention."""
    code = node.get("shortcode") or node.get("code")
    node_id = code or node.get("id") or node.get("pk")
    if not code and not node_id:
        return None

    shortcode = str(code or node_id)
    post_url = f"https://www.instagram.com/p/{shortcode}/"

    # Caption
    caption_text = ""
    if "edge_media_to_caption" in node:
        edges = node.get("edge_media_to_caption", {}).get("edges", [])
        if edges and isinstance(edges[0], dict):
            caption_text = edges[0].get("node", {}).get("text", "")
    elif "caption" in node:
        cap = node["caption"]
        if isinstance(cap, dict):
            caption_text = cap.get("text", "")
        elif isinstance(cap, str):
            caption_text = cap

    # Author
    owner = node.get("owner") or node.get("user") or {}
    username = owner.get("username") if isinstance(owner, dict) else "instagram_user"
    full_name = owner.get("full_name") if isinstance(owner, dict) else username

    # Timestamp
    ts = node.get("taken_at_timestamp") or node.get("taken_at")
    pub_date = None
    if ts:
        try:
            pub_date = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            pass
    if pub_date is None and shortcode:
        pub_date = decode_instagram_shortcode(shortcode)

    # Engagement
    likes = 0
    if "edge_liked_by" in node:
        likes = int(node.get("edge_liked_by", {}).get("count", 0))
    elif "edge_media_preview_like" in node:
        likes = int(node.get("edge_media_preview_like", {}).get("count", 0))
    elif "like_count" in node:
        likes = int(node.get("like_count", 0))

    comments = 0
    if "edge_media_to_comment" in node:
        comments = int(node.get("edge_media_to_comment", {}).get("count", 0))
    elif "comment_count" in node:
        comments = int(node.get("comment_count", 0))

    return CrawledMention(
        platform="instagram",
        external_id=f"ig-{node_id}",
        url=post_url,
        content=caption_text.strip(),
        author=username,
        author_name=full_name,
        published_at=pub_date,
        likes=likes,
        comments=comments,
        engagement_count=likes + comments,
        raw={"shortcode": shortcode},
    )


def _parse_ig_comment_nodes(
    node: dict[str, Any],
    parent_external_id: str,
    post_url: str,
    max_comments: int = 25,
) -> list[CrawledMention]:
    """Ekstraksi komentar yang tersemat dalam node GraphQL Instagram jika ada."""
    comment_mentions: list[CrawledMention] = []
    comment_edges = []

    # 1. edge_media_to_parent_comment atau edge_media_to_comment
    for key in ("edge_media_to_parent_comment", "edge_media_to_comment", "edge_media_preview_comment"):
        if key in node and isinstance(node[key], dict):
            edges = node[key].get("edges", [])
            if edges:
                comment_edges = edges
                break

    for edge in comment_edges:
        if len(comment_mentions) >= max_comments:
            break
        c_node = edge.get("node", {}) if isinstance(edge, dict) else {}
        if not c_node:
            continue
        c_id = c_node.get("id") or c_node.get("pk")
        c_text = c_node.get("text", "") or ""
        owner = c_node.get("owner") or c_node.get("user") or {}
        c_user = owner.get("username", "instagram_user") if isinstance(owner, dict) else "instagram_user"
        c_name = owner.get("full_name", c_user) if isinstance(owner, dict) else c_user
        c_ts = c_node.get("created_at") or c_node.get("taken_at")
        pub_dt = None
        if c_ts:
            try:
                pub_dt = datetime.fromtimestamp(int(c_ts), tz=timezone.utc)
            except Exception:
                pass
        likes = int(c_node.get("like_count", 0) or 0)

        cm = CrawledMention(
            platform="instagram",
            external_id=f"ig-cm-{c_id}" if c_id else f"ig-cm-{abs(hash(c_text))}",
            url=f"{post_url}#pantau-comment-{c_id}" if c_id else post_url,
            content=c_text.strip(),
            author=c_user,
            author_name=c_name,
            published_at=pub_dt,
            likes=likes,
            engagement_count=likes,
            parent_external_id=parent_external_id,
        )
        comment_mentions.append(cm)

    # 2. Format list langsung seperti 'comments' atau 'preview_comments'
    if not comment_mentions:
        raw_list = node.get("comments") or node.get("preview_comments") or []
        if isinstance(raw_list, list):
            for c in raw_list:
                if len(comment_mentions) >= max_comments:
                    break
                if not isinstance(c, dict):
                    continue
                c_id = c.get("id") or c.get("pk")
                c_text = c.get("text", "") or ""
                c_user = c.get("user", {}).get("username", "instagram_user") if isinstance(c.get("user"), dict) else "instagram_user"
                c_ts = c.get("created_at")
                pub_dt = datetime.fromtimestamp(int(c_ts), tz=timezone.utc) if c_ts else None
                likes = int(c.get("comment_like_count", 0) or 0)
                cm = CrawledMention(
                    platform="instagram",
                    external_id=f"ig-cm-{c_id}" if c_id else f"ig-cm-{abs(hash(c_text))}",
                    url=f"{post_url}#pantau-comment-{c_id}" if c_id else post_url,
                    content=c_text.strip(),
                    author=c_user,
                    author_name=c_user,
                    published_at=pub_dt,
                    likes=likes,
                    engagement_count=likes,
                    parent_external_id=parent_external_id,
                )
                comment_mentions.append(cm)

    return comment_mentions


async def _scrape_instagram_playwright_async(
    target: str,
    is_hashtag: bool = True,
    since_date: datetime | None = None,
    max_posts: int = 50,
    include_comments: bool = True,
    max_comments_per_post: int = 25,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Crawl Instagram menggunakan sesi web browser Playwright dengan cookies."""
    if cookie_file is None:
        cookie_file = find_platform_cookie("instagram")

    storage_state = None
    if cookie_file and Path(cookie_file).is_file():
        logger.info("Memuat cookie Instagram dari: %s", cookie_file)
        storage_state = to_playwright_storage_state(cookie_file)

    mentions: list[CrawledMention] = []
    seen_ids: set[str] = set()

    async def handle_response(response: Response):
        url = response.url
        if "graphql" in url or "/api/v1/" in url:
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct or "graphql" in url:
                    data = await response.json()
                    nodes: list[dict] = []
                    _find_media_nodes_recursive(data, nodes)
                    for n in nodes:
                        m = _parse_ig_media_node(n)
                        if m and m.external_id not in seen_ids:
                            if since_date and m.published_at and m.published_at < since_date:
                                continue
                            seen_ids.add(m.external_id)
                            mentions.append(m)
                            if on_mention:
                                try:
                                    on_mention(m)
                                except Exception as exc:
                                    logger.debug("Error on_mention IG: %s", exc)
                            logger.info(
                                "[Instagram] Terambil post: %s oleh @%s (%s likes)",
                                m.external_id,
                                m.author,
                                m.likes,
                            )

                            # Ekstraksi komentar jika include_comments aktif
                            if include_comments:
                                c_mentions = _parse_ig_comment_nodes(
                                    n,
                                    parent_external_id=m.external_id,
                                    post_url=m.url,
                                    max_comments=max_comments_per_post,
                                )
                                for cm in c_mentions:
                                    if cm.external_id not in seen_ids:
                                        seen_ids.add(cm.external_id)
                                        mentions.append(cm)
                                        if on_mention:
                                            try:
                                                on_mention(cm)
                                            except Exception as exc:
                                                logger.debug("Error on_mention IG comment: %s", exc)
                                if c_mentions:
                                    logger.info("  [Instagram] Terambil %d komentar untuk post %s", len(c_mentions), m.external_id)
            except Exception as exc:
                logger.debug("Error membaca response IG GraphQL: %s", exc)

    clean_target = target.lstrip("#").strip()
    if is_hashtag:
        nav_url = f"https://www.instagram.com/explore/tags/{clean_target}/"
    else:
        nav_url = f"https://www.instagram.com/{clean_target}/"

    logger.info("Membuka Instagram di URL: %s...", nav_url)

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
            "viewport": {"width": 1280, "height": 900},
        }
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.on("response", handle_response)

        try:
            await page.goto(nav_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)

            scroll_count = 0
            max_scrolls = max(8, (max_posts // 4) * 2)
            stagnant_count = 0
            last_count = 0

            while len(mentions) < max_posts and scroll_count < max_scrolls:
                if stop_event and stop_event.is_set():
                    logger.info("[Instagram] Proses dihentikan oleh sinyal pengguna.")
                    break
                # Coba ambil link post dari DOM sebagai fallback jika belum tertangkap API
                try:
                    post_links = await page.locator('a[href*="/p/"], a[href*="/reel/"]').all()
                    for pl in post_links:
                        if stop_event and stop_event.is_set():
                            break
                        href = await pl.get_attribute("href")
                        if not href:
                            continue
                        # Ekstrak shortcode dari /p/CODE/ atau /reel/CODE/
                        parts = [p for p in href.strip("/").split("/") if p]
                        if len(parts) >= 2:
                            shortcode = parts[1]
                            ext_id = f"ig-{shortcode}"
                            if ext_id not in seen_ids:
                                pub_date = decode_instagram_shortcode(shortcode)
                                if since_date and pub_date and pub_date < since_date:
                                    continue
                                seen_ids.add(ext_id)
                                img_el = pl.locator("img")
                                alt_text = ""
                                if await img_el.count() > 0:
                                    alt_text = await img_el.first.get_attribute("alt") or ""
                                
                                # Coba ekstrak username dari alt text (misal: 'Photo by @user on ...' atau 'Photo by User on ...')
                                author_from_alt = "instagram_user"
                                author_name_from_alt = None
                                m_alt_by = re.search(r"Photo (?:by|shared by) @?([a-zA-Z0-9_\.]+)", alt_text, re.IGNORECASE)
                                if m_alt_by:
                                    author_from_alt = m_alt_by.group(1)
                                    author_name_from_alt = author_from_alt

                                mention_obj = CrawledMention(
                                    platform="instagram",
                                    external_id=ext_id,
                                    url=f"https://www.instagram.com/p/{shortcode}/",
                                    content=alt_text.strip(),
                                    author=author_from_alt,
                                    author_name=author_name_from_alt,
                                    published_at=pub_date,
                                    raw={"shortcode": shortcode},
                                )
                                mentions.append(mention_obj)
                                if on_mention:
                                    try:
                                        on_mention(mention_obj)
                                    except Exception as exc:
                                        logger.debug("Error on_mention IG DOM: %s", exc)
                                logger.info("[Instagram DOM] Terambil post link: /p/%s", shortcode)
                except Exception:
                    pass

                await page.evaluate("window.scrollBy(0, 1000)")
                scroll_count += 1

                # Jeda aman anti-bot acak (2.5 - 5.0 detik) interruptible
                jitter = round(random.uniform(2.5, 5.0), 2)
                logger.debug("Jeda aman anti-bot Instagram: %.2fs", jitter)
                for _ in range(int(jitter * 10)):
                    if stop_event and stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)

                current_count = len(mentions)
                if current_count == last_count:
                    stagnant_count += 1
                    if stagnant_count >= 3:
                        logger.info(
                            "[Circuit Breaker] Tidak ada item Instagram baru setelah 3x scroll berturut-turut. "
                            "Menghentikan scrolling (total terkumpul: %d).",
                            current_count,
                        )
                        break
                else:
                    stagnant_count = 0
                    last_count = current_count

        except Exception as exc:
            logger.error("Terjadi error saat otomasi Instagram: %s", exc)
        finally:
            await browser.close()

    logger.info("Total item Instagram yang berhasil dikumpulkan: %d", len(mentions))
    return mentions


def scrape_instagram_hashtag(
    hashtag: str,
    since_date: datetime | None = None,
    max_posts: int = 50,
    include_comments: bool = True,
    max_comments_per_post: int = 25,
    cookie_file: Path | str | None = None,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Crawl postingan Instagram berdasarkan hashtag menggunakan Playwright browser session."""
    return asyncio.run(
        _scrape_instagram_playwright_async(
            target=hashtag,
            is_hashtag=True,
            since_date=since_date,
            max_posts=max_posts,
            include_comments=include_comments,
            max_comments_per_post=max_comments_per_post,
            cookie_file=cookie_file,
            headless=True,
            on_mention=on_mention,
            stop_event=stop_event,
        )
    )


def scrape_instagram_user(
    username: str,
    since_date: datetime | None = None,
    max_posts: int = 50,
    include_comments: bool = True,
    max_comments_per_post: int = 30,
    cookie_file: Path | str | None = None,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Crawl postingan dan profil Instagram menggunakan Playwright browser session."""
    return asyncio.run(
        _scrape_instagram_playwright_async(
            target=username,
            is_hashtag=False,
            since_date=since_date,
            max_posts=max_posts,
            include_comments=include_comments,
            max_comments_per_post=max_comments_per_post,
            cookie_file=cookie_file,
            headless=True,
            on_mention=on_mention,
            stop_event=stop_event,
        )
    )
