#!/usr/bin/env python
"""Script utilitas untuk membersihkan data hasil crawling dari file CSV.

Kriteria data yang dibersihkan:
1. Timestamp posting kosong atau 100% sama dengan timestamp crawl/input.
2. Body kosong (postingan video/gambar tanpa caption/isi teks).
3. Author/username invalid (kosong atau bernilai hardcoded placeholder seperti @instagram_user, @facebook_user).
4. Link/URL postingan invalid (kosong, malformed, atau hanya homepage tanpa mengarah ke konten spesifik).

Format output:
- File bersih: <nama_file>-cleaned.csv
- File ditolak (opsional): <nama_file>-rejected.csv (dilengkapi kolom 'Alasan Ditolak')
"""
from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")

# Daftar placeholder author default dari crawler / fallbacks
DEFAULT_PLACEHOLDER_AUTHORS = {
    "@instagram_user",
    "@facebook_user",
    "@tidak_dikenal",
    "@unknown",
    "@undefined",
    "@null",
    "@none",
    "@nan",
    "@n/a",
    "@anonymous",
    "@anon",
    "@crawler",
    "@admin",
    "instagram_user",
    "facebook_user",
    "tidak_dikenal",
    "unknown",
    "undefined",
    "null",
    "none",
    "nan",
    "n/a",
    "anonymous",
    "anon",
}

# Alias nama kolom untuk fleksibilitas schema
COL_ALIASES = {
    "post_time": [
        "waktu posting",
        "published_at",
        "datetime_post",
        "post_time",
        "created_at",
        "tanggal posting",
    ],
    "input_time": [
        "waktu input",
        "crawled_at",
        "tanggal_pencarian",
        "crawl_time",
        "input_time",
    ],
    "body": [
        "isi sebutan",
        "content",
        "post_content",
        "body",
        "text",
        "caption",
    ],
    "author": [
        "penulis / akun",
        "penulis",
        "author",
        "username",
        "akun",
        "user",
    ],
    "url": [
        "tautan asli",
        "url",
        "source_link_post",
        "link",
        "link_post",
    ],
    "platform": [
        "platform",
        "sumber",
        "source",
    ],
}


def resolve_column_mapping(fieldnames: list[str]) -> dict[str, str]:
    """Petakan nama kolom aktual di CSV ke kunci kanonikal."""
    mapping: dict[str, str] = {}
    normalized = {f.strip().lower(): f for f in fieldnames if f}

    for key, aliases in COL_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[key] = normalized[alias]
                break

    return mapping


def parse_datetime_loose(dt_str: str) -> datetime | None:
    """Coba parsing format datetime umum (ISO 8601 atau YYYY-MM-DD HH:MM:SS)."""
    if not dt_str:
        return None
    clean = dt_str.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
            try:
                return datetime.strptime(clean, fmt)
            except Exception:
                pass
    return None


def validate_timestamp(post_time_raw: str, input_time_raw: str) -> list[str]:
    """Kriteria 1: Validasi waktu posting."""
    reasons = []
    p = (post_time_raw or "").strip()
    i = (input_time_raw or "").strip()

    if not p or p.lower() in ("none", "null", "nan", "-"):
        reasons.append("timestamp_kosong")
        return reasons

    # Cek kesamaan string langsung
    if i and p == i:
        reasons.append("timestamp_sama_dengan_input")
        return reasons

    # Cek kesamaan via parsed datetime
    dt_p = parse_datetime_loose(p)
    dt_i = parse_datetime_loose(i)
    if dt_p and dt_i and dt_p == dt_i:
        reasons.append("timestamp_sama_dengan_input")

    return reasons


def validate_body(body_raw: str) -> list[str]:
    """Kriteria 2: Validasi body / isi sebutan."""
    reasons = []
    b = (body_raw or "").strip()
    if not b or b.lower() in ("none", "null", "nan", "-", "[]"):
        reasons.append("body_kosong")
    return reasons


