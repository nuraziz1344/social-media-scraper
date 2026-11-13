"""Utilitas untuk menggabungkan (merge & deduplicate) hasil crawling multi-platform.

Mendukung ekspor per-platform maupun versi combined (semua platform dalam 1 file).

Format nama file output:
- Per-platform  : YYYYMMDD-<platform>.<format> (contoh: 20261012-twitter.json)
- Versi Combined: YYYYMMDD-combined.<format>  (contoh: 20261012-combined.json)

Format skema output:
- Format JSON:
  Root element: Bare array [ {...}, {...} ]
  Keys: ['id', 'username', 'post_content', 'source_link_post', 'link_image_content_video',
         'platform', 'total_like', 'total_view', 'total_comment', 'datetime_post',
         'tanggal_pencarian', 'daftar_comment', 'parent_external_id']
- Format CSV: siap diimpor via fitur Data Browser pantaudev
  Headers: ['ID Eksternal', 'Waktu Input', 'Waktu Posting', 'Platform', 'Penulis / Akun',
            'Jenis Konten', 'Isi Sebutan', 'Total Interaksi', 'Tautan Asli']
"""
import csv
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .utils.snowflake import decode_instagram_shortcode

logger = logging.getLogger("crawler.merger")

# Nama tampilan platform sesuai standar Data Browser pantaudev
PLATFORM_DISPLAY_NAMES = {
    "youtube": "YouTube",
    "x": "X (Twitter)",
    "twitter": "X (Twitter)",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "threads": "Threads",
}

DATA_BROWSER_CSV_HEADERS = [
    "ID Eksternal",
    "Waktu Input",
    "Waktu Posting",
    "Platform",
    "Penulis / Akun",
    "Jenis Konten",
    "Isi Sebutan",
    "Total Interaksi",
    "Tautan Asli",
]


def normalize_platform_slug(p: str) -> str:
    p = (p or "").lower().strip()
    if p in ("x", "twitter"):
        return "twitter"
    if p in ("yt", "youtube"):
        return "youtube"
    if p in ("ig", "instagram"):
        return "instagram"
    if p in ("tt", "tiktok"):
        return "tiktok"
    if p in ("th", "threads"):
        return "threads"
    if p in ("fb", "facebook"):
        return "facebook"
    return p


def infer_platform(file_name: str, item: dict) -> str:
    prefix = item.get("external_id", "").split("-")[0].lower()
    url = item.get("url", "").lower()
    fn = file_name.lower()

    if prefix == "yt" or "youtube.com" in url or "youtu.be" in url or fn.startswith("youtube"):
        return "youtube"
    if prefix == "tw" or "twitter.com" in url or "x.com" in url or fn.startswith("x_"):
        return "twitter"
    if prefix == "ig" or "instagram.com" in url or fn.startswith("instagram"):
        return "instagram"
    if prefix == "th" or "threads.net" in url or "threads.com" in url or fn.startswith("threads"):
        return "threads"
    if prefix == "tt" or "tiktok.com" in url or fn.startswith("tiktok"):
        return "tiktok"
    if prefix == "fb" or "facebook.com" in url or fn.startswith("facebook"):
        return "facebook"
    return normalize_platform_slug(prefix) or "unknown"


def clean_id(external_id: str) -> str:
    """Bersihkan prefix platform dari ID (misal tw-210956... -> 210956...)."""
    if not external_id:
        return ""
    return re.sub(r"^(tw|yt|ig|tt|th|fb)-", "", str(external_id))


def format_iso_utc(dt_str: str | None) -> str | None:
    """Format ke standar ISO 8601 UTC dengan .000Z.

    Jika dt_str kosong/None, kembalikan None agar diserialisasi menjadi null di JSON.
    """
    if not dt_str:
        return None
    try:
        clean = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return dt_str


def format_datetime_str(dt_str: str) -> str:
    """Format ke standar YYYY-MM-DD HH:MM:SS seperti di Data Browser."""
    if not dt_str:
        return ""
    try:
        clean = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str


