# Social Media Crawler (Self-Hosted)

Tool scraping mandiri untuk mengambil data media sosial 6 bulan terakhir tanpa biaya kredit Apify. Data diekspor dalam format JSON terstandarisasi yang kompatibel penuh dengan pipeline ingestion database [`pantaudev`](file:///D:/projects/pantaudev).

---

## 1. Persiapan Cookie / Session

Sebagian besar platform media sosial (terutama X, Facebook, Instagram, Threads, TikTok) membutuhkan sesi login. Cukup ekspor cookie dari browser Anda menggunakan ekstensi seperti **Get cookies.txt locally**, **CookieJar**, atau **Cookie-Editor** (format Netscape / Text).

Simpan file cookie di dalam folder `cookies/` dengan penamaan berikut:
- **X (Twitter)**: `cookies/x.txt`
- **Instagram**: `cookies/instagram.txt`
- **Facebook**: `cookies/facebook.txt`
- **TikTok**: `cookies/tiktok.txt`
- **Threads**: `cookies/threads.txt`
- **YouTube**: `cookies/youtube.txt` *(opsional, YouTube bisa berjalan tanpa cookie)*

Cek status ketersediaan cookie Anda kapan saja dengan perintah:
```bash
uv run crawler cookies list
```

---

## 2. Cara Menjalankan (Interactive Wizard — Paralel Multi-Platform)

Anda dapat menjalankan script wizard interaktif dengan cukup mengetikkan salah satu perintah berikut di terminal:
```bash
uv run run.py
# atau
uv run crawler
```
Menu interaktif akan memandu Anda:
1. Memilih platform (YouTube, X, Instagram, TikTok, Facebook, Threads, atau Semua).
2. Memilih preset keyword (Kulon Progo, Bupati Agung Setyawan, Wabup Ambar Purwoko, atau keyword kustom).
3. Mengatur rentang waktu (default: 6 bulan terakhir).
4. **Eksekusi Paralel Antar-Platform:**
   - Semua platform yang dipilih akan **berjalan secara bersamaan (simultan)** dalam worker thread masing-masing.
   - **Anti-Bot Throttling:** Pada setiap platform yang sama, antrean request dan keyword dijalankan secara sekuensial dengan jeda acak (2.5 - 5.0 detik) untuk mencegah deteksi bot dan rate limit.
   - **Progressive Writing:** Setiap item yang ditemukan langsung diflush ke disk (CSV & JSONL).

### 🛑 Shortcut / Keybind Menghentikan Crawling Secara Aman

Kapan saja saat proses crawling berjalan, Anda dapat menekan salah satu shortcut berikut:
- **`Ctrl + C`** (Standar terminal interrupt)
- **`q`** atau **`Q`** (Tombol cepat berhenti)
- **`ESC`** (Tombol escape)

**Apa yang terjadi saat tombol ditekan?**
1. Sinyal berhenti (`stop_event`) langsung dikirim ke seluruh worker thread platform seketika (tanpa menunggu jeda tidur/sleep selesai).
2. Peramban headless (Playwright) ditutup secara bersih.
3. Seluruh data yang sudah dikumpulkan detik itu diflush dan difinalisasi ke disk (`.csv` dan `.json`).
4. Terminal mencetak tabel ringkasan data yang berhasil diselamatkan dan disimpan.

---

## 3. Cara Penggunaan Manual per Perintah (CLI)

Semua perintah dijalankan menggunakan `uv run crawler`.

### A. YouTube
Menggunakan `scrapetube` + `yt-dlp` (mengekstrak metadata video dan semua komentar).
```bash
# Crawl video & komentar kata kunci "kulon progo" mundur 6 bulan (default)
uv run crawler scrape --platform youtube --query "kulon progo" --max-results 20 --months 6

# Tanpa mengambil komentar video (hanya metadata video)
uv run crawler scrape --platform youtube --query "kulon progo" --max-results 50 --no-comments
```

---

### B. X (Twitter)
Menggunakan `twscrape` dengan dukungan operator pencarian tanggal (`since:` & `until:`).
```bash
# Crawl tweet pencarian mundur 6 bulan
uv run crawler scrape --platform x --query "kulon progo" --max-results 100 --months 6

# Crawl dengan rentang tanggal manual spesifik
uv run crawler scrape --platform x --query "kulon progo" --since 2026-04-12 --until 2026-10-12 --max-results 200

# Crawl linimasa akun tertentu
uv run crawler scrape --platform x --account "pemkabkulonprogo" --max-results 50
```

---

### C. Instagram
Menggunakan `instagrapi` dengan sesi login dari `cookies/instagram.txt`.
```bash
# Crawl postingan & komentar akun dinas/media lokal mundur 6 bulan
uv run crawler scrape --platform instagram --account "pemkabkulonprogo" --months 6 --max-results 50

# Crawl berdasarkan hashtag
uv run crawler scrape --platform instagram --query "#kulonprogo" --max-results 30
```

---

### D. TikTok
Menggunakan Playwright headless browser dengan intersepsi respons API internal.
```bash
uv run crawler scrape --platform tiktok --query "kulon progo" --max-results 50 --months 6

# Mode headed (melihat browser terbuka untuk debugging)
uv run crawler scrape --platform tiktok --query "kulon progo" --max-results 20 --headed
```

---

### E. Facebook
Menggunakan Playwright dengan cookie `cookies/facebook.txt` dan intersepsi feed/GraphQL.
```bash
uv run crawler scrape --platform facebook --query "kulon progo" --max-results 50 --months 6
```

---

### F. Threads
Menggunakan Playwright dengan cookie `cookies/threads.txt` dan intersepsi GraphQL.
```bash
uv run crawler scrape --platform threads --query "kulon progo" --max-results 50 --months 6
```

---

## 3. Hasil Output

Semua hasil crawling akan disimpan di folder `output/` dalam format JSON:
`output/<platform>_<query>_<timestamp>.json`

Setiap item memiliki struktur standar:
```json
{
  "external_id": "yt-QYijjdM-qFs",
  "url": "https://www.youtube.com/watch?v=QYijjdM-qFs",
  "content": "Isi postingan / teks caption / video description",
  "author": "@username",
  "published_at": "2026-08-31T05:12:59+00:00",
  "crawled_at": "2026-10-12T07:28:08+00:00",
  "engagement_count": 266,
  "reach": 73604,
  "engagement": {
    "likes": 219,
    "comments": 47,
    "shares": 0
  },
  "parent_external_id": null
}
```

Format ini kompatibel 100% dan dapat langsung di-ingest ke sistem [`pantaudev`](file:///D:/projects/pantaudev) via `apps.governance.ingestion.ingest_items`.
