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

from .exporter import ProgressiveExporter
from .platforms.facebook import scrape_facebook
from .platforms.instagram import scrape_instagram_hashtag, scrape_instagram_user
from .platforms.threads import scrape_threads
from .platforms.tiktok import scrape_tiktok
from .platforms.twitter import scrape_twitter
from .platforms.youtube import scrape_youtube
from .utils.cookies import find_platform_cookie

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
):
    """Worker platform mandiri.

    Menjalankan list keyword secara sekuensial dengan jeda interval anti-bot aman,
    sementara platform lain berjalan secara simultan/paralel di thread lain.
    """
    plat_color = PLATFORM_COLORS.get(platform, Fore.CYAN)
    tag = f"{plat_color}[{platform.upper()}]{Style.RESET_ALL}"

    with lock:
        print(f"{tag} {Fore.GREEN}Worker platform aktif! Memulai {len(keywords)} antrean keyword...{Style.RESET_ALL}")

    for kw_idx, kw in enumerate(keywords, 1):
        if stop_event.is_set():
            with lock:
                print(f"{tag} {Fore.YELLOW}Menghentikan sisa antrean karena sinyal stop.{Style.RESET_ALL}")
            break

        clean_kw = "".join(c if c.isalnum() else "_" for c in kw)[:25]
        prefix = f"{platform}_{clean_kw}"
        exporter = ProgressiveExporter(output_dir=output_dir, prefix=prefix)

        with lock:
            print(f"{tag} {Fore.YELLOW}[{kw_idx}/{len(keywords)}] Keyword: '{kw}' | Target: {exporter.csv_path.name}{Style.RESET_ALL}")

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
            if item_count > 0:
                print(f"{tag} {Fore.GREEN}✓ Selesai '{kw}': {item_count} item tersimpan ke {exporter.csv_path.name} ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, kw, item_count, exporter.csv_path.name, f"{duration}s"))
            else:
                print(f"{tag} {Fore.YELLOW}⚠ Selesai '{kw}': Tidak ada data ({duration}s){Style.RESET_ALL}")
                results_collector.append((platform, kw, 0, "-", f"{duration}s"))

        # Jeda anti-bot antar keyword di platform yang sama (interruptible)
        if kw_idx < len(keywords) and not stop_event.is_set():
            pause = round(random.uniform(3.0, 5.5), 2)
            with lock:
                print(f"{tag} {Fore.LIGHTBLACK_EX}Jeda anti-bot antar-keyword: {pause}s...{Style.RESET_ALL}")
            if stop_event.wait(timeout=pause):
                break

    with lock:
        print(f"{tag} {Fore.GREEN}Seluruh antrean untuk {platform.upper()} telah selesai!{Style.RESET_ALL}")


def run_interactive():
    print_header()

    selected_platforms = prompt_select_platforms()
    selected_keywords = prompt_select_keywords()
    months, limit = prompt_settings()

    since_date = datetime.now(timezone.utc) - timedelta(days=months * 30)
    output_dir = Path(__file__).resolve().parent.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

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


if __name__ == "__main__":
    run_interactive()