def extract_date_tag(dir_name: str) -> str:
    """Ekstrak YYYYMMDD dari nama folder (misal '2026-10-12' -> '20261012')."""
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", dir_name)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return datetime.now().strftime("%Y%m%d")


def list_raw_crawl_json_files(input_dir: Path | str) -> list[Path]:
    """Kembalikan daftar file .json hasil crawling asli (abaikan file hasil merge)."""
    in_dir = Path(input_dir)
    if not in_dir.exists() or not in_dir.is_dir():
        return []
    return sorted([
        f for f in in_dir.glob("*.json")
        if not f.name.startswith("merged") and not re.match(r"^\d{8}-", f.name)
    ])


def load_items_by_platform(input_dir: Path | str, target_platform: str = "all") -> dict[str, list[tuple[str, dict]]]:
    in_dir = Path(input_dir)
    if not in_dir.exists() or not in_dir.is_dir():
        raise FileNotFoundError(f"Folder tidak ditemukan: {in_dir}")

    json_files = list_raw_crawl_json_files(in_dir)
    if not json_files:
        raise ValueError(f"Tidak ada file .json hasil crawling ditemukan di {in_dir}")

    logger.info("Memproses %d file JSON dari %s...", len(json_files), in_dir)

    target_plat = normalize_platform_slug(target_platform)
    platform_buckets: dict[str, list[tuple[str, dict]]] = {}

    for jf in json_files:
        parts = jf.stem.split("_")
        query_hint = "_".join(parts[1:-2]) if len(parts) >= 4 else jf.stem

        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Gagal membaca %s: %s", jf.name, e)
            continue

        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for it in items:
            plat = infer_platform(jf.name, it)
            if target_plat not in ("all", "combined") and plat != target_plat:
                continue
            platform_buckets.setdefault(plat, []).append((query_hint, it))

    return platform_buckets


def deduplicate_platform_items(
    items_with_queries: list[tuple[str, dict]],
) -> tuple[dict[str, dict], dict[str, list[dict]], dict[str, dict], int, int]:
    """Deduplikasi item per platform.

    Returns:
        posts: dict item postingan utama (tanpa parent_external_id)
        comments_by_parent: dict komentar yang dikelompokkan ke parent post id
        all_unique: dict seluruh item unik (posts + comments)
        raw_total: jumlah data mentah sebelum deduplikasi
        duplicates_count: jumlah duplikat yang dibersihkan
    """
    raw_by_id: dict[str, dict] = {}
    queries_by_id: dict[str, set[str]] = {}
    raw_total = len(items_with_queries)
    duplicates_count = 0

    for query_hint, it in items_with_queries:
        eid = it.get("external_id")
        if not eid:
            continue

        item_obj = dict(it)

        if eid not in raw_by_id:
            raw_by_id[eid] = item_obj
            queries_by_id[eid] = {query_hint}
        else:
            duplicates_count += 1
            queries_by_id[eid].add(query_hint)
            existing = raw_by_id[eid]

            if not existing.get("content") and item_obj.get("content"):
                existing["content"] = item_obj["content"]

            ex_eng = existing.get("engagement_count", 0) or 0
            new_eng = item_obj.get("engagement_count", 0) or 0
            if new_eng > ex_eng:
                existing["engagement_count"] = new_eng
                existing["engagement"] = item_obj.get("engagement", existing.get("engagement"))

            existing["reach"] = max(existing.get("reach", 0) or 0, item_obj.get("reach", 0) or 0)

            if item_obj.get("crawled_at", "") > existing.get("crawled_at", ""):
                existing["crawled_at"] = item_obj.get("crawled_at")

    comments_by_parent: dict[str, list[dict]] = {}
    posts: dict[str, dict] = {}

    for eid, it in raw_by_id.items():
        it["_queries"] = sorted(list(queries_by_id.get(eid, [])))

        parent_id = it.get("parent_external_id")
        if parent_id and parent_id in raw_by_id:
            comments_by_parent.setdefault(parent_id, []).append({
                "comment_content": it.get("content", "") or "",
                "user_name": (it.get("author") or "").lstrip("@"),
            })
        else:
            posts[eid] = it

    return posts, comments_by_parent, raw_by_id, raw_total, duplicates_count


