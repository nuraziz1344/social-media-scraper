"""CLI entrypoint untuk Social Media Crawler."""
import argparse
import concurrent.futures
import logging
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .exporter import ProgressiveExporter, export_to_data_browser_csv, export_to_json
from .merger import list_raw_crawl_json_files, merge_and_export
from .platforms.facebook import scrape_facebook
from .platforms.instagram import scrape_instagram_hashtag, scrape_instagram_user
from .platforms.threads import scrape_threads
from .platforms.tiktok import scrape_tiktok
from .platforms.twitter import scrape_twitter
from .platforms.youtube import scrape_youtube
from .utils.accounts import (
    AccountTarget,
    infer_platform_from_input,
    load_accounts_file,
    normalize_platform,
    parse_account_target,
)
from .utils.cookies import find_platform_cookie, to_playwright_storage_state
from .utils.session_check import ALL_PLATFORMS, check_all_sessions, check_platform_session

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
for _noisy_logger in ("httpx", "httpcore", "urllib3", "twscrape", "instagrapi", "playwright"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)
logger = logging.getLogger("crawler")


_VALID_PLATFORMS = {"youtube", "x", "instagram", "tiktok", "facebook", "threads"}


def _parse_platforms_arg(raw: str) -> list[str]:
    """Parse argumen --platform (mendukung daftar dipisah koma seperti 'x,instagram')."""
    platforms: list[str] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        norm = normalize_platform(token)
        if norm not in _VALID_PLATFORMS:
            raise ValueError(
                f"Platform '{token}' tidak didukung. Pilih: youtube, x, instagram, tiktok, facebook, threads."
            )
        if norm not in platforms:
            platforms.append(norm)
    if not platforms:
        raise ValueError("Argumen --platform tidak boleh kosong.")
    return platforms


def _resolve_since_date(
    months: int | None,
    since_str: str | None,
    is_account_mode: bool = False,
) -> datetime | None:
    """Menghitung tanggal batas awal. None berarti tanpa batas tanggal."""
    if since_str:
        try:
            return datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error("Format tanggal --since salah. Gunakan YYYY-MM-DD.")
            sys.exit(1)
    if months is not None:
        if months <= 0:
            return None
        return datetime.now(timezone.utc) - timedelta(days=months * 30)
    if is_account_mode:
        return None
    return datetime.now(timezone.utc) - timedelta(days=6 * 30)


def _resolve_output_dir(output_dir: str | None) -> Path:
    """Mengembalikan folder output (default: ./output/<YYYY-MM-DD>)."""
    if output_dir:
        return Path(output_dir)
    return Path("output") / datetime.now().strftime("%Y-%m-%d")


def _resolve_max_results(max_results: int | None, is_account_mode: bool = False) -> int | None:
    """Menentukan batas jumlah item utama. None berarti semua item (tanpa batas)."""
    if max_results is not None:
        return None if max_results <= 0 else max_results
    return None if is_account_mode else 50


