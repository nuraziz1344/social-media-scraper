#!/usr/bin/env python
"""Script utilitas untuk menggabungkan (merge & deduplicate) hasil crawling multi-platform.

Mendukung ekspor per-platform maupun versi combined (semua platform dalam 1 file).

Format nama file output:
- Per-platform  : YYYYMMDD-<platform>.<format> (contoh: 20261012-twitter.json)
- Versi Combined: YYYYMMDD-combined.<format>  (contoh: 20261012-combined.json)
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from crawler.merger import (  # noqa: E402, F401
    DATA_BROWSER_CSV_HEADERS,
    PLATFORM_DISPLAY_NAMES,
    clean_id,
    deduplicate_platform_items,
    export_csv_file,
    export_json_file,
    extract_date_tag,
    format_datetime_str,
    format_iso_utc,
    infer_platform,
    load_items_by_platform,
    merge_and_export,
    normalize_platform_slug,
    transform_to_data_browser_csv,
    transform_to_reference_json,
)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Merge dan deduplikasi hasil crawling multi-platform.")
    parser.add_argument(
        "--input-dir",
        "-i",
        required=True,
        help="Direktori target output crawling (wajib)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "csv"],
        default="json",
        help="Format output: json atau csv (default: json)",
    )
    parser.add_argument(
        "--platform",
        "-p",
        choices=["all", "combined", "twitter", "x", "youtube", "instagram", "tiktok", "threads", "facebook"],
        default="all",
        help="Target platform, 'combined' untuk 1 file gabungan, atau 'all' per-platform (default: all)",
    )
    parser.add_argument(
        "--combined",
        "-c",
        action="store_true",
        help="Hasilkan juga file gabungan YYYYMMDD-combined.<format> (semua platform dalam 1 file)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="Direktori penyimpanan file output (opsional, default: sama dengan input-dir)",
    )
    args = parser.parse_args()

    merge_and_export(
        input_dir=args.input_dir,
        fmt=args.format.lower(),
        target_platform=args.platform,
        combined=args.combined,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