def transform_to_reference_json(
    platform: str,
    posts: dict[str, dict],
    comments_by_parent: dict[str, list[dict]],
) -> list[dict]:
    """Konversi ke format JSON referensi pantaudev."""
    output_items = []

    sorted_posts = sorted(
        posts.values(),
        key=lambda x: x.get("published_at", "") or "",
        reverse=True,
    )

    for p in sorted_posts:
        eid = p.get("external_id", "")
        plat_slug = (p.get("platform") or platform).lower()
        if plat_slug == "x":
            plat_slug = "twitter"

        eng = p.get("engagement", {}) or {}
        likes = int(eng.get("likes", 0) or 0)
        reach = int(p.get("reach", 0) or 0)
        parent_comments = comments_by_parent.get(eid, [])
        total_comments = max(len(parent_comments), int(eng.get("comments", 0) or 0))

        pub_raw = p.get("published_at")
        crawl_raw = p.get("crawled_at")
        is_same_or_near = False
        if pub_raw and crawl_raw:
            try:
                dt_p = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                dt_c = datetime.fromisoformat(crawl_raw.replace("Z", "+00:00"))
                if abs((dt_p - dt_c).total_seconds()) < 2:
                    is_same_or_near = True
            except Exception:
                if pub_raw[:16] == crawl_raw[:16]:
                    is_same_or_near = True

        if (not pub_raw or is_same_or_near) and plat_slug in ("instagram", "threads"):
            code = clean_id(eid)
            decoded_dt = decode_instagram_shortcode(code)
            if decoded_dt:
                pub_raw = decoded_dt.isoformat()

        item = {
            "id": clean_id(eid),
            "username": (p.get("author") or "").lstrip("@"),
            "post_content": p.get("content", "") or "",
            "source_link_post": p.get("url", "") or "",
            "link_image_content_video": [],
            "platform": plat_slug,
            "total_like": likes,
            "total_view": reach,
            "total_comment": total_comments,
            "datetime_post": format_iso_utc(pub_raw),
            "tanggal_pencarian": format_iso_utc(crawl_raw),
            "daftar_comment": parent_comments,
            "parent_external_id": p.get("parent_external_id"),
        }
        output_items.append(item)

    return output_items


def transform_to_data_browser_csv(
    platform: str,
    all_unique_items: dict[str, dict],
) -> list[dict]:
    """Konversi ke format CSV Data Browser pantaudev."""
    output_rows = []

    sorted_items = sorted(
        all_unique_items.values(),
        key=lambda x: x.get("published_at", "") or "",
        reverse=True,
    )

    for it in sorted_items:
        eid = it.get("external_id", "")
        plat_raw = (it.get("platform") or platform).lower()
        platform_display = PLATFORM_DISPLAY_NAMES.get(plat_raw, plat_raw.capitalize())

        raw_author = (it.get("author") or "").strip()
        author_str = f"@{raw_author.lstrip('@')}" if raw_author else "@tidak_dikenal"

        is_comment = bool(it.get("parent_external_id"))
        jenis_konten = "Komentar/Balasan" if is_comment else "Posting/Artikel"

        eng = it.get("engagement", {}) or {}
        calc_eng = (
            int(it.get("engagement_count", 0) or 0)
            or (int(eng.get("likes", 0) or 0) + int(eng.get("comments", 0) or 0) + int(eng.get("shares", 0) or 0))
        )

        row = {
            "ID Eksternal": eid,
            "Waktu Input": format_datetime_str(it.get("crawled_at", "")),
            "Waktu Posting": format_datetime_str(it.get("published_at", "")),
            "Platform": platform_display,
            "Penulis / Akun": author_str,
            "Jenis Konten": jenis_konten,
            "Isi Sebutan": it.get("content", "") or "",
            "Total Interaksi": str(calc_eng),
            "Tautan Asli": it.get("url", "") or "",
        }
        output_rows.append(row)

    return output_rows


