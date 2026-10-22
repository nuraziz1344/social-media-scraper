"""CLI entrypoint untuk Social Media Crawler."""
import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .exporter import ProgressiveExporter, export_to_data_browser_csv, export_to_json
from .platforms.facebook import scrape_facebook
from .platforms.instagram import scrape_instagram_hashtag, scrape_instagram_user
from .platforms.threads import scrape_threads
from .platforms.tiktok import scrape_tiktok
from .platforms.twitter import scrape_twitter
from .platforms.youtube import scrape_youtube
from .utils.cookies import find_platform_cookie, to_playwright_storage_state

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawler")


def _calc_since_date(months: int, since_str: str | None) -> datetime:
    """Menghitung tanggal batas awal berdasarkan bulan atau input tanggal manual."""
    if since_str:
        try:
            return datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error("Format tanggal --since salah. Gunakan YYYY-MM-DD.")
            sys.exit(1)
    days = months * 30
    return datetime.now(timezone.utc) - timedelta(days=days)


def handle_scrape(args: argparse.Namespace):
    """Menangani perintah 'scrape'."""
    platform = args.platform.lower()
    query = args.query
    months = args.months
    since_date = _calc_since_date(months, args.since)
    until_date = None
    if args.until:
        try:
            until_date = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error("Format tanggal --until salah. Gunakan YYYY-MM-DD.")
            sys.exit(1)

    output_dir = Path(args.output_dir)
    cookie_file = Path(args.cookie) if args.cookie else None
    prefix = f"{platform}_{query.replace(' ', '_')[:30] if query else args.account}"
    exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)

    stop_event = threading.Event()

    def _listen_kb():
        try:
            import msvcrt
            while not stop_event.is_set():
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b"q", b"Q", b"\x1b", b"\x03"):
                        logger.warning("\n[SHORTCUT] Tombol stop ditekan! Menghentikan crawling dengan aman...")
                        stop_event.set()
                        break
                time.sleep(0.1)
        except Exception:
            pass

    threading.Thread(target=_listen_kb, daemon=True).start()

    logger.info("=== Memulai Crawling (Progressive Writing Aktif) ===")
    logger.info("Platform   : %s", platform)
    logger.info("Query      : %s", query or "-")
    logger.info("Batas Awal : %s (mundur %d bulan)", since_date.date(), months)
    logger.info("Max Items  : %d", args.max_results)
    logger.info("Target CSV : %s", exporter.csv_path.name)
    logger.info("Target JSON: %s", exporter.json_path.name)
    logger.info("Stop Aman  : Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja")

    try:
        if platform in ("youtube", "yt"):
            if not query:
                logger.error("Platform YouTube memerlukan argumen --query.")
                sys.exit(1)
            scrape_youtube(
                query=query,
                since_date=since_date,
                max_videos=args.max_results,
                include_comments=not args.no_comments,
                cookie_file=cookie_file,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )

        elif platform in ("x", "twitter"):
            if not query and not args.account:
                logger.error("Platform X memerlukan argumen --query atau --account.")
                sys.exit(1)
            q = f"from:{args.account}" if args.account and not query else query
            scrape_twitter(
                query=q,
                since_date=since_date.date(),
                until_date=until_date.date() if until_date else None,
                max_tweets=args.max_results,
                include_comments=not args.no_comments,
                cookie_file=cookie_file,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )

        elif platform in ("instagram", "ig"):
            if args.account:
                scrape_instagram_user(
                    username=args.account,
                    since_date=since_date,
                    max_posts=args.max_results,
                    include_comments=not args.no_comments,
                    cookie_file=cookie_file,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )
            elif query:
                scrape_instagram_hashtag(
                    hashtag=query,
                    since_date=since_date,
                    max_posts=args.max_results,
                    include_comments=not args.no_comments,
                    cookie_file=cookie_file,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )
            else:
                logger.error("Instagram memerlukan argumen --account (username) atau --query (hashtag).")
                sys.exit(1)

        elif platform in ("tiktok", "tt"):
            if not query:
                logger.error("TikTok memerlukan argumen --query.")
                sys.exit(1)
            scrape_tiktok(
                query=query,
                since_date=since_date,
                max_posts=args.max_results,
                cookie_file=cookie_file,
                headless=not args.headed,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )

        elif platform in ("facebook", "fb"):
            if not query:
                logger.error("Facebook memerlukan argumen --query.")
                sys.exit(1)
            scrape_facebook(
                query=query,
                since_date=since_date,
                max_posts=args.max_results,
                cookie_file=cookie_file,
                headless=not args.headed,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )

        elif platform in ("threads", "th"):
            if not query:
                logger.error("Threads memerlukan argumen --query.")
                sys.exit(1)
            scrape_threads(
                query=query,
                since_date=since_date,
                max_posts=args.max_results,
                cookie_file=cookie_file,
                headless=not args.headed,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )

        else:
            logger.error("Platform '%s' tidak didukung. Pilih: youtube, x, instagram, tiktok, facebook, threads.", platform)
            sys.exit(1)

    except KeyboardInterrupt:
        logger.warning(
            "\n[Pemberitahuan] Proses crawling dihentikan oleh pengguna (Ctrl+C). "
            "Data yang sudah terkumpul (%d item) tetap aman tersimpan di disk!",
            len(exporter.items),
        )
        stop_event.set()
    finally:
        csv_path, json_path, jsonl_path = exporter.finish()
        if exporter.items:
            logger.info("=== Crawling Selesai / Disimpan! ===")
            logger.info("  Total item tersimpan : %d", len(exporter.items))
            logger.info("  CSV Data Browser     : %s", csv_path)
            logger.info("  JSON pantaudev       : %s", json_path)
            logger.info("  (Siap di-import via: python manage.py load_data_browser_dataset --file %s)", csv_path)
        else:
            logger.warning("Tidak ada data yang berhasil dikumpulkan.")


