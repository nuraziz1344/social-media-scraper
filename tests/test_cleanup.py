"""Unit test untuk script cleanup.py."""
import csv
import tempfile
import unittest
from pathlib import Path

from cleanup import (
    DEFAULT_PLACEHOLDER_AUTHORS,
    clean_csv,
    resolve_column_mapping,
    validate_author,
    validate_body,
    validate_timestamp,
    validate_url,
)


class TestCleanupValidators(unittest.TestCase):
    def test_validate_timestamp(self):
        # 1. Empty timestamp
        self.assertEqual(validate_timestamp("", "2026-10-22 05:00:00"), ["timestamp_kosong"])
        self.assertEqual(validate_timestamp("   ", "2026-10-22 05:00:00"), ["timestamp_kosong"])
        self.assertEqual(validate_timestamp("None", "2026-10-22 05:00:00"), ["timestamp_kosong"])
        self.assertEqual(validate_timestamp("null", "2026-10-22 05:00:00"), ["timestamp_kosong"])

        # 2. Same as input timestamp
        self.assertEqual(
            validate_timestamp("2026-10-22 05:00:00", "2026-10-22 05:00:00"),
            ["timestamp_sama_dengan_input"],
        )
        self.assertEqual(
            validate_timestamp("2026-10-22T05:00:00Z", "2026-10-22T05:00:00+00:00"),
            ["timestamp_sama_dengan_input"],
        )

        # 3. Valid different timestamps
        self.assertEqual(
            validate_timestamp("2026-10-21 20:00:00", "2026-10-22 05:00:00"),
            [],
        )

    def test_validate_body(self):
        # Empty body
        self.assertEqual(validate_body(""), ["body_kosong"])
        self.assertEqual(validate_body("   \n\t  "), ["body_kosong"])
        self.assertEqual(validate_body("none"), ["body_kosong"])
        self.assertEqual(validate_body("null"), ["body_kosong"])

        # Valid body
        self.assertEqual(validate_body("Kebakaran di lahan gambut"), [])
        self.assertEqual(validate_body("🔥 Alert update"), [])

    def test_validate_author(self):
        placeholders = set(DEFAULT_PLACEHOLDER_AUTHORS)

        # Empty / invalid
        self.assertEqual(validate_author("", placeholders), ["author_kosong"])
        self.assertEqual(validate_author("@", placeholders), ["author_kosong"])
        self.assertEqual(validate_author("none", placeholders), ["author_kosong"])

        # Placeholders in strict mode
        self.assertTrue(any("author_placeholder" in r for r in validate_author("@instagram_user", placeholders)))
        self.assertTrue(any("author_placeholder" in r for r in validate_author("@facebook_user", placeholders)))
        self.assertTrue(any("author_placeholder" in r for r in validate_author("tidak_dikenal", placeholders)))

        # Placeholders in lenient mode
        self.assertEqual(validate_author("@instagram_user", placeholders, lenient=True), [])

        # Allowed placeholders
        allowed = {"@instagram_user", "instagram_user"}
        self.assertEqual(validate_author("@instagram_user", placeholders, lenient=False, allowed_placeholders=allowed), [])
        self.assertTrue(any("author_placeholder" in r for r in validate_author("@facebook_user", placeholders, lenient=False, allowed_placeholders=allowed)))

        # Valid normal authors
        self.assertEqual(validate_author("@kompascom", placeholders), [])
        self.assertEqual(validate_author("johndoe2026", placeholders), [])

    def test_validate_url(self):
        # Empty
        self.assertEqual(validate_url(""), ["url_kosong"])
        self.assertEqual(validate_url("   "), ["url_kosong"])

        # Malformed
        self.assertEqual(validate_url("not_a_valid_url"), ["url_malformed"])
        self.assertEqual(validate_url("ftp://example.com/file"), ["url_malformed"])

        # Homepage only
        self.assertEqual(validate_url("https://www.facebook.com"), ["url_hanya_homepage"])
        self.assertEqual(validate_url("https://www.facebook.com/"), ["url_hanya_homepage"])
        self.assertEqual(validate_url("https://x.com"), ["url_hanya_homepage"])
        self.assertEqual(validate_url("https://tiktok.com/"), ["url_hanya_homepage"])

        # Valid specific URLs
        self.assertEqual(validate_url("https://x.com/kompascom/status/1234567890"), [])
        self.assertEqual(validate_url("https://www.instagram.com/p/DexOmmyTa1x/"), [])
        self.assertEqual(validate_url("https://www.tiktok.com/@user/video/987654321"), [])


