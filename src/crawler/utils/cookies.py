"""Utilitas manajemen, parser, dan pembaruan aman cookie.

Mendukung pembacaan file .txt format Netscape / Mozilla Cookie File
(hasil export ekstensi browser seperti Get cookies.txt locally, CookieJar, Cookie-Editor)
dan format Playwright storage_state (.json), serta pembaruan atomik yang aman.
"""
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cookie esensial yang menandakan session login aktif per platform
REQUIRED_SESSION_COOKIES: dict[str, tuple[tuple[str, ...], ...]] = {
    # Setiap inner-tuple adalah alternatif (minimal salah satu ada)
    "x": (("auth_token",), ("ct0",)),
    "instagram": (("sessionid",), ("csrftoken",)),
    "threads": (("sessionid",), ("csrftoken",)),
    "facebook": (("c_user",), ("xs",)),
    "tiktok": (("sessionid", "sid_tt", "sessionid_ss"),),
    "youtube": (),  # YouTube opsional (bisa tanpa cookie)
}

_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[Path, threading.Lock] = {}


def _get_file_lock(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[resolved] = lock
        return lock


class CookieEntry:
    def __init__(
        self,
        domain: str,
        include_subdomains: bool,
        path: str,
        secure: bool,
        expires: int,
        name: str,
        value: str,
        http_only: bool = False,
        same_site: str = "Lax",
    ):
        self.domain = domain
        self.include_subdomains = include_subdomains
        self.path = path
        self.secure = secure
        self.expires = expires
        self.name = name
        self.value = value
        self.http_only = http_only
        self.same_site = same_site

    def to_playwright_dict(self) -> dict[str, Any]:
        cookie_dict: dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site,
        }
        if self.expires > 0:
            cookie_dict["expires"] = float(self.expires)
        return cookie_dict


