"""Scraper Facebook menggunakan Playwright dan Network/DOM Interception."""
import asyncio
import json
import logging
import random
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Response, async_playwright

from ..models import CrawledMention
from ..utils.cookies import find_platform_cookie, to_playwright_storage_state

logger = logging.getLogger(__name__)


def _extract_text_recursive(data: Any, target_key: str = "text") -> list[str]:
    """Helper untuk mencari nilai key tertentu dalam struktur nested JSON."""
    results = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == target_key and isinstance(v, str):
                results.append(v)
            else:
                results.extend(_extract_text_recursive(v, target_key))
    elif isinstance(data, list):
        for item in data:
            results.extend(_extract_text_recursive(item, target_key))
    return results


def _extract_facebook_story_details(story: dict[str, Any]) -> dict[str, Any]:
    """Ekstrak detail postingan Facebook dari struktur story GraphQL jika ada."""
    actors = story.get("actors") or []
    author_name = None
    author_handle = "facebook_user"
    author_url = None
    if actors and isinstance(actors[0], dict):
        author_name = actors[0].get("name")
        author_url = actors[0].get("url")
        if author_name:
            author_handle = author_name.lower().replace(" ", "_")

    post_url = story.get("story_post_url") or story.get("url")
    creation_time = story.get("creation_time")
    pub_date = None
    if creation_time:
        try:
            pub_date = datetime.fromtimestamp(int(creation_time), tz=timezone.utc)
        except Exception:
            pass

    return {
        "author_name": author_name,
        "author_handle": author_handle,
        "author_url": author_url,
        "post_url": post_url,
        "published_at": pub_date,
    }