def validate_author(
    author_raw: str,
    placeholders: set[str],
    lenient: bool = False,
    allowed_placeholders: set[str] | None = None,
) -> list[str]:
    """Kriteria 3: Validasi author/username."""
    reasons = []
    a = (author_raw or "").strip()
    if not a or a == "@" or a.lower() in ("none", "null", "nan", "-"):
        reasons.append("author_kosong")
        return reasons

    if not lenient:
        norm = a.lower()
        handle_norm = f"@{norm.lstrip('@')}"
        bare_norm = norm.lstrip("@")

        # Cek jika ada dalam allowed_placeholders (diizinkan secara eksplisit)
        if allowed_placeholders:
            if handle_norm in allowed_placeholders or bare_norm in allowed_placeholders:
                return reasons

        if handle_norm in placeholders or bare_norm in placeholders:
            reasons.append(f"author_placeholder({a})")

    return reasons


def validate_url(url_raw: str) -> list[str]:
    """Kriteria 4: Validasi link/url postingan."""
    reasons = []
    u = (url_raw or "").strip()
    if not u or u.lower() in ("none", "null", "nan", "-"):
        reasons.append("url_kosong")
        return reasons

    try:
        parsed = urlparse(u)
    except Exception:
        reasons.append("url_malformed")
        return reasons

    if not parsed.scheme or not parsed.netloc:
        reasons.append("url_malformed")
        return reasons

    if parsed.scheme.lower() not in ("http", "https"):
        reasons.append("url_malformed")
        return reasons

    # Cek apakah hanya domain / homepage tanpa path postingan
    path_clean = parsed.path.strip("/")
    if not path_clean and not parsed.query and not parsed.fragment:
        reasons.append("url_hanya_homepage")

    return reasons


def validate_row(
    row: dict[str, str],
    col_map: dict[str, str],
    placeholders: set[str],
    lenient_authors: bool = False,
    allowed_placeholders: set[str] | None = None,
) -> list[str]:
    """Validasi satu baris data terhadap seluruh kriteria.
    
    Returns:
        List alasan penolakan (kosong jika baris valid).
    """
    reasons: list[str] = []

    # 1. Timestamp
    col_post = col_map.get("post_time")
    col_input = col_map.get("input_time")
    p_time = row.get(col_post, "") if col_post else ""
    i_time = row.get(col_input, "") if col_input else ""
    reasons.extend(validate_timestamp(p_time, i_time))

    # 2. Body
    col_body = col_map.get("body")
    body = row.get(col_body, "") if col_body else ""
    reasons.extend(validate_body(body))

    # 3. Author
    col_author = col_map.get("author")
    author = row.get(col_author, "") if col_author else ""
    reasons.extend(validate_author(author, placeholders, lenient_authors, allowed_placeholders))

    # 4. URL
    col_url = col_map.get("url")
    url = row.get(col_url, "") if col_url else ""
    reasons.extend(validate_url(url))

    return reasons


class CleanResult:
    def __init__(self, input_file: Path, output_file: Path | None, rejected_file: Path | None):
        self.input_file = input_file
        self.output_file = output_file
        self.rejected_file = rejected_file
        self.total_rows = 0
        self.clean_rows = 0
        self.rejected_rows = 0
        self.reason_counts: Counter[str] = Counter()
        self.rejected_by_platform: Counter[str] = Counter()

    def add_rejected(self, reasons: list[str], platform: str = "Unknown"):
        self.rejected_rows += 1
        self.rejected_by_platform[platform] += 1
        for r in reasons:
            cat = r.split("(")[0]
            self.reason_counts[cat] += 1

    def add_clean(self):
        self.clean_rows += 1