class TestCleanupEndToEnd(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_csv_end_to_end(self):
        test_file = self.dir_path / "test_data.csv"
        headers = [
            "ID Eksternal", "Waktu Input", "Waktu Posting", "Platform",
            "Penulis / Akun", "Jenis Konten", "Isi Sebutan", "Total Interaksi", "Tautan Asli"
        ]

        rows = [
            # 1. Valid row
            {
                "ID Eksternal": "tw-101",
                "Waktu Input": "2026-10-22 05:00:00",
                "Waktu Posting": "2026-10-22 04:30:00",
                "Platform": "X (Twitter)",
                "Penulis / Akun": "@valid_reporter",
                "Jenis Konten": "Posting/Artikel",
                "Isi Sebutan": "Laporan kebakaran terkini di lokasi.",
                "Total Interaksi": "10",
                "Tautan Asli": "https://x.com/valid_reporter/status/101",
            },
            # 2. Invalid: Empty posting time
            {
                "ID Eksternal": "fb-201",
                "Waktu Input": "2026-10-22 05:00:00",
                "Waktu Posting": "",
                "Platform": "Facebook",
                "Penulis / Akun": "@facebook_user",
                "Jenis Konten": "Posting/Artikel",
                "Isi Sebutan": "Teks ada tapi waktu kosong dan URL homepage",
                "Total Interaksi": "0",
                "Tautan Asli": "https://www.facebook.com",
            },
            # 3. Invalid: Same timestamp
            {
                "ID Eksternal": "tw-301",
                "Waktu Input": "2026-10-22 05:00:00",
                "Waktu Posting": "2026-10-22 05:00:00",
                "Platform": "X (Twitter)",
                "Penulis / Akun": "@same_timer",
                "Jenis Konten": "Posting/Artikel",
                "Isi Sebutan": "Postingan dengan timestamp persis input",
                "Total Interaksi": "2",
                "Tautan Asli": "https://x.com/same_timer/status/301",
            },
            # 4. Invalid: Empty body
            {
                "ID Eksternal": "ig-401",
                "Waktu Input": "2026-10-22 05:00:00",
                "Waktu Posting": "2026-10-22 03:00:00",
                "Platform": "Instagram",
                "Penulis / Akun": "@valid_photographer",
                "Jenis Konten": "Posting/Artikel",
                "Isi Sebutan": "",
                "Total Interaksi": "50",
                "Tautan Asli": "https://www.instagram.com/p/ig401/",
            },
            # 5. Invalid: Placeholder author (@instagram_user)
            {
                "ID Eksternal": "ig-501",
                "Waktu Input": "2026-10-22 05:00:00",
                "Waktu Posting": "2026-10-22 03:00:00",
                "Platform": "Instagram",
                "Penulis / Akun": "@instagram_user",
                "Jenis Konten": "Posting/Artikel",
                "Isi Sebutan": "Berita lengkap dengan foto karhutla",
                "Total Interaksi": "20",
                "Tautan Asli": "https://www.instagram.com/p/ig501/",
            },
        ]

        with open(test_file, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            w.writerows(rows)

        # Run strict cleanup with save_rejected=True
        res = clean_csv(
            input_path=test_file,
            save_rejected=True,
        )

        self.assertEqual(res.total_rows, 5)
        self.assertEqual(res.clean_rows, 1)  # Only row 1 is clean
        self.assertEqual(res.rejected_rows, 4)  # Rows 2, 3, 4, 5 rejected

        # Verify output file
        cleaned_file = self.dir_path / "test_data-cleaned.csv"
        self.assertTrue(cleaned_file.exists())
        with open(cleaned_file, "r", encoding="utf-8-sig") as f:
            clean_rows = list(csv.DictReader(f))
        self.assertEqual(len(clean_rows), 1)
        self.assertEqual(clean_rows[0]["ID Eksternal"], "tw-101")

        # Verify rejected file
        rejected_file = self.dir_path / "test_data-rejected.csv"
        self.assertTrue(rejected_file.exists())
        with open(rejected_file, "r", encoding="utf-8-sig") as f:
            rej_rows = list(csv.DictReader(f))
        self.assertEqual(len(rej_rows), 4)
        for r in rej_rows:
            self.assertIn("Alasan Ditolak", r)
            self.assertTrue(len(r["Alasan Ditolak"]) > 0)

        # Test lenient / allowed placeholder for @instagram_user
        res_lenient = clean_csv(
            input_path=test_file,
            allowed_placeholders=["instagram_user"],
            dry_run=True,
        )
        # In lenient mode for @instagram_user, row 5 becomes clean, row 4 still has empty body
        self.assertEqual(res_lenient.clean_rows, 2)
        self.assertEqual(res_lenient.rejected_rows, 3)


if __name__ == "__main__":
    unittest.main()