def _dispatch_scrape(
    platform: str,
    query: str = "",
    account_target: AccountTarget | None = None,
    since_date: datetime | None = None,
    until_date: datetime | None = None,
    max_results: int | None = 50,
    include_comments: bool = True,
    cookie_file: Path | None = None,
    headless: bool = True,
    on_mention=None,
    stop_event: threading.Event | None = None,
):
    """Jalankan scraper sesuai platform untuk mode query maupun akun."""
    plat = normalize_platform(platform)
    acct_arg = account_target.url if account_target else None

    if plat == "youtube":
        scrape_youtube(
            query=query,
            since_date=since_date,
            max_videos=max_results,
            include_comments=include_comments,
            cookie_file=cookie_file,
            on_mention=on_mention,
            stop_event=stop_event,
            account=acct_arg,
        )
    elif plat == "x":
        scrape_twitter(
            query=query,
            since_date=since_date.date() if since_date else None,
            until_date=until_date.date() if until_date else None,
            max_tweets=max_results,
            include_comments=include_comments,
            cookie_file=cookie_file,
            on_mention=on_mention,
            stop_event=stop_event,
            account=acct_arg,
        )
    elif plat == "instagram":
        if account_target:
            scrape_instagram_user(
                username=account_target.url,
                since_date=since_date,
                max_posts=max_results,
                include_comments=include_comments,
                cookie_file=cookie_file,
                headless=headless,
                on_mention=on_mention,
                stop_event=stop_event,
            )
        else:
            scrape_instagram_hashtag(
                hashtag=query,
                since_date=since_date,
                max_posts=max_results,
                include_comments=include_comments,
                cookie_file=cookie_file,
                headless=headless,
                on_mention=on_mention,
                stop_event=stop_event,
            )
    elif plat == "tiktok":
        scrape_tiktok(
            query=query,
            since_date=since_date,
            max_posts=max_results,
            cookie_file=cookie_file,
            headless=headless,
            on_mention=on_mention,
            stop_event=stop_event,
            account=acct_arg,
        )
    elif plat == "facebook":
        scrape_facebook(
            query=query,
            since_date=since_date,
            max_posts=max_results,
            cookie_file=cookie_file,
            headless=headless,
            on_mention=on_mention,
            stop_event=stop_event,
            account=acct_arg,
        )
    elif plat == "threads":
        scrape_threads(
            query=query,
            since_date=since_date,
            max_posts=max_results,
            cookie_file=cookie_file,
            headless=headless,
            on_mention=on_mention,
            stop_event=stop_event,
            account=acct_arg,
        )
    else:
        raise ValueError(f"Platform '{platform}' tidak didukung. Pilih: youtube, x, instagram, tiktok, facebook, threads.")


def _start_keyboard_listener(stop_event: threading.Event):
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


def _run_post_crawl_merge(
    output_dir: Path,
    merge: bool,
    merge_format: str = "json",
    merge_combined: bool = False,
) -> list[Path]:
    """Jalankan merge & deduplikasi di akhir crawling jika diminta."""
    if not (merge or merge_combined):
        return []

    if not list_raw_crawl_json_files(output_dir):
        logger.warning("[MERGE] Tidak ada file .json hasil crawling di %s untuk digabungkan.", output_dir)
        return []

    formats = ["json", "csv"] if merge_format == "both" else [merge_format]
    do_per_platform = bool(merge)
    do_combined = bool(merge_combined)

    generated: list[Path] = []
    logger.info("=== Menjalankan Merge & Deduplikasi Akhir Crawling ===")
    for fmt in formats:
        try:
            files = merge_and_export(
                input_dir=output_dir,
                fmt=fmt,
                combined=do_combined,
                per_platform=do_per_platform,
            )
            generated.extend(files)
        except Exception as exc:
            logger.error("[MERGE] Gagal melakukan merge format %s: %s", fmt.upper(), exc)

    if generated:
        logger.info("=== Merge Selesai (%d file dihasilkan) ===", len(generated))
        for path in generated:
            logger.info("  -> %s", path)

    return generated