def clean_csv(
    input_path: Path,
    output_path: Path | None = None,
    rejected_path: Path | None = None,
    suffix: str = "-cleaned",
    save_rejected: bool = False,
    rejected_suffix: str = "-rejected",
    lenient_authors: bool = False,
    additional_placeholders: list[str] | None = None,
    allowed_placeholders: list[str] | None = None,
    dry_run: bool = False,
) -> CleanResult:
    """Proses pembersihan file CSV tunggal."""
    if not input_path.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {input_path}")

    # Tentukan path output
    stem = input_path.stem
    ext = input_path.suffix or ".csv"
    if output_path is None:
        output_path = input_path.parent / f"{stem}{suffix}{ext}"

    if save_rejected and rejected_path is None:
        rejected_path = input_path.parent / f"{stem}{rejected_suffix}{ext}"

    result = CleanResult(input_path, output_path if not dry_run else None, rejected_path if not dry_run else None)

    # Siapkan set placeholders
    placeholders = set(DEFAULT_PLACEHOLDER_AUTHORS)
    if additional_placeholders:
        for p in additional_placeholders:
            clean_p = p.strip().lower()
            if clean_p:
                placeholders.add(clean_p)
                placeholders.add(f"@{clean_p.lstrip('@')}")

    allowed_set = set()
    if allowed_placeholders:
        for p in allowed_placeholders:
            clean_p = p.strip().lower()
            if clean_p:
                allowed_set.add(clean_p)
                allowed_set.add(f"@{clean_p.lstrip('@')}")

    # Baca file input
    with open(input_path, "r", encoding="utf-8-sig", newline="") as f_in:
        reader = csv.DictReader(f_in)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            logger.warning("File CSV kosong atau tidak memiliki header: %s", input_path.name)
            return result

        col_map = resolve_column_mapping(fieldnames)
        col_plat = col_map.get("platform")

        clean_data: list[dict[str, str]] = []
        rejected_data: list[dict[str, str]] = []

        for row in reader:
            result.total_rows += 1
            plat = (row.get(col_plat, "") if col_plat else "") or "Unknown"
            reasons = validate_row(
                row=row,
                col_map=col_map,
                placeholders=placeholders,
                lenient_authors=lenient_authors,
                allowed_placeholders=allowed_set,
            )

            if reasons:
                result.add_rejected(reasons, plat)
                if save_rejected and not dry_run:
                    row_with_reason = dict(row)
                    row_with_reason["Alasan Ditolak"] = "; ".join(reasons)
                    rejected_data.append(row_with_reason)
            else:
                result.add_clean()
                if not dry_run:
                    clean_data.append(row)

    # Tulis hasil jika bukan dry-run
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(clean_data)

        if save_rejected and rejected_path:
            rejected_fieldnames = list(fieldnames)
            if "Alasan Ditolak" not in rejected_fieldnames:
                rejected_fieldnames.append("Alasan Ditolak")
            rejected_path.parent.mkdir(parents=True, exist_ok=True)
            with open(rejected_path, "w", encoding="utf-8-sig", newline="") as f_rej:
                writer_rej = csv.DictWriter(f_rej, fieldnames=rejected_fieldnames)
                writer_rej.writeheader()
                writer_rej.writerows(rejected_data)

    return result


