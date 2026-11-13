"""Interactive CLI Runner untuk Social Media Crawler.

Mempermudah pemilihan platform, pemilihan batch keyword,
dan penentuan rentang tanggal 6 bulan secara interaktif dan visual.
Mendukung eksekusi PARALEL antar-platform dengan jeda aman anti-bot sekuensial
di tiap platform yang sama.
Dilengkapi shortcut aman: Ctrl+C, 'q', atau 'ESC' untuk menghentikan seluruh proses
seketika sambil menjamin seluruh data yang terkumpul tersimpan utuh di disk.
"""
import concurrent.futures
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    class _DummyColor:
        def __getattr__(self, _):
            return ""
    Fore = Style = _DummyColor()

from .cli import _dispatch_scrape
from .exporter import ProgressiveExporter
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
    parse_account_target,
)
from .utils.cookies import find_platform_cookie
from .utils.session_check import ALL_PLATFORMS, check_all_sessions

PRESET_KEYWORDS = [
    "kulon progo",
    "kulonprogo",
    "Agung Setyawan",
    '"Agung Setyawan" "Kulon Progo"',
    "Ambar Purwoko",
    '"Ambar Purwoko" "Kulon Progo"',
]

PLATFORMS = [
    ("youtube", "YouTube", "Bisa langsung jalan (tanpa cookie)"),
    ("x", "X / Twitter", "Memerlukan cookies/x.txt"),
    ("instagram", "Instagram", "Memerlukan cookies/instagram.txt"),
    ("tiktok", "TikTok", "Memerlukan cookies/tiktok.txt"),
    ("facebook", "Facebook", "Memerlukan cookies/facebook.txt"),
    ("threads", "Threads", "Memerlukan cookies/threads.txt"),
]

PLATFORM_COLORS = {
    "youtube": Fore.RED,
    "x": Fore.CYAN,
    "twitter": Fore.CYAN,
    "instagram": Fore.MAGENTA,
    "tiktok": Fore.LIGHTBLUE_EX,
    "facebook": Fore.BLUE,
    "threads": Fore.LIGHTMAGENTA_EX,
}


def print_header():
    print(f"\n{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}   PANTAU CRAWLER - INTERACTIVE SOCIAL MEDIA RUNNER   {Style.RESET_ALL}")
    print(f"{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{Fore.WHITE}Tool crawling data historis hingga 6 bulan terakhir.{Style.RESET_ALL}")
    print(f"{Fore.GREEN}⚡ Mode: Paralel Antar-Platform + Anti-Bot Interval per Platform{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}🛑 Shortcut Stop Aman: Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja{Style.RESET_ALL}\n")


def check_cookie_status(platform_key: str) -> tuple[bool, str]:
    """Cek apakah file cookie untuk platform tersedia."""
    cookie = find_platform_cookie(platform_key)
    if cookie:
        return True, f"{Fore.GREEN}[Cookie OK: {cookie.name}]{Style.RESET_ALL}"
    if platform_key == "youtube":
        return True, f"{Fore.YELLOW}[Opsional / Siap]{Style.RESET_ALL}"
    return False, f"{Fore.RED}[Cookie Belum Ada]{Style.RESET_ALL}"


def prompt_crawl_mode() -> str:
    """Pilih mode crawling: keyword, akun, atau cek session."""
    print(f"{Fore.YELLOW}PILIH MODE:{Style.RESET_ALL}")
    print("  [1] Crawl berdasarkan Keyword / Kata Kunci Pencarian")
    print("  [2] Crawl berdasarkan Akun (Username / URL / File JSON)")
    print("  [3] Cek Status & Validitas Session / Cookie")
    while True:
        choice = input(f"\n{Fore.GREEN}Masukkan pilihan (1, 2, atau 3) [Default: 1]: {Style.RESET_ALL}").strip()
        if not choice or choice == "1":
            return "keyword"
        if choice == "2":
            return "account"
        if choice == "3":
            return "check"
        print(f"{Fore.RED}Pilihan tidak valid. Masukkan 1, 2, atau 3.{Style.RESET_ALL}")