def _handle_account_file_scrape(
    args: argparse.Namespace,
    since_date: datetime | None,
    until_date: datetime | None,
    max_results: int | None,
    output_dir: Path,
    cookie_file: Path | None,
):
    """Jalankan batch account crawling dari file JSON (paralel antar platform)."""
    try:
        grouped = load_accounts_file(args.account_file)
    except Exception as exc:
        logger.error("Gagal membaca --account-file '%s': %s", args.account_file, exc)
        sys.exit(1)

    if args.platform:
        try:
            plat_filters = set(_parse_platforms_arg(args.platform))
        except ValueError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        grouped = {k: v for k, v in grouped.items() if k in plat_filters}

    if not grouped:
        logger.warning("Tidak ada akun yang valid ditemukan di %s.", args.account_file)
        return

    stop_event = threading.Event()
    _start_keyboard_listener(stop_event)

    total_accounts = sum(len(v) for v in grouped.values())
    progress_lock = threading.Lock()
    completed_accounts = 0
    total_items_all = 0
    start_batch = time.time()

    logger.info("=== Memulai Batch Account Crawling (%d akun di %d platform) ===", total_accounts, len(grouped))
    logger.info("Batas Awal : %s", since_date.date() if since_date else "Tanpa batas tanggal (semua post)")
    logger.info("Max Items  : %s", max_results if max_results is not None else "Semua (tanpa batas)")
    logger.info("Stop Aman  : Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja")

    def _run_plat(plat: str, targets: list[AccountTarget]):
        nonlocal completed_accounts, total_items_all
        total = 0
        done_in_plat = 0
        plat_start = time.time()
        for idx, tgt in enumerate(targets, 1):
            if stop_event.is_set():
                break
            prefix = f"{plat}_{tgt.slug}"
            exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)
            logger.info("[%s] [%d/%d] Memulai: %s", plat.upper(), idx, len(targets), tgt.identifier)
            acct_start = time.time()
            try:
                _dispatch_scrape(
                    platform=plat,
                    account_target=tgt,
                    since_date=since_date,
                    until_date=until_date,
                    max_results=max_results,
                    include_comments=not args.no_comments,
                    cookie_file=cookie_file,
                    headless=not args.headed,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )
            except Exception as exc:
                logger.error("[%s] Error crawling akun '%s': %s", plat.upper(), tgt.identifier, exc)
            finally:
                exporter.finish()

            item_count = len(exporter.items)
            acct_dur = round(time.time() - acct_start, 1)
            total += item_count
            done_in_plat += 1
            with progress_lock:
                completed_accounts += 1
                total_items_all += item_count
                global_done = completed_accounts

            logger.info(
                "[%s] [%d/%d | Total %d/%d] Selesai: %s -> %d item (%.1fs)",
                plat.upper(),
                idx,
                len(targets),
                global_done,
                total_accounts,
                tgt.identifier,
                item_count,
                acct_dur,
            )
            if idx < len(targets) and not stop_event.is_set():
                pause = round(random.uniform(3.0, 5.5), 2)
                if stop_event.wait(timeout=pause):
                    break
        return plat, done_in_plat, len(targets), total, round(time.time() - plat_start, 1)

    platform_summaries: list[tuple[str, int, int, int, float]] = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(grouped)) as executor:
            futures = [executor.submit(_run_plat, p, t) for p, t in grouped.items()]
            for f in concurrent.futures.as_completed(futures):
                summary = f.result()
                platform_summaries.append(summary)
    except KeyboardInterrupt:
        logger.warning("\n[Pemberitahuan] Proses dihentikan oleh pengguna (Ctrl+C).")
        stop_event.set()

    total_duration = round(time.time() - start_batch, 1)
    logger.info("=== RINGKASAN BATCH CRAWLING (%d/%d akun selesai) ===", completed_accounts, total_accounts)
    for plat, done_n, total_n, count, dur in sorted(platform_summaries):
        logger.info("  %-10s : %d/%d akun | %d item (%.1fs)", plat.upper(), done_n, total_n, count, dur)
    logger.info("  Total Item : %d item | Durasi: %.1fs | Output: %s", total_items_all, total_duration, output_dir)

    _run_post_crawl_merge(
        output_dir=output_dir,
        merge=args.merge,
        merge_format=args.merge_format,
        merge_combined=args.merge_combined,
    )