def print_summary(result: CleanResult, dry_run: bool = False):
    """Cetak tabel ringkasan hasil pembersihan ke konsol."""
    pct_clean = (result.clean_rows / result.total_rows * 100) if result.total_rows else 0
    pct_rej = (result.rejected_rows / result.total_rows * 100) if result.total_rows else 0

    mode_str = "[DRY-RUN PREVIEW]" if dry_run else "[BERHASIL DIPROSES]"
    print("\n" + "=" * 65)
    print(f" {mode_str} {result.input_file.name}")
    print("=" * 65)
    print(f" Total Baris Awal       : {result.total_rows:,}")
    print(f" Baris Bersih (Disimpan): {result.clean_rows:,} ({pct_clean:.1f}%)")
    print(f" Baris Ditolak (Dibuang): {result.rejected_rows:,} ({pct_rej:.1f}%)")

    if not dry_run and result.output_file:
        print(f" -> File Bersih         : {result.output_file}")
    if not dry_run and result.rejected_file:
        print(f" -> File Ditolak        : {result.rejected_file}")

    if result.reason_counts:
        print("-" * 65)
        print(" Rincian Alasan Penolakan (bisa terjadi > 1 per baris):")
        labels = {
            "timestamp_kosong": "1. Timestamp posting kosong",
            "timestamp_sama_dengan_input": "1. Timestamp sama dengan input/crawl",
            "body_kosong": "2. Body postingan kosong (caption kosong)",
            "author_kosong": "3. Author / username kosong",
            "author_placeholder": "3. Author hardcoded / placeholder",
            "url_kosong": "4. URL kosong",
            "url_hanya_homepage": "4. URL hanya homepage / root domain",
            "url_malformed": "4. URL format invalid / malformed",
        }
        for code, count in result.reason_counts.most_common():
            label = labels.get(code, code)
            print(f"    - {label:<45}: {count:,}")

    if result.rejected_by_platform:
        print("-" * 65)
        print(" Baris Ditolak per Platform:")
        for plat, count in result.rejected_by_platform.most_common():
            print(f"    - {plat:<30}: {count:,}")
    print("=" * 65 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pembersih data CSV hasil crawling berdasarkan kualitas timestamp, body, author, dan URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_files",
        nargs="+",
        help="Path file CSV atau pola glob (misal: output/2026-10-22_kebakaran/20261022-combined.csv)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Path output kustom (hanya berlaku jika memproses 1 file input)",
    )
    parser.add_argument(
        "--suffix",
        type=str,
        default="-cleaned",
        help="Suffix nama file bersih (default: '-cleaned')",
    )
    parser.add_argument(
        "--save-rejected",
        action="store_true",
        help="Simpan baris data yang dibuang ke file *-rejected.csv lengkap dengan kolom alasan",
    )
    parser.add_argument(
        "--rejected-suffix",
        type=str,
        default="-rejected",
        help="Suffix nama file data ditolak (default: '-rejected')",
    )
    parser.add_argument(
        "--lenient-authors",
        action="store_true",
        help="Mode non-strict author: abaikan filter placeholder author (pertahankan semua author selama tidak kosong)",
    )
    parser.add_argument(
        "--allow-placeholder-authors",
        type=str,
        default=None,
        help="Daftar author placeholder yang diizinkan (dipisahkan koma, misal: 'instagram_user')",
    )
    parser.add_argument(
        "--additional-placeholders",
        type=str,
        default=None,
        help="Daftar tambahan author placeholder yang ingin dibuang (dipisahkan koma)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Jalankan simulasi tanpa menulis file apapun ke disk",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Jangan cetak ringkasan ke konsol",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Expand glob patterns
    all_files: list[Path] = []
    for pattern in args.input_files:
        matches = glob.glob(pattern)
        if matches:
            all_files.extend(Path(m) for m in matches if m.lower().endswith(".csv"))
        else:
            p = Path(pattern)
            if p.exists() and p.suffix.lower() == ".csv":
                all_files.append(p)
            else:
                logger.warning("Pola/file tidak ditemukan: %s", pattern)

    if not all_files:
        logger.error("Tidak ada file CSV yang ditemukan untuk diproses.")
        sys.exit(1)

    if args.output and len(all_files) > 1:
        logger.error("Opsi --output hanya dapat digunakan jika memproses tepat 1 file CSV.")
        sys.exit(1)

    allowed_list = [a.strip() for a in args.allow_placeholder_authors.split(",")] if args.allow_placeholder_authors else None
    add_list = [a.strip() for a in args.additional_placeholders.split(",")] if args.additional_placeholders else None

    total_clean_all = 0
    total_rejected_all = 0

    for fpath in all_files:
        try:
            res = clean_csv(
                input_path=fpath,
                output_path=args.output if len(all_files) == 1 else None,
                suffix=args.suffix,
                save_rejected=args.save_rejected,
                rejected_suffix=args.rejected_suffix,
                lenient_authors=args.lenient_authors,
                allowed_placeholders=allowed_list,
                additional_placeholders=add_list,
                dry_run=args.dry_run,
            )
            total_clean_all += res.clean_rows
            total_rejected_all += res.rejected_rows

            if not args.quiet:
                print_summary(res, dry_run=args.dry_run)
        except Exception as e:
            logger.error("Gagal memproses %s: %s", fpath.name, e, exc_info=True)

    if len(all_files) > 1 and not args.quiet:
        print(f"\nSelesai memproses {len(all_files)} file CSV.")
        print(f"Total baris bersih: {total_clean_all:,} | Total baris ditolak: {total_rejected_all:,}\n")


if __name__ == "__main__":
    main()