def export_json_file(items: list[dict], out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    logger.info("  -> JSON: %s (%s bytes, %d item)", out_path.name, f"{out_path.stat().st_size:,}", len(items))


def export_csv_file(rows: list[dict], out_path: Path):
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DATA_BROWSER_CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("  -> CSV : %s (%s bytes, %d baris)", out_path.name, f"{out_path.stat().st_size:,}", len(rows))


def merge_and_export(
    input_dir: str | Path,
    fmt: str = "json",
    target_platform: str = "all",
    combined: bool = False,
    output_dir: str | Path | None = None,
    per_platform: bool | None = None,
) -> list[Path]:
    """Gabungkan dan deduplikasi file JSON hasil crawling di `input_dir`."""
    in_dir = Path(input_dir)
    out_dir = Path(output_dir) if output_dir else in_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    date_tag = extract_date_tag(in_dir.name)
    platform_buckets = load_items_by_platform(in_dir, target_platform=target_platform)

    if not platform_buckets:
        logger.warning("Tidak ada data ditemukan untuk platform '%s' di %s", target_platform, in_dir)
        return []

    generated_files: list[Path] = []
    should_do_combined = combined or (target_platform == "combined")
    should_do_per_platform = (not should_do_combined) if per_platform is None else per_platform

    # Deduplikasi sekali per platform agar bisa dipakai oleh mode per-platform maupun combined
    deduped_by_platform: dict[str, tuple[dict[str, dict], dict[str, list[dict]], dict[str, dict], int, int]] = {}
    for plat, items_with_queries in sorted(platform_buckets.items()):
        posts, comments_by_parent, all_unique, raw_total, dup_count = deduplicate_platform_items(items_with_queries)
        deduped_by_platform[plat] = (posts, comments_by_parent, all_unique, raw_total, dup_count)
        nested_comments_count = sum(len(c) for c in comments_by_parent.values())
        logger.info(
            "[%s] %d raw item -> %d post & %d komentar bersarang (%d duplikat dibersihkan)",
            plat.upper(),
            raw_total,
            len(posts),
            nested_comments_count,
            dup_count,
        )

    if should_do_per_platform:
        logger.info("Menjalankan merge per-platform untuk tanggal [%s] (Format: %s)...", date_tag, fmt.upper())
        for plat, (posts, comments_by_parent, all_unique, _, _) in deduped_by_platform.items():
            file_name = f"{date_tag}-{plat}.{fmt}"
            target_path = out_dir / file_name

            if fmt == "json":
                json_items = transform_to_reference_json(plat, posts, comments_by_parent)
                export_json_file(json_items, target_path)
            elif fmt == "csv":
                csv_rows = transform_to_data_browser_csv(plat, all_unique)
                export_csv_file(csv_rows, target_path)

            generated_files.append(target_path)

    if should_do_combined:
        logger.info("Menjalankan merge versi COMBINED untuk tanggal [%s] (Format: %s)...", date_tag, fmt.upper())
        all_combined_json: list[dict] = []
        all_combined_csv: list[dict] = []

        for plat, (posts, comments_by_parent, all_unique, _, _) in deduped_by_platform.items():
            if fmt == "json":
                all_combined_json.extend(transform_to_reference_json(plat, posts, comments_by_parent))
            elif fmt == "csv":
                all_combined_csv.extend(transform_to_data_browser_csv(plat, all_unique))

        combined_filename = f"{date_tag}-combined.{fmt}"
        combined_path = out_dir / combined_filename

        if fmt == "json":
            all_combined_json.sort(key=lambda x: x.get("datetime_post", "") or "", reverse=True)
            export_json_file(all_combined_json, combined_path)
        elif fmt == "csv":
            all_combined_csv.sort(key=lambda x: x.get("Waktu Posting", "") or "", reverse=True)
            export_csv_file(all_combined_csv, combined_path)

        generated_files.append(combined_path)

    return generated_files