def handle_scrape(args: argparse.Namespace):
    """Menangani perintah 'scrape'."""
    if getattr(args, "verbose", False):
        logging.getLogger("crawler").setLevel(logging.DEBUG)

    query = args.query
    account_raw = args.account
    account_file = args.account_file
    is_account_mode = bool(account_raw or account_file)

    since_date = _resolve_since_date(args.months, args.since, is_account_mode=is_account_mode)
    until_date = None
    if args.until:
        try:
            until_date = datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error("Format tanggal --until salah. Gunakan YYYY-MM-DD.")
            sys.exit(1)

    max_results = _resolve_max_results(args.max_results, is_account_mode=is_account_mode)
    output_dir = _resolve_output_dir(args.output_dir)
    cookie_file = Path(args.cookie) if args.cookie else None

    if account_file:
        _handle_account_file_scrape(args, since_date, until_date, max_results, output_dir, cookie_file)
        return

    if not query and not account_raw:
        logger.error("Diperlukan argumen --query, --account, atau --account-file.")
        sys.exit(1)

    account_target: AccountTarget | None = None
    parsed_platforms: list[str] = []
    if args.platform:
        try:
            parsed_platforms = _parse_platforms_arg(args.platform)
        except ValueError as exc:
            logger.error("%s", exc)
            sys.exit(1)

    if account_raw:
        if len(parsed_platforms) > 1:
            logger.error("Mode single --account hanya mendukung 1 platform (gunakan --account-file untuk multi-platform).")
            sys.exit(1)
        try:
            account_target = parse_account_target(account_raw, default_platform=parsed_platforms[0] if parsed_platforms else None)
        except ValueError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        platform = account_target.platform
    else:
        if not parsed_platforms:
            logger.error("Argumen --platform wajib untuk mode --query.")
            sys.exit(1)
        if len(parsed_platforms) > 1:
            logger.error("Mode single --query hanya mendukung 1 platform.")
            sys.exit(1)
        platform = parsed_platforms[0]

    prefix = f"{platform}_{account_target.slug}" if account_target else f"{platform}_{query.replace(' ', '_')[:30]}"
    exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)

    stop_event = threading.Event()
    _start_keyboard_listener(stop_event)

    logger.info("=== Memulai Crawling (Progressive Writing Aktif) ===")
    logger.info("Platform   : %s", platform)
    if account_target:
        logger.info("Akun       : %s (%s)", account_target.identifier, account_target.url)
    else:
        logger.info("Query      : %s", query)
    logger.info("Batas Awal : %s", since_date.date() if since_date else "Tanpa batas tanggal (semua post)")
    logger.info("Max Items  : %s", max_results if max_results is not None else "Semua (tanpa batas)")
    logger.info("Target CSV : %s", exporter.csv_path.name)
    logger.info("Target JSON: %s", exporter.json_path.name)
    logger.info("Stop Aman  : Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja")

    try:
        _dispatch_scrape(
            platform=platform,
            query=query,
            account_target=account_target,
            since_date=since_date,
            until_date=until_date,
            max_results=max_results,
            include_comments=not args.no_comments,
            cookie_file=cookie_file,
            headless=not args.headed,
            on_mention=exporter.write_mention,
            stop_event=stop_event,
        )
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
            _run_post_crawl_merge(
                output_dir=output_dir,
                merge=args.merge,
                merge_format=args.merge_format,
                merge_combined=args.merge_combined,
            )
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