def _run_session_check_interactive():
    """Jalankan pengecekan validitas session semua platform di mode interaktif."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    print(f"\n{Fore.CYAN}>>> MEMERIKSA VALIDITAS SESSION / COOKIE... <<<{Style.RESET_ALL}\n")
    results = check_all_sessions(ALL_PLATFORMS, base_dir=base_dir, live=True)

    print(f"{'Platform':<11} {'File':<16} {'Status':<10} {'Keterangan'}")
    print("-" * 78)
    ready_count = 0
    for r in results:
        fname = r.cookie_path.name if r.cookie_path else "-"
        status_label = r.status.upper()
        if r.status in ("valid", "optional"):
            color = Fore.GREEN
            ready_count += 1
        elif r.status in ("expired", "invalid", "error"):
            color = Fore.RED
        else:
            color = Fore.YELLOW

        extra = f" ({r.username})" if r.username else ""
        exp_str = f" [exp: {r.earliest_expiry.strftime('%Y-%m-%d')}]" if r.earliest_expiry else ""
        print(f"{r.platform.upper():<11} {fname:<16} {color}{status_label:<10}{Style.RESET_ALL} {r.detail}{extra}{exp_str}")

    print("-" * 78)
    print(f"Ringkasan: {Fore.CYAN}{ready_count}/{len(results)}{Style.RESET_ALL} platform siap digunakan.\n")


def prompt_account_targets() -> dict[str, list[AccountTarget]]:
    """Prompt input akun (username/URL atau file JSON) dan kelompokkan per platform."""
    print(f"\n{Fore.YELLOW}LANGKAH 1: Input Akun Target{Style.RESET_ALL}")
    print("  - Masukkan username (misal: @setdakp), URL akun, atau path ke file .json")
    print("  - Pisahkan dengan koma jika lebih dari satu (atau ketik 'F' untuk muat file JSON)")

    while True:
        raw = input(f"\n{Fore.GREEN}Username / URL / Path JSON: {Style.RESET_ALL}").strip()
        if not raw:
            print(f"{Fore.RED}Input tidak boleh kosong.{Style.RESET_ALL}")
            continue

        if raw.upper() == "F":
            raw = input(f"{Fore.GREEN}Masukkan path file JSON akun: {Style.RESET_ALL}").strip()

        potential_path = Path(raw.strip('"').strip("'"))
        if raw.lower().endswith(".json") and potential_path.is_file():
            try:
                grouped = load_accounts_file(potential_path)
                if grouped:
                    return grouped
                print(f"{Fore.RED}File JSON tidak memuat akun yang valid.{Style.RESET_ALL}")
                continue
            except Exception as exc:
                print(f"{Fore.RED}Gagal membaca file JSON: {exc}{Style.RESET_ALL}")
                continue

        entries = [e.strip() for e in raw.split(",") if e.strip()]
        grouped: dict[str, list[AccountTarget]] = {}
        plain_usernames: list[str] = []

        for entry in entries:
            inferred = infer_platform_from_input(entry)
            if inferred:
                try:
                    tgt = parse_account_target(entry)
                    grouped.setdefault(tgt.platform, []).append(tgt)
                except ValueError as exc:
                    print(f"{Fore.RED}{exc}{Style.RESET_ALL}")
            else:
                plain_usernames.append(entry)

        if plain_usernames:
            print(f"\n{Fore.CYAN}Username tanpa URL terdeteksi: {', '.join(plain_usernames)}{Style.RESET_ALL}")
            selected_plats = prompt_select_platforms()
            for plat in selected_plats:
                for uname in plain_usernames:
                    try:
                        tgt = parse_account_target(uname, default_platform=plat)
                        grouped.setdefault(tgt.platform, []).append(tgt)
                    except ValueError as exc:
                        print(f"{Fore.RED}{exc}{Style.RESET_ALL}")

        if grouped:
            return grouped
        print(f"{Fore.RED}Tidak ada target akun yang valid. Silakan coba lagi.{Style.RESET_ALL}")


def prompt_filter_account_platforms(
    grouped: dict[str, list[AccountTarget]],
) -> dict[str, list[AccountTarget]]:
    """Pilih platform mana saja yang ingin di-crawl dari daftar akun yang sudah dimuat."""
    if len(grouped) <= 1:
        return grouped

    plat_list = list(grouped.keys())
    print(f"\n{Fore.YELLOW}Pilih Platform yang Akan Di-crawl ({len(plat_list)} platform tersedia):{Style.RESET_ALL}")
    for idx, plat in enumerate(plat_list, 1):
        _, status_label = check_cookie_status(plat)
        count = len(grouped[plat])
        print(f"  [{idx}] {plat.upper():<12} ({count} akun) {status_label}")
    print("  [A] SEMUA PLATFORM di daftar")

    while True:
        choice = input(
            f"\n{Fore.GREEN}Masukkan pilihan (contoh: 1,3 atau A) [Default: A]: {Style.RESET_ALL}"
        ).strip().upper()
        if not choice or choice == "A":
            return grouped

        parts = [p.strip() for p in choice.split(",") if p.strip()]
        selected_keys: list[str] = []
        valid = True
        for part in parts:
            if part.isdigit() and 1 <= int(part) <= len(plat_list):
                key = plat_list[int(part) - 1]
                if key not in selected_keys:
                    selected_keys.append(key)
            else:
                valid = False
                break

        if valid and selected_keys:
            return {k: grouped[k] for k in selected_keys}
        print(f"{Fore.RED}Pilihan tidak valid. Masukkan nomor 1-{len(plat_list)} dipisah koma, atau A.{Style.RESET_ALL}")


def prompt_account_settings() -> tuple[int | None, int | None, bool]:
    """Prompt batas tanggal (opsional), batas jumlah post (opsional), dan komentar."""
    print(f"\n{Fore.YELLOW}LANGKAH 2: Pengaturan Waktu & Batas Hasil Akun{Style.RESET_ALL}")

    months_str = input(
        f"{Fore.GREEN}Mundur berapa bulan? (Kosongkan / 0 untuk SEMUA tanpa batas tanggal) [Default: Tanpa batas]: {Style.RESET_ALL}"
    ).strip()
    months: int | None = None
    if months_str and months_str.isdigit() and int(months_str) > 0:
        months = int(months_str)

    limit_str = input(
        f"{Fore.GREEN}Maksimal post per akun? (Kosongkan / 0 untuk SEMUA post) [Default: Semua]: {Style.RESET_ALL}"
    ).strip()
    limit: int | None = None
    if limit_str and limit_str.isdigit() and int(limit_str) > 0:
        limit = int(limit_str)

    cm_str = input(f"{Fore.GREEN}Ambil komentar/balasan juga? (y/n) [Default: y]: {Style.RESET_ALL}").strip().lower()
    include_comments = cm_str != "n"

    return months, limit, include_comments


def run_account_platform_worker(
    platform: str,
    targets: list[AccountTarget],
    since_date: datetime | None,
    limit: int | None,
    include_comments: bool,
    output_dir: Path,
    results_collector: list,
    lock: threading.Lock,
    stop_event: threading.Event,
    progress_state: dict[str, int] | None = None,
):
    """Worker platform untuk mode crawling akun."""
    plat_color = PLATFORM_COLORS.get(platform, Fore.CYAN)
    tag = f"{plat_color}[{platform.upper()}]{Style.RESET_ALL}"

    for idx, tgt in enumerate(targets, 1):
        if stop_event.is_set():
            with lock:
                print(f"{tag} {Fore.YELLOW}Menghentikan sisa antrean karena sinyal stop.{Style.RESET_ALL}")
            break

        prefix = f"{platform}_{tgt.slug}"
        exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)

        with lock:
            print(f"{tag} {Fore.YELLOW}[{idx}/{len(targets)}] Memulai: '{tgt.identifier}'{Style.RESET_ALL}")

        start_time = time.time()
        try:
            _dispatch_scrape(
                platform=platform,
                account_target=tgt,
                since_date=since_date,
                max_results=limit,
                include_comments=include_comments,
                headless=True,
                on_mention=exporter.write_mention,
                stop_event=stop_event,
            )
        except Exception as exc:
            with lock:
                print(f"{tag} {Fore.RED}Error saat crawling akun '{tgt.identifier}': {exc}{Style.RESET_ALL}")
        finally:
            exporter.finish()

        duration = round(time.time() - start_time, 1)
        item_count = len(exporter.items)

        with lock:
            progress_label = f"{idx}/{len(targets)}"
            if progress_state is not None:
                progress_state["done"] += 1
                progress_label = f"{idx}/{len(targets)} | Total {progress_state['done']}/{progress_state['total']}"

            if item_count > 0:
                print(f"{tag} {Fore.GREEN}[{progress_label}] ✓ '{tgt.identifier}': {item_count} item ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, tgt.identifier, item_count, exporter.csv_path.name, f"{duration}s"))
            else:
                print(f"{tag} {Fore.YELLOW}[{progress_label}] ⚠ '{tgt.identifier}': 0 item ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, tgt.identifier, 0, "-", f"{duration}s"))

        if idx < len(targets) and not stop_event.is_set():
            pause = round(random.uniform(3.0, 5.5), 2)
            if stop_event.wait(timeout=pause):
                break

    with lock:
        print(f"{tag} {Fore.GREEN}Seluruh antrean akun untuk {platform.upper()} telah selesai!{Style.RESET_ALL}")


def prompt_select_platforms() -> list[str]:
    """Prompt interaktif memilih platform."""
    print(f"{Fore.YELLOW}LANGKAH 1: Pilih Platform Target{Style.RESET_ALL}")
    for idx, (key, name, note) in enumerate(PLATFORMS, 1):
        _, status_label = check_cookie_status(key)
        print(f"  [{idx}] {name:<12} {status_label} - {note}")
    print(f"  [A] SEMUA PLATFORM yang ada cookienya (+ YouTube)")
    print(f"  [X] Keluar")

    while True:
        choice = input(f"\n{Fore.GREEN}Masukkan pilihan (contoh: 1 atau 1,2 atau A) [Default: 1]: {Style.RESET_ALL}").strip().upper()
        if not choice:
            return ["youtube"]
        if choice == "X":
            sys.exit(0)
        if choice == "A":
            selected = []
            for key, _, _ in PLATFORMS:
                ok, _ = check_cookie_status(key)
                if ok:
                    selected.append(key)
            return selected

        parts = [p.strip() for p in choice.split(",") if p.strip()]
        selected = []
        valid = True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(PLATFORMS):
                selected.append(PLATFORMS[int(p) - 1][0])
            else:
                valid = False
                break
        if valid and selected:
            return selected
        print(f"{Fore.RED}Pilihan tidak valid. Silakan coba lagi.{Style.RESET_ALL}")


def prompt_select_keywords() -> list[str]:
    """Prompt interaktif memilih keywords."""
    print(f"\n{Fore.YELLOW}LANGKAH 2: Pilih Keywords / Kata Kunci Pencarian{Style.RESET_ALL}")
    print("Daftar Preset Keyword Kulon Progo:")
    for idx, kw in enumerate(PRESET_KEYWORDS, 1):
        print(f"  [{idx}] {kw}")
    print(f"  [A] Jalankan SEMUA {len(PRESET_KEYWORDS)} keyword preset di atas")
    print(f"  [C] Masukkan keyword kustom sendiri")

    while True:
        choice = input(f"\n{Fore.GREEN}Masukkan pilihan (contoh: A atau 1,2,3 atau C) [Default: A]: {Style.RESET_ALL}").strip().upper()
        if not choice or choice == "A":
            return PRESET_KEYWORDS.copy()
        if choice == "C":
            custom_input = input(f"{Fore.GREEN}Ketik keyword Anda (pisahkan dengan koma jika lebih dari 1): {Style.RESET_ALL}").strip()
            if custom_input:
                return [k.strip() for k in custom_input.split(",") if k.strip()]
            print(f"{Fore.RED}Keyword tidak boleh kosong.{Style.RESET_ALL}")
            continue

        parts = [p.strip() for p in choice.split(",") if p.strip()]
        selected = []
        valid = True
        for p in parts:
            if p.isdigit() and 1 <= int(p) <= len(PRESET_KEYWORDS):
                selected.append(PRESET_KEYWORDS[int(p) - 1])
            else:
                valid = False
                break
        if valid and selected:
            return selected
        print(f"{Fore.RED}Pilihan tidak valid. Silakan coba lagi.{Style.RESET_ALL}")


def prompt_settings() -> tuple[int, int]:
    """Prompt rentang bulan dan jumlah maksimal item."""
    print(f"\n{Fore.YELLOW}LANGKAH 3: Pengaturan Waktu & Batas Hasil{Style.RESET_ALL}")

    months_str = input(f"{Fore.GREEN}Mundur berapa bulan? [Default: 6]: {Style.RESET_ALL}").strip()
    months = 6
    if months_str and months_str.isdigit():
        months = max(1, int(months_str))

    limit_str = input(f"{Fore.GREEN}Maksimal hasil per keyword per platform? [Default: 50]: {Style.RESET_ALL}").strip()
    limit = 50
    if limit_str and limit_str.isdigit():
        limit = max(5, int(limit_str))

    return months, limit


def _listen_keyboard_stop(stop_event: threading.Event, lock: threading.Lock):
    """Background listener untuk tombol 'q', 'Q', atau ESC di console Windows."""
    try:
        import msvcrt
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"q", b"Q", b"\x1b", b"\x03"):  # 'q', 'Q', ESC, atau Ctrl+C
                    with lock:
                        print(f"\n{Fore.YELLOW}🛑 [KEYPRESS SHORTCUT] Tombol stop terdeteksi! Menghentikan semua proses secara aman...{Style.RESET_ALL}")
                    stop_event.set()
                    break
            time.sleep(0.1)
    except Exception:
        pass


def run_platform_worker(
    platform: str,
    keywords: list[str],
    since_date: datetime,
    limit: int,
    output_dir: Path,
    results_collector: list,
    lock: threading.Lock,
    stop_event: threading.Event,
    progress_state: dict[str, int] | None = None,
):
    """Worker platform mandiri.

    Menjalankan list keyword secara sekuensial dengan jeda interval anti-bot aman,
    sementara platform lain berjalan secara simultan/paralel di thread lain.
    """
    plat_color = PLATFORM_COLORS.get(platform, Fore.CYAN)
    tag = f"{plat_color}[{platform.upper()}]{Style.RESET_ALL}"

    for kw_idx, kw in enumerate(keywords, 1):
        if stop_event.is_set():
            with lock:
                print(f"{tag} {Fore.YELLOW}Menghentikan sisa antrean karena sinyal stop.{Style.RESET_ALL}")
            break

        clean_kw = "".join(c if c.isalnum() else "_" for c in kw)[:25]
        prefix = f"{platform}_{clean_kw}"
        exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)

        with lock:
            print(f"{tag} {Fore.YELLOW}[{kw_idx}/{len(keywords)}] Memulai: '{kw}'{Style.RESET_ALL}")

        start_time = time.time()
        try:
            if platform in ("youtube", "yt"):
                scrape_youtube(
                    query=kw,
                    since_date=since_date,
                    max_videos=limit,
                    include_comments=True,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )

            elif platform in ("x", "twitter"):
                scrape_twitter(
                    query=kw,
                    since_date=since_date.date(),
                    max_tweets=limit,
                    include_comments=True,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )

            elif platform in ("instagram", "ig"):
                if kw.startswith("#") or " " not in kw:
                    scrape_instagram_hashtag(
                        hashtag=kw,
                        since_date=since_date,
                        max_posts=limit,
                        include_comments=True,
                        on_mention=exporter.write_mention,
                        stop_event=stop_event,
                    )
                else:
                    clean_tag = kw.replace(" ", "")
                    scrape_instagram_hashtag(
                        hashtag=clean_tag,
                        since_date=since_date,
                        max_posts=limit,
                        include_comments=True,
                        on_mention=exporter.write_mention,
                        stop_event=stop_event,
                    )

            elif platform in ("tiktok", "tt"):
                scrape_tiktok(
                    query=kw,
                    since_date=since_date,
                    max_posts=limit,
                    headless=True,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )

            elif platform in ("facebook", "fb"):
                scrape_facebook(
                    query=kw,
                    since_date=since_date,
                    max_posts=limit,
                    headless=True,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )

            elif platform in ("threads", "th"):
                scrape_threads(
                    query=kw,
                    since_date=since_date,
                    max_posts=limit,
                    headless=True,
                    on_mention=exporter.write_mention,
                    stop_event=stop_event,
                )

        except Exception as exc:
            with lock:
                print(f"{tag} {Fore.RED}Error saat crawling '{kw}': {exc}{Style.RESET_ALL}")
        finally:
            exporter.finish()

        duration = round(time.time() - start_time, 1)
        item_count = len(exporter.items)

        with lock:
            progress_label = f"{kw_idx}/{len(keywords)}"
            if progress_state is not None:
                progress_state["done"] += 1
                progress_label = f"{kw_idx}/{len(keywords)} | Total {progress_state['done']}/{progress_state['total']}"

            if item_count > 0:
                print(f"{tag} {Fore.GREEN}[{progress_label}] ✓ '{kw}': {item_count} item ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, kw, item_count, exporter.csv_path.name, f"{duration}s"))
            else:
                print(f"{tag} {Fore.YELLOW}[{progress_label}] ⚠ '{kw}': 0 item ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, kw, 0, "-", f"{duration}s"))

        # Jeda anti-bot antar keyword di platform yang sama (interruptible)
        if kw_idx < len(keywords) and not stop_event.is_set():
            pause = round(random.uniform(3.0, 5.5), 2)
            if stop_event.wait(timeout=pause):
                break

    with lock:
        print(f"{tag} {Fore.GREEN}Seluruh antrean untuk {platform.upper()} telah selesai!{Style.RESET_ALL}")


def _prompt_and_run_post_crawl_merge(output_dir: Path) -> list[Path]:
    """Tawarkan dan jalankan merge & deduplikasi di akhir crawling interaktif."""
    if not list_raw_crawl_json_files(output_dir):
        return []

    choice = input(
        f"{Fore.GREEN}Gabungkan & deduplikasi hasil crawling di folder ini sekarang? (y/n) [Default: y]: {Style.RESET_ALL}"
    ).strip().lower()
    if choice == "n":
        return []

    print(f"\n{Fore.YELLOW}Pilih mode merge:{Style.RESET_ALL}")
    print("  [1] Per-platform (YYYYMMDD-<platform>.<ext>)")
    print("  [2] Combined (YYYYMMDD-combined.<ext>)")
    print("  [3] Keduanya (Per-platform + Combined)")
    mode_choice = input(f"{Fore.GREEN}Masukkan pilihan (1/2/3) [Default: 1]: {Style.RESET_ALL}").strip()

    do_per_platform = mode_choice in ("", "1", "3")
    do_combined = mode_choice in ("2", "3")
    if mode_choice not in ("", "1", "2", "3"):
        print(f"{Fore.YELLOW}Pilihan tidak dikenali, menggunakan default [1] Per-platform.{Style.RESET_ALL}")
        do_per_platform = True
        do_combined = False

    print(f"\n{Fore.YELLOW}Pilih format output merge:{Style.RESET_ALL}")
    print("  [1] JSON")
    print("  [2] CSV (Data Browser)")
    print("  [3] Keduanya (JSON + CSV)")
    fmt_choice = input(f"{Fore.GREEN}Masukkan pilihan (1/2/3) [Default: 1]: {Style.RESET_ALL}").strip()

    if fmt_choice == "2":
        formats = ["csv"]
    elif fmt_choice == "3":
        formats = ["json", "csv"]
    else:
        if fmt_choice not in ("", "1"):
            print(f"{Fore.YELLOW}Pilihan tidak dikenali, menggunakan default [1] JSON.{Style.RESET_ALL}")
        formats = ["json"]

    print(f"\n{Fore.CYAN}>>> MENJALANKAN MERGE & DEDUPLIKASI AKHIR CRAWLING <<<{Style.RESET_ALL}")
    generated: list[Path] = []
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
            print(f"{Fore.RED}Gagal melakukan merge format {fmt.upper()}: {exc}{Style.RESET_ALL}")

    if generated:
        print(f"\n{Fore.GREEN}✓ Merge selesai! {len(generated)} file dihasilkan:{Style.RESET_ALL}")
        for path in generated:
            print(f"  -> {path}")
        print()
    else:
        print(f"{Fore.YELLOW}⚠ Tidak ada file merge yang dihasilkan.{Style.RESET_ALL}\n")

    return generated


def _run_account_interactive(output_dir: Path):
    grouped_accounts = prompt_account_targets()
    grouped_accounts = prompt_filter_account_platforms(grouped_accounts)
    months, limit, include_comments = prompt_account_settings()

    since_date = datetime.now(timezone.utc) - timedelta(days=months * 30) if months else None
    total_accounts = sum(len(v) for v in grouped_accounts.values())
    platforms = list(grouped_accounts.keys())

    print(f"\n{Fore.MAGENTA}================== KONFIRMASI RUN AKUN =================={Style.RESET_ALL}")
    print(f"Platform ({len(platforms)})   : {Fore.CYAN}{', '.join([p.upper() for p in platforms])}{Style.RESET_ALL}")
    print(f"Total Akun    : {Fore.CYAN}{total_accounts} akun{Style.RESET_ALL}")
    for plat, tgts in grouped_accounts.items():
        print(f"  - {plat.upper():<10}: {', '.join(t.identifier for t in tgts)}")
    print(f"Batas Tanggal : {Fore.CYAN}{since_date.strftime('%Y-%m-%d') if since_date else 'Tanpa batas tanggal (semua post)'}{Style.RESET_ALL}")
    print(f"Maksimal/Akun : {Fore.CYAN}{limit if limit is not None else 'Semua post (tanpa batas)'}{Style.RESET_ALL}")
    print(f"Komentar      : {Fore.CYAN}{'Ya' if include_comments else 'Tidak'}{Style.RESET_ALL}")
    print(f"Folder Output : {Fore.CYAN}{output_dir}{Style.RESET_ALL}")
    print(f"Shortcut Stop : {Fore.YELLOW}Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja{Style.RESET_ALL}")
    print(f"{Fore.MAGENTA}========================================================={Style.RESET_ALL}")

    confirm = input(f"\n{Fore.GREEN}Lanjutkan eksekusi crawling akun? (y/n) [Default: y]: {Style.RESET_ALL}").strip().lower()
    if confirm and confirm != "y":
        print(f"{Fore.YELLOW}Dibatalkan oleh pengguna.{Style.RESET_ALL}")
        return

    print(f"\n{Fore.GREEN}>>> MEMULAI PARALEL ACCOUNT CRAWLING ({len(platforms)} WORKER AKTIF) <<<{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Tips: Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja untuk menghentikan dengan aman.{Style.RESET_ALL}\n")

    results_summary = []
    print_lock = threading.Lock()
    stop_event = threading.Event()
    progress_state = {"done": 0, "total": total_accounts}
    start_total_time = time.time()

    def _sigint_handler(signum, frame):
        with print_lock:
            print(f"\n{Fore.YELLOW}🛑 [Ctrl+C DITERIMA] Menghentikan semua worker secara aman & menyimpan file data...{Style.RESET_ALL}")
        stop_event.set()

    old_sigint = signal.signal(signal.SIGINT, _sigint_handler)
    threading.Thread(target=_listen_keyboard_stop, args=(stop_event, print_lock), daemon=True).start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(platforms)) as executor:
        futures = {
            executor.submit(
                run_account_platform_worker,
                plat,
                tgts,
                since_date,
                limit,
                include_comments,
                output_dir,
                results_summary,
                print_lock,
                stop_event,
                progress_state,
            ): plat
            for plat, tgts in grouped_accounts.items()
        }
        try:
            while any(not f.done() for f in futures.keys()):
                if stop_event.is_set():
                    break
                time.sleep(0.2)
            for f in futures.keys():
                try:
                    f.result(timeout=1.0)
                except Exception:
                    pass
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            signal.signal(signal.SIGINT, old_sigint)

    total_duration = round(time.time() - start_total_time, 1)
    print(f"\n{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}            RINGKASAN ACCOUNT CRAWLING                {Style.RESET_ALL}")
    print(f"{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{'Platform':<12} {'Akun':<30} {'Jumlah':<8} {'Durasi':<8} {'File Output'}")
    print("-" * 85)
    total_all_items = 0
    for plat, acct, count, fname, dur in results_summary:
        total_all_items += count
        color = Fore.GREEN if count > 0 else Fore.YELLOW
        print(f"{plat:<12} {acct:<30} {color}{count:<8}{Style.RESET_ALL} {dur:<8} {fname}")
    print("-" * 85)
    print(f"{Fore.WHITE}Total Seluruh Item Terkumpul: {Fore.GREEN}{total_all_items} item{Style.RESET_ALL}")
    print(f"Total Waktu Eksekusi Paralel: {Fore.CYAN}{total_duration} detik{Style.RESET_ALL}")
    print(f"Semua file CSV & JSON tersimpan di: {output_dir}\n")

    if total_all_items > 0:
        _prompt_and_run_post_crawl_merge(output_dir)


def run_interactive():
    print_header()

    output_dir = Path(__file__).resolve().parent.parent.parent / "output" / datetime.now().strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    while True:
        mode = prompt_crawl_mode()
        if mode == "check":
            _run_session_check_interactive()
            continue
        break
    if mode == "account":
        _run_account_interactive(output_dir)
        return

    selected_platforms = prompt_select_platforms()
    selected_keywords = prompt_select_keywords()
    months, limit = prompt_settings()

    since_date = datetime.now(timezone.utc) - timedelta(days=months * 30)

    print(f"\n{Fore.MAGENTA}================== KONFIRMASI RUN =================={Style.RESET_ALL}")
    print(f"Platform ({len(selected_platforms)})   : {Fore.CYAN}{', '.join([p.upper() for p in selected_platforms])}{Style.RESET_ALL}")
    print(f"Eksekusi      : {Fore.GREEN}PARALEL ANTA-PLATFORM (Semua platform berjalan bersamaan){Style.RESET_ALL}")
    print(f"Keywords ({len(selected_keywords)}) : {Fore.CYAN}{', '.join(selected_keywords)}{Style.RESET_ALL}")
    print(f"Anti-Bot Delay: {Fore.GREEN}Aktif (Jeda acak 2.5s - 5.0s per request pada platform yang sama){Style.RESET_ALL}")
    print(f"Batas Tanggal : {Fore.CYAN}{since_date.strftime('%Y-%m-%d')} (mundur {months} bulan){Style.RESET_ALL}")
    print(f"Maksimal/Run  : {Fore.CYAN}{limit} item{Style.RESET_ALL}")
    print(f"Folder Output : {Fore.CYAN}{output_dir}{Style.RESET_ALL}")
    print(f"Shortcut Stop : {Fore.YELLOW}Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja{Style.RESET_ALL}")
    print(f"{Fore.MAGENTA}===================================================={Style.RESET_ALL}")

    confirm = input(f"\n{Fore.GREEN}Lanjutkan eksekusi crawling? (y/n) [Default: y]: {Style.RESET_ALL}").strip().lower()
    if confirm and confirm != "y":
        print(f"{Fore.YELLOW}Dibatalkan oleh pengguna.{Style.RESET_ALL}")
        return

    print(f"\n{Fore.GREEN}>>> MEMULAI PARALEL BATCH CRAWLING ({len(selected_platforms)} WORKER AKTIF) <<<{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Tips: Tekan [Ctrl+C] atau [q] atau [ESC] kapan saja untuk menghentikan dengan aman.{Style.RESET_ALL}\n")

    results_summary = []
    print_lock = threading.Lock()
    stop_event = threading.Event()
    progress_state = {"done": 0, "total": len(selected_platforms) * len(selected_keywords)}
    start_total_time = time.time()

    # Daftarkan handler SIGINT (Ctrl+C)
    def _sigint_handler(signum, frame):
        with print_lock:
            print(f"\n{Fore.YELLOW}🛑 [Ctrl+C DITERIMA] Menghentikan semua worker platform secara aman & menyimpan file data...{Style.RESET_ALL}")
        stop_event.set()

    old_sigint = signal.signal(signal.SIGINT, _sigint_handler)

    # Jalankan background thread listener untuk tombol 'q' / ESC
    kb_thread = threading.Thread(
        target=_listen_keyboard_stop,
        args=(stop_event, print_lock),
        daemon=True,
    )
    kb_thread.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected_platforms)) as executor:
        futures = {
            executor.submit(
                run_platform_worker,
                plat,
                selected_keywords,
                since_date,
                limit,
                output_dir,
                results_summary,
                print_lock,
                stop_event,
                progress_state,
            ): plat
            for plat in selected_platforms
        }

        try:
            # Polling berkala agar Ctrl+C responsif seketika
            while any(not f.done() for f in futures.keys()):
                if stop_event.is_set():
                    break
                time.sleep(0.2)

            # Tunggu sisa worker yang sedang memfinalisasi flush
            for f in futures.keys():
                try:
                    f.result(timeout=1.0)
                except Exception:
                    pass

        except KeyboardInterrupt:
            with print_lock:
                print(f"\n{Fore.YELLOW}🛑 [Ctrl+C DITERIMA] Menyelesaikan dan menutup file...{Style.RESET_ALL}")
            stop_event.set()
        finally:
            # Kembalikan signal handler awal
            signal.signal(signal.SIGINT, old_sigint)

    total_duration = round(time.time() - start_total_time, 1)

    print(f"\n{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}            RINGKASAN PARALEL BATCH CRAWLING          {Style.RESET_ALL}")
    print(f"{Fore.CYAN}======================================================{Style.RESET_ALL}")
    print(f"{'Platform':<12} {'Keyword':<30} {'Jumlah':<8} {'Durasi':<8} {'File Output'}")
    print("-" * 85)
    total_all_items = 0
    for plat, kw, count, fname, dur in results_summary:
        total_all_items += count
        color = Fore.GREEN if count > 0 else Fore.YELLOW
        print(f"{plat:<12} {kw:<30} {color}{count:<8}{Style.RESET_ALL} {dur:<8} {fname}")
    print("-" * 85)
    print(f"{Fore.WHITE}Total Seluruh Item Terkumpul: {Fore.GREEN}{total_all_items} item{Style.RESET_ALL}")
    print(f"Total Waktu Eksekusi Paralel: {Fore.CYAN}{total_duration} detik{Style.RESET_ALL}")
    print(f"Semua file CSV & JSON tersimpan di: {output_dir}\n")

    if total_all_items > 0:
        _prompt_and_run_post_crawl_merge(output_dir)


if __name__ == "__main__":
    run_interactive()