def parse_netscape_cookies(file_path: Path | str) -> list[CookieEntry]:
    """Parse file cookies.txt (Netscape format) menjadi daftar objek CookieEntry."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File cookie tidak ditemukan: {path}")

    entries: list[CookieEntry] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) < 7:
                # Coba split dengan whitespace bila ada format alternatif
                parts = line.split()
                if len(parts) < 7:
                    continue

            domain = parts[0].strip()
            include_subdomains = parts[1].strip().upper() == "TRUE"
            cookie_path = parts[2].strip()
            secure = parts[3].strip().upper() == "TRUE"
            try:
                expires = int(parts[4].strip())
            except ValueError:
                expires = -1
            name = parts[5].strip()
            value = parts[6].strip() if len(parts) > 6 else ""

            entries.append(
                CookieEntry(
                    domain=domain,
                    include_subdomains=include_subdomains,
                    path=cookie_path,
                    secure=secure,
                    expires=expires,
                    name=name,
                    value=value,
                )
            )
    return entries


def load_cookie_entries(cookie_path: Path | str) -> list[CookieEntry]:
    """Muat cookie dari file .txt (Netscape) atau .json (Playwright storage_state)."""
    path = Path(cookie_path)
    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_cookies = data.get("cookies", []) if isinstance(data, dict) else []
        entries: list[CookieEntry] = []
        for c in raw_cookies:
            if not isinstance(c, dict):
                continue
            entries.append(
                CookieEntry(
                    domain=str(c.get("domain", "")),
                    include_subdomains=str(c.get("domain", "")).startswith("."),
                    path=str(c.get("path", "/")),
                    secure=bool(c.get("secure", False)),
                    expires=int(float(c.get("expires", -1) or -1)),
                    name=str(c.get("name", "")),
                    value=str(c.get("value", "")),
                    http_only=bool(c.get("httpOnly", False)),
                    same_site=str(c.get("sameSite", "Lax") or "Lax"),
                )
            )
        return entries

    return parse_netscape_cookies(path)


def cookies_to_dict(entries: list[CookieEntry]) -> dict[str, str]:
    """Konversi daftar cookie ke mapping key-value {name: value}."""
    return {entry.name: entry.value for entry in entries}


def has_valid_session_cookies(
    platform: str,
    cookies_by_name: dict[str, str],
) -> bool:
    """Pastikan seluruh token session esensial platform tersedia dan tidak kosong."""
    requirements = REQUIRED_SESSION_COOKIES.get(platform.lower(), ())
    for alt_group in requirements:
        if not any(bool(cookies_by_name.get(name)) for name in alt_group):
            return False
    return True


def to_playwright_storage_state(
    file_path: Path | str, output_path: Path | str | None = None
) -> dict[str, Any]:
    """Konversi file cookies (.txt Netscape atau .json) ke format storage_state Playwright."""
    entries = load_cookie_entries(file_path)
    state = {
        "cookies": [entry.to_playwright_dict() for entry in entries],
        "origins": [],
    }
    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    return state


def _sanitize_netscape_field(val: str) -> str:
    return val.replace("\r", "").replace("\n", "").replace("\t", " ")


def _atomic_write_text(path: Path, content: str):
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def merge_and_save_cookies(
    cookie_file: Path | str,
    updated_cookies: list[dict[str, Any]],
    platform: str,
) -> bool:
    """Gabungkan cookie terbaru dari browser ke file cookie secara aman & atomik.

    Melindungi file cookie agar tidak tertimpa oleh session kosong/logout.
    Mengembalikan True jika ada perubahan yang berhasil disimpan.
    """
    path = Path(cookie_file)
    if not path.is_file() or not updated_cookies:
        return False

    now = time.time()
    plat = platform.lower()
    essential_names = {
        name for grp in REQUIRED_SESSION_COOKIES.get(plat, ()) for name in grp
    }

    # Filter cookie yang valid & belum kedaluwarsa saat mengecek kelengkapan session
    live_by_name: dict[str, str] = {}
    for c in updated_cookies:
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") if c.get("value") is not None else "")
        if not name or not value:
            continue
        try:
            exp_val = float(c.get("expires", -1) or -1)
        except (TypeError, ValueError):
            exp_val = -1.0
        if exp_val > 0 and exp_val < now:
            continue
        live_by_name[name] = value

    if not has_valid_session_cookies(plat, live_by_name):
        logger.debug(
            "Melewati penyimpanan cookie %s: token session esensial tidak lengkap atau kedaluwarsa.",
            plat,
        )
        return False

    lock = _get_file_lock(path)
    with lock:
        existing_entries = load_cookie_entries(path)
        existing_origins: list[Any] = []
        if path.suffix.lower() == ".json":
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("origins"), list):
                    existing_origins = raw["origins"]
            except Exception:
                pass

        merged: dict[tuple[str, str, str], CookieEntry] = {}
        for entry in existing_entries:
            key = (entry.domain.lower(), entry.path or "/", entry.name)
            merged[key] = entry

        changed = False
        for c in updated_cookies:
            name = str(c.get("name") or "").strip()
            domain = str(c.get("domain") or "").strip()
            c_path = str(c.get("path") or "/")
            value = str(c.get("value") if c.get("value") is not None else "")
            if not name or not domain:
                continue

            try:
                exp_float = float(c.get("expires", -1) or -1)
                expires = int(exp_float) if exp_float > 0 else 0
            except (TypeError, ValueError):
                expires = 0

            # Jangan timpa token esensial dengan nilai kosong atau sudah kedaluwarsa
            if name in essential_names and (not value or (expires > 0 and expires < now)):
                continue

            secure = bool(c.get("secure", False))
            http_only = bool(c.get("httpOnly", False))
            same_site = str(c.get("sameSite", "Lax") or "Lax")
            include_sub = domain.startswith(".")
            key = (domain.lower(), c_path, name)

            existing = merged.get(key)
            if existing is not None:
                # Jangan hapus expiry positif yang sudah ada jika Playwright mengembalikan session expiry (0) dan value sama
                new_expires = expires if expires > 0 else (0 if value != existing.value else existing.expires)
                if (
                    existing.value != value
                    or existing.expires != new_expires
                    or existing.secure != secure
                ):
                    merged[key] = CookieEntry(
                        domain=domain,
                        include_subdomains=include_sub,
                        path=c_path,
                        secure=secure,
                        expires=new_expires,
                        name=name,
                        value=value,
                        http_only=http_only,
                        same_site=same_site,
                    )
                    changed = True
            else:
                merged[key] = CookieEntry(
                    domain=domain,
                    include_subdomains=include_sub,
                    path=c_path,
                    secure=secure,
                    expires=expires,
                    name=name,
                    value=value,
                    http_only=http_only,
                    same_site=same_site,
                )
                changed = True

        if not changed:
            return False

        if path.suffix.lower() == ".json":
            payload = {
                "cookies": [e.to_playwright_dict() for e in merged.values()],
                "origins": existing_origins,
            }
            _atomic_write_text(path, json.dumps(payload, indent=2))
        else:
            lines = ["# Netscape HTTP Cookie File"]
            for e in merged.values():
                sub_flag = "TRUE" if e.domain.startswith(".") or e.include_subdomains else "FALSE"
                sec_flag = "TRUE" if e.secure else "FALSE"
                exp_int = e.expires if e.expires > 0 else 0
                lines.append(
                    "\t".join([
                        _sanitize_netscape_field(e.domain),
                        sub_flag,
                        _sanitize_netscape_field(e.path or "/"),
                        sec_flag,
                        str(exp_int),
                        _sanitize_netscape_field(e.name),
                        _sanitize_netscape_field(e.value),
                    ])
                )
            _atomic_write_text(path, "\n".join(lines) + "\n")

        logger.debug("Berhasil memperbarui cookie %s ke %s (%d entri).", plat, path.name, len(merged))
        return True


async def persist_playwright_cookies(
    context: Any,
    cookie_file: Path | str | None,
    platform: str,
) -> bool:
    """Ambil cookie terbaru dari Playwright context dan simpan ke file cookie secara aman."""
    if not cookie_file:
        return False
    try:
        updated = await context.cookies()
        return merge_and_save_cookies(cookie_file, updated, platform=platform)
    except Exception as exc:
        logger.debug("Gagal menyimpan pembaruan cookie %s: %s", platform, exc)
        return False


def extract_twitter_tokens(file_path: Path | str) -> dict[str, str]:
    """Mengekstrak token esensial Twitter/X (auth_token dan ct0)."""
    entries = load_cookie_entries(file_path)
    c_dict = cookies_to_dict(entries)
    auth_token = c_dict.get("auth_token", "")
    ct0 = c_dict.get("ct0", "")
    return {"auth_token": auth_token, "ct0": ct0}


def extract_instagram_tokens(file_path: Path | str) -> dict[str, str]:
    """Mengekstrak sessionid dan csrftoken Instagram."""
    entries = load_cookie_entries(file_path)
    c_dict = cookies_to_dict(entries)
    return {
        "sessionid": c_dict.get("sessionid", ""),
        "csrftoken": c_dict.get("csrftoken", ""),
        "ds_user_id": c_dict.get("ds_user_id", ""),
    }


def find_platform_cookie(platform: str, base_dir: Path | None = None) -> Path | None:
    """Mencari file cookie untuk platform tertentu di folder cookies/."""
    if base_dir is None:
        # Asumsikan base_dir adalah root crawler (D:\projects\crawler)
        base_dir = Path(__file__).resolve().parent.parent.parent.parent

    cookies_dir = base_dir / "cookies"
    if not cookies_dir.exists():
        return None

    # Daftar nama file yang mungkin untuk platform
    possible_names = [
        f"{platform}.txt",
        f"{platform}_cookies.txt",
        f"cookies_{platform}.txt",
        f"{platform}.json",
    ]
    if platform in ("x", "twitter"):
        possible_names.extend(["x.txt", "twitter.txt"])

    for name in possible_names:
        candidate = cookies_dir / name
        if candidate.is_file():
            return candidate

    return None