def handle_cookies(args: argparse.Namespace):
    """Menangani pengecekan dan konversi cookie."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    cookies_dir = base_dir / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)

    if args.action == "list":
        logger.info("Folder cookies: %s", cookies_dir)
        platforms = ["x", "youtube", "instagram", "tiktok", "facebook", "threads"]
        print("\n=== STATUS FILE COOKIE (.txt) ===")
        for p in platforms:
            found = find_platform_cookie(p, base_dir=base_dir)
            if found:
                print(f"  [OK] {p.upper():<10} : Ditemukan -> {found.name}")
            else:
                print(f"  [--] {p.upper():<10} : Belum ada  (letakkan di cookies/{p}.txt)")
        print()

    elif args.action == "convert":
        if not args.input:
            logger.error("Argumen --input wajib untuk tindakan convert.")
            sys.exit(1)
        inp = Path(args.input)
        out = Path(args.output) if args.output else inp.with_suffix(".json")
        to_playwright_storage_state(inp, out)
        logger.info("Berhasil mengonversi %s -> %s (format Playwright)", inp, out)


def main():
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="Self-Hosted Social Media Crawler untuk data 6 bulan terakhir.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Subcommand: scrape
    p_scrape = subparsers.add_parser("scrape", help="Jalankan scraper untuk platform tertentu")
    p_scrape.add_argument(
        "--platform",
        "-p",
        required=True,
        choices=["youtube", "yt", "x", "twitter", "instagram", "ig", "tiktok", "tt", "facebook", "fb", "threads", "th"],
        help="Platform target",
    )
    p_scrape.add_argument("--query", "-q", default="", help="Kata kunci pencarian")
    p_scrape.add_argument("--account", "-a", default="", help="Username/akun target (Instagram / X)")
    p_scrape.add_argument("--months", "-m", type=int, default=6, help="Batas mundur dalam bulan (default: 6)")
    p_scrape.add_argument("--since", default=None, help="Batas tanggal awal manual (YYYY-MM-DD)")
    p_scrape.add_argument("--until", default=None, help="Batas tanggal akhir manual (YYYY-MM-DD)")
    p_scrape.add_argument("--max-results", "-n", type=int, default=50, help="Maksimal jumlah item utama")
    p_scrape.add_argument("--no-comments", action="store_true", help="Nonaktifkan pengambilan komentar")
    p_scrape.add_argument("--cookie", "-c", default=None, help="Path ke file cookies.txt spesifik")
    p_scrape.add_argument("--output-dir", "-o", default="output", help="Folder penyimpanan hasil JSON")
    p_scrape.add_argument("--headed", action="store_true", help="Buka browser Playwright dengan tampilan UI")
    p_scrape.set_defaults(func=handle_scrape)

    # Subcommand: cookies
    p_cookies = subparsers.add_parser("cookies", help="Manajemen file cookie")
    p_cookies.add_argument("action", choices=["list", "convert"], help="Tindakan: list atau convert")
    p_cookies.add_argument("--input", "-i", default=None, help="File input cookies.txt untuk convert")
    p_cookies.add_argument("--output", "-o", default=None, help="File output json untuk convert")
    p_cookies.set_defaults(func=handle_cookies)

    # Subcommand: interactive
    p_inter = subparsers.add_parser("interactive", help="Jalankan menu wizard interaktif")
    p_inter.set_defaults(func=lambda _: __import__("crawler.interactive", fromlist=["run_interactive"]).run_interactive())

    # Jika dipanggil tanpa argumen, default ke mode interaktif
    if len(sys.argv) == 1:
        from .interactive import run_interactive
        run_interactive()
        return

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