async def _scrape_facebook_async(
    query: str,
    since_date: datetime | None = None,
    max_posts: int = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Scrape postingan Facebook dengan Playwright dan GraphQL interception."""
    if cookie_file is None:
        cookie_file = find_platform_cookie("facebook")

    if not cookie_file or not Path(cookie_file).is_file():
        logger.warning(
            "File cookie Facebook tidak ditemukan di cookies/facebook.txt. "
            "Facebook mewajibkan login untuk melihat hasil pencarian."
        )

    storage_state = None
    if cookie_file and Path(cookie_file).is_file():
        logger.info("Memuat cookie Facebook dari: %s", cookie_file)
        storage_state = to_playwright_storage_state(cookie_file)

    mentions: list[CrawledMention] = []
    seen_ids: set[str] = set()

    async def handle_response(response: Response):
        url = response.url
        if "/api/graphql/" in url:
            try:
                text = await response.text()
                # Respons FB GraphQL bisa berupa multi-line JSON
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Navigasi data feed FB
                    data_obj = data.get("data", {})
                    # Cari nodes atau stories
                    stories = []
                    if "serpResponse" in data_obj:
                        stories.append(data_obj["serpResponse"])
                    elif "viewer" in data_obj:
                        stories.append(data_obj["viewer"])
                    else:
                        stories.append(data_obj)

                    for story in stories:
                        texts = _extract_text_recursive(story, "text")
                        if not texts:
                            continue
                        # Gabungkan teks yang relevan
                        combined_content = "\n".join(texts)
                        if len(combined_content.strip()) < 10:
                            continue

                        story_details = _extract_facebook_story_details(story) if isinstance(story, dict) else {}
                        pub_date = story_details.get("published_at")
                        if since_date and pub_date and pub_date < since_date:
                            continue

                        # Generate pseudo-id jika ID eksplisit terbungkus
                        item_id = str(hash(combined_content[:100]))[-10:]
                        external_id = f"fb-{item_id}"
                        if external_id in seen_ids:
                            continue

                        seen_ids.add(external_id)
                        author_handle = story_details.get("author_handle") or "facebook_user"
                        author_name = story_details.get("author_name") or author_handle
                        post_url = story_details.get("post_url") or story_details.get("author_url") or "https://www.facebook.com"

                        m_obj = CrawledMention(
                            platform="facebook",
                            external_id=external_id,
                            url=post_url,
                            content=combined_content,
                            author=author_handle,
                            author_name=author_name,
                            published_at=pub_date,
                            raw={"sample_text": texts[:2]},
                        )
                        mentions.append(m_obj)
                        if on_mention:
                            try:
                                on_mention(m_obj)
                            except Exception as exc:
                                logger.debug("Error on_mention FB: %s", exc)
                        logger.info("[Facebook] Menangkap post GraphQL (%s oleh %s)...", external_id, author_name)
            except Exception as exc:
                logger.debug("Error saat membaca payload GraphQL FB: %s", exc)

    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.facebook.com/search/posts/?q={encoded_query}"

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

        logger.info("Membuka pencarian Facebook: %s", search_url)
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(5)

            # Ekstraksi berbasis DOM sebagai pelengkap yang andal
            scroll_count = 0
            max_scrolls = max(8, (max_posts // 5) * 2)
            stagnant_count = 0
            last_count = 0

            while len(mentions) < max_posts and scroll_count < max_scrolls:
                # Ambil card postingan dari feed HTML
                post_cards = await page.query_selector_all("div[role='feed'] > div, div[role='article']")
                for card in post_cards:
                    try:
                        # 1. Ekstrak konten bersih via div[dir="auto"] untuk menghindari teks noise 'Facebook Facebook...'
                        dir_autos = await card.query_selector_all('div[dir="auto"]')
                        extracted_texts = []
                        for d_el in dir_autos:
                            t_val = (await d_el.inner_text() or "").strip()
                            if t_val and t_val not in extracted_texts and len(t_val) > 10 and not t_val.startswith("Facebook"):
                                extracted_texts.append(t_val)

                        clean_content = "\n".join(extracted_texts).strip()
                        if not clean_content or len(clean_content) < 15:
                            # Fallback ke inner_text jika div[dir=auto] tidak ditemukan
                            raw_text = (await card.inner_text() or "").strip()
                            # Buang baris yang hanya berisi 'Facebook'
                            clean_lines = [line.strip() for line in raw_text.splitlines() if line.strip() and line.strip() != "Facebook"]
                            clean_content = "\n".join(clean_lines)

                        if len(clean_content) < 15:
                            continue

                        card_hash = str(abs(hash(clean_content[:80])))
                        ext_id = f"fb-{card_hash}"
                        if ext_id in seen_ids:
                            continue

                        # 2. Ekstrak penulis (author name & handle) dari link profil card
                        author_name = ""
                        author_handle = "facebook_user"
                        author_url = ""
                        post_href = ""

                        all_links = await card.query_selector_all("a")
                        for a_el in all_links:
                            href = await a_el.get_attribute("href") or ""
                            txt = (await a_el.inner_text() or "").strip()

                            # Deteksi link author/profil
                            if not author_name and txt and len(txt) < 50:
                                if "profile.php" in href or "/user/" in href or (
                                    href.startswith("https://www.facebook.com/")
                                    and not any(x in href for x in ["/posts/", "/photo", "/groups/", "/watch", "/reel", "/story", "hashtag", "search"])
                                ):
                                    author_name = txt
                                    author_url = href.split("&__cft__")[0].split("?__cft__")[0]
                                    m_user = re.search(r"facebook\.com/([a-zA-Z0-9_\.]+)", author_url)
                                    if m_user and m_user.group(1) not in ("profile.php", "groups", "watch", "photo"):
                                        author_handle = m_user.group(1)
                                    elif "id=" in href:
                                        m_id = re.search(r"id=(\d+)", href)
                                        if m_id:
                                            author_handle = f"fb_{m_id.group(1)}"

                            # Deteksi tautan spesifik postingan
                            if not post_href:
                                if any(x in href for x in ["/posts/", "story_fbid=", "set=pcb.", "/photo/?"]):
                                    m_pcb = re.search(r"set=pcb\.(\d+)", href)
                                    if m_pcb and "/groups/" in href:
                                        m_grp = re.search(r"/groups/(\d+)", href)
                                        if m_grp:
                                            post_href = f"https://www.facebook.com/groups/{m_grp.group(1)}/posts/{m_pcb.group(1)}/"
                                    elif m_pcb:
                                        post_href = href.split("&__cft__")[0]
                                    else:
                                        post_href = href.split("&__cft__")[0].split("?__cft__")[0]

                        # Fallback author jika belum ketemu link profil
                        if not author_name:
                            h_el = await card.query_selector("h2, h3, h4, strong")
                            if h_el:
                                author_name = (await h_el.inner_text() or "").split("\n")[0].strip()

                        if not author_name:
                            author_name = "Facebook User"
                            author_handle = "facebook_user"
                        elif author_handle == "facebook_user":
                            slug = re.sub(r"[^a-zA-Z0-9_]", "_", author_name.lower()).strip("_")
                            author_handle = slug or "facebook_user"

                        if not post_href:
                            post_href = author_url or "https://www.facebook.com"

                        seen_ids.add(ext_id)
                        m_obj = CrawledMention(
                            platform="facebook",
                            external_id=ext_id,
                            url=post_href,
                            content=clean_content,
                            author=author_handle,
                            author_name=author_name,
                            published_at=None,
                        )
                        mentions.append(m_obj)
                        if on_mention:
                            try:
                                on_mention(m_obj)
                            except Exception as exc:
                                logger.debug("Error on_mention FB DOM: %s", exc)
                        logger.info("[Facebook] Menangkap post dari feed: %s (%s - %s)...", ext_id, author_name, post_href[:60])
                    except Exception:
                        pass

                if stop_event and stop_event.is_set():
                    logger.info("[Facebook] Proses dihentikan oleh sinyal pengguna.")
                    break

                await page.evaluate("window.scrollBy(0, 1200)")
                scroll_count += 1
                jitter = round(random.uniform(3.0, 6.0), 2)
                for _ in range(int(jitter * 10)):
                    if stop_event and stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)

                current_count = len(mentions)
                if current_count == last_count:
                    stagnant_count += 1
                    if stagnant_count >= 3:
                        logger.info(
                            "[Circuit Breaker] Tidak ada data baru setelah 3 kali scroll berturut-turut. "
                            "Menghentikan scrolling Facebook (total didapatkan: %d).",
                            current_count,
                        )
                        break
                else:
                    stagnant_count = 0
                    last_count = current_count

        except Exception as exc:
            logger.error("Terjadi error saat otomasi Facebook: %s", exc)
        finally:
            await browser.close()

    logger.info("Total item Facebook yang berhasil dikumpulkan: %d", len(mentions))
    return mentions


def scrape_facebook(
    query: str,
    since_date: datetime | None = None,
    max_posts: int = 50,
    cookie_file: Path | str | None = None,
    headless: bool = True,
    on_mention: Any = None,
    stop_event: Any = None,
) -> list[CrawledMention]:
    """Wrapper sinkron untuk _scrape_facebook_async."""
    return asyncio.run(
        _scrape_facebook_async(
            query=query,
            since_date=since_date,
            max_posts=max_posts,
            cookie_file=cookie_file,
            headless=headless,
            on_mention=on_mention,
            stop_event=stop_event,
        )
    )
