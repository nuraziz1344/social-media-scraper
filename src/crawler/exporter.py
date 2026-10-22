"""Eksportir data hasil crawling ke format JSON standar, CSV Data Browser, dan format legacy dataset."""
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .models import CrawledMention

logger = logging.getLogger(__name__)

PLATFORM_DISPLAY_NAMES = {
    "youtube": "YouTube",
    "x": "X (Twitter)",
    "twitter": "X (Twitter)",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "threads": "Threads",
}


class ProgressiveExporter:
    """Exporter yang menulis item secara streaming dan langsung melakukan flush ke disk.

    Setiap item baru langsung ditulis ke file CSV Data Browser dan JSONL detik itu juga,
    sehingga jika proses dihentikan paksa (Ctrl+C atau kill) data yang sudah terkumpul
    tetap utuh dan tersimpan di disk.
    """

    def __init__(self, output_dir: Path | str, prefix: str = "crawled"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.items: list[CrawledMention] = []

        self.csv_path = self.output_dir / f"{prefix}_{self.timestamp}.csv"
        self.jsonl_path = self.output_dir / f"{prefix}_{self.timestamp}.jsonl"
        self.json_path = self.output_dir / f"{prefix}_{self.timestamp}.json"

        # Buka file CSV dan tulis header langsung
        self._csv_file = open(self.csv_path, "w", encoding="utf-8-sig", newline="")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            "ID Eksternal",
            "Waktu Input",
            "Waktu Posting",
            "Platform",
            "Penulis / Akun",
            "Jenis Konten",
            "Isi Sebutan",
            "Total Interaksi",
            "Tautan Asli",
        ])
        self._csv_file.flush()

        # Buka file JSONL
        self._jsonl_file = open(self.jsonl_path, "w", encoding="utf-8")
        self._jsonl_file.flush()

    def write_mention(self, m: CrawledMention):
        """Tulis 1 mention ke CSV dan JSONL secara instan dan flush ke disk."""
        self.items.append(m)

        # 1. Tulis ke CSV
        ext_id = m.external_id
        waktu_input = m.crawled_at.strftime("%Y-%m-%d %H:%M:%S") if m.crawled_at else ""
        waktu_posting = m.published_at.strftime("%Y-%m-%d %H:%M:%S") if m.published_at else ""
        platform_str = PLATFORM_DISPLAY_NAMES.get(m.platform.lower(), m.platform.capitalize())
        author_str = f"@{m.author.lstrip('@')}" if m.author else "@tidak_dikenal"
        jenis_konten = "Komentar" if m.parent_external_id else "Posting/Artikel"
        isi = m.content or ""
        total_interaksi = m.engagement_count or (m.likes + m.comments + m.shares)
        tautan = m.url or ""

        self._csv_writer.writerow([
            ext_id,
            waktu_input,
            waktu_posting,
            platform_str,
            author_str,
            jenis_konten,
            isi,
            total_interaksi,
            tautan,
        ])
        self._csv_file.flush()

        # 2. Tulis ke JSONL
        payload = m.to_pantaudev_payload()
        self._jsonl_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._jsonl_file.flush()

    def finish(self) -> tuple[Path, Path, Path]:
        """Finalisasi: dump full JSON dan tutup stream file."""
        if not self._csv_file.closed:
            self._csv_file.close()
        if not self._jsonl_file.closed:
            self._jsonl_file.close()

        # Dump consolidated JSON jika ada item
        if self.items:
            payload = {
                "count": len(self.items),
                "exported_at": datetime.now().isoformat(),
                "items": [m.to_pantaudev_payload() for m in self.items],
            }
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        return self.csv_path, self.json_path, self.jsonl_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finish()



def export_to_json(
    mentions: Sequence[CrawledMention],
    output_dir: Path | str,
    prefix: str = "crawled",
    save_raw: bool = False,
) -> Path:
    """Simpan kumpulan CrawledMention ke file JSON terformat untuk pantaudev."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{prefix}_{timestamp}.json"
    target_path = out_dir / file_name

    items_payload = []
    for m in mentions:
        data = m.to_pantaudev_payload()
        if save_raw:
            data["raw"] = m.raw
        items_payload.append(data)

    payload = {
        "count": len(items_payload),
        "exported_at": datetime.now().isoformat(),
        "items": items_payload,
    }

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info("Berhasil mengekspor %d item ke %s", len(mentions), target_path)
    return target_path


def export_to_data_browser_csv(
    mentions: Sequence[CrawledMention],
    output_dir: Path | str,
    prefix: str = "data-browser",
) -> Path:
    """Eksport ke CSV dengan format identik dengan data-browser-20261012-0728.csv.

    Format ini dapat langsung diimport ke pantaudev menggunakan command:
    python manage.py load_data_browser_dataset --file <path_ke_csv>
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{prefix}_{timestamp}.csv"
    target_path = out_dir / file_name

    headers = [
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

    with open(target_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for m in mentions:
            ext_id = m.external_id
            waktu_input = m.crawled_at.strftime("%Y-%m-%d %H:%M:%S") if m.crawled_at else ""
            waktu_posting = m.published_at.strftime("%Y-%m-%d %H:%M:%S") if m.published_at else ""
            platform_str = PLATFORM_DISPLAY_NAMES.get(m.platform.lower(), m.platform.capitalize())
            author_str = f"@{m.author.lstrip('@')}" if m.author else "@tidak_dikenal"
            jenis_konten = "Komentar" if m.parent_external_id else "Posting/Artikel"
            isi = m.content or ""
            total_interaksi = m.engagement_count or (m.likes + m.comments + m.shares)
            tautan = m.url or ""

            writer.writerow([
                ext_id,
                waktu_input,
                waktu_posting,
                platform_str,
                author_str,
                jenis_konten,
                isi,
                total_interaksi,
                tautan,
            ])

    logger.info("Berhasil mengekspor format Data Browser CSV: %s (%d baris)", target_path, len(mentions))
    return target_path


def export_to_twitter_dataset_json(
    mentions: Sequence[CrawledMention],
    output_dir: Path | str,
    prefix: str = "kulon_progo_twitter",
) -> Path:
    """Eksport ke format JSON dataset legacy seperti kulon_progo_twitter_2000.json."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{prefix}_{timestamp}.json"
    target_path = out_dir / file_name

    # Kelompokkan komentar ke post induknya
    comments_by_parent: dict[str, list[str]] = {}
    for m in mentions:
        if m.parent_external_id:
            comments_by_parent.setdefault(m.parent_external_id, []).append(m.content)

    items = []
    for m in mentions:
        if m.parent_external_id:
            continue  # Komentar dimasukkan ke daftar_comment post induk

        # Ekstrak ID numerik dari tw-{id} jika ada
        raw_id = m.external_id.replace("tw-", "")
        post_comments = comments_by_parent.get(m.external_id, [])

        item = {
            "id": raw_id,
            "username": m.author.lstrip("@"),
            "post_content": m.content,
            "source_link_post": m.url,
            "link_image_content_video": [],
            "platform": "twitter",
            "total_like": m.likes,
            "total_view": m.reach,
            "total_comment": len(post_comments) or m.comments,
            "datetime_post": m.published_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if m.published_at else None,
            "tanggal_pencarian": m.crawled_at.strftime("%Y-%m-%dT%H:%M:%S.000Z") if m.crawled_at else "",
            "daftar_comment": post_comments,
        }
        items.append(item)

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    logger.info("Berhasil mengekspor format Twitter Dataset JSON: %s (%d tweet)", target_path, len(items))
    return target_path