def handle_check(args: argparse.Namespace):
    """Mengecek validitas session/cookie yang tersedia."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    live = not args.offline
    if args.cookie and not args.platform:
        logger.error("Argumen --cookie memerlukan --platform.")
        sys.exit(1)

    if args.platform:
        results = [
            check_platform_session(
                args.platform,
                cookie_file=args.cookie,
                base_dir=base_dir,
                live=live,
            )
        ]
    else:
        results = check_all_sessions(ALL_PLATFORMS, base_dir=base_dir, live=live)

    mode_label = "Offline (Lokal)" if args.offline else "Live + Lokal"
    print(f"\n=== CHECK SESSION / COOKIE ({mode_label}) ===")
    print(f"{'Platform':<11} {'File':<16} {'Status':<10} {'Keterangan'}")
    print("-" * 78)

    ready_count = 0
    for r in results:
        fname = r.cookie_path.name if r.cookie_path else "-"
        status_label = r.status.upper()
        extra = f" ({r.username})" if r.username else ""
        exp_str = f" [exp: {r.earliest_expiry.strftime('%Y-%m-%d')}]" if r.earliest_expiry else ""
        print(f"{r.platform.upper():<11} {fname:<16} {status_label:<10} {r.detail}{extra}{exp_str}")
        if r.status in ("valid", "optional"):
            ready_count += 1

    print("-" * 78)
    print(f"Ringkasan: {ready_count}/{len(results)} platform siap digunakan.\n")


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
        required=False,
        default=None,
        help="Platform target: youtube, x, instagram, tiktok, facebook, threads (pisahkan koma untuk multi-platform pada --account-file, misal: x,instagram)",
    )
    p_scrape.add_argument("--query", "-q", default="", help="Kata kunci pencarian")
    p_scrape.add_argument("--account", "-a", default="", help="Username atau URL akun target (semua platform)")
    p_scrape.add_argument("--account-file", "-f", default=None, help="Path ke file JSON berisi daftar akun per platform / URL")
    p_scrape.add_argument("--months", "-m", type=int, default=None, help="Batas mundur dalam bulan (default: 6 untuk --query, tanpa batas untuk --account; 0 = tanpa batas)")
    p_scrape.add_argument("--since", default=None, help="Batas tanggal awal manual (YYYY-MM-DD)")
    p_scrape.add_argument("--until", default=None, help="Batas tanggal akhir manual (YYYY-MM-DD)")
    p_scrape.add_argument("--max-results", "-n", type=int, default=None, help="Maksimal jumlah item utama (default: 50 untuk --query, semua/tanpa batas untuk --account; 0 = tanpa batas)")
    p_scrape.add_argument("--no-comments", action="store_true", help="Nonaktifkan pengambilan komentar")
    p_scrape.add_argument("--cookie", "-c", default=None, help="Path ke file cookies.txt spesifik")
    p_scrape.add_argument("--output-dir", "-o", default=None, help="Folder penyimpanan hasil (default: output/<YYYY-MM-DD>)")
    p_scrape.add_argument("--headed", action="store_true", help="Buka browser Playwright dengan tampilan UI")
    p_scrape.add_argument("--verbose", "-v", action="store_true", help="Tampilkan log detail per-item (mode debug)")
    p_scrape.add_argument(
        "--merge",
        action="store_true",
        help="Gabungkan & deduplikasi hasil crawling di akhir proses (per-platform)",
    )
    p_scrape.add_argument(
        "--merge-format",
        choices=["json", "csv", "both"],
        default="json",
        help="Format output merge: json, csv, atau both (default: json)",
    )
    p_scrape.add_argument(
        "--merge-combined",
        action="store_true",
        help="Hasilkan file gabungan YYYYMMDD-combined.<format> di akhir crawling (otomatis mengaktifkan merge)",
    )
    p_scrape.set_defaults(func=handle_scrape)

    # Subcommand: cookies
    p_cookies = subparsers.add_parser("cookies", help="Manajemen file cookie")
    p_cookies.add_argument("action", choices=["list", "convert"], help="Tindakan: list atau convert")
    p_cookies.add_argument("--input", "-i", default=None, help="File input cookies.txt untuk convert")
    p_cookies.add_argument("--output", "-o", default=None, help="File output json untuk convert")
    p_cookies.set_defaults(func=handle_cookies)

    # Subcommand: check
    p_check = subparsers.add_parser("check", help="Cek validitas session/cookie yang ada")
    p_check.add_argument(
        "--platform",
        "-p",
        required=False,
        default=None,
        choices=["youtube", "yt", "x", "twitter", "instagram", "ig", "tiktok", "tt", "facebook", "fb", "threads", "th"],
        help="Cek satu platform tertentu (default: semua platform)",
    )
    p_check.add_argument("--cookie", "-c", default=None, help="Path ke file cookies.txt spesifik (wajib disertai --platform)")
    p_check.add_argument("--offline", action="store_true", help="Cek token & kedaluwarsa secara lokal tanpa request ke server")
    p_check.set_defaults(func=handle_check)

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

