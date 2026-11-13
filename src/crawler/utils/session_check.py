"""Utilitas pengecekan validitas session / cookie untuk setiap platform."""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .accounts import normalize_platform
from .cookies import (
    REQUIRED_SESSION_COOKIES,
    CookieEntry,
    find_platform_cookie,
    load_cookie_entries,
)

logger = logging.getLogger("crawler.session_check")

ALL_PLATFORMS = ("youtube", "x", "instagram", "tiktok", "facebook", "threads")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass
class SessionCheckResult:
    platform: str
    cookie_path: Path | None
    file_exists: bool
    has_required_tokens: bool
    missing_tokens: list[str] = field(default_factory=list)
    expired_tokens: list[str] = field(default_factory=list)
    earliest_expiry: datetime | None = None
    live_valid: bool | None = None
    username: str | None = None
    status: str = "unknown"  # valid | expired | invalid | missing | optional | error
    detail: str = ""


def _evaluate_local_cookies(
    platform: str,
    entries: list[CookieEntry],
    now_ts: float | None = None,
) -> tuple[bool, list[str], list[str], datetime | None]:
    """Cek kelengkapan token esensial dan masa berlaku cookie secara lokal."""
    now = now_ts if now_ts is not None else time.time()
    by_name: dict[str, CookieEntry] = {e.name: e for e in entries if e.value}

    requirements = REQUIRED_SESSION_COOKIES.get(platform, ())
    missing: list[str] = []
    expired: list[str] = []
    expiries: list[int] = []

    for alt_group in requirements:
        present = [name for name in alt_group if name in by_name]
        if not present:
            missing.append("/".join(alt_group))
            continue
        # Cek apakah semua kandidat yang ada sudah kedaluwarsa
        non_expired = []
        for name in present:
            exp = by_name[name].expires
            if exp > 0:
                expiries.append(exp)
                if exp < now:
                    continue
            non_expired.append(name)
        if not non_expired:
            expired.append(present[0])

    earliest_expiry = (
        datetime.fromtimestamp(min(expiries), tz=timezone.utc) if expiries else None
    )
    has_required = len(missing) == 0
    return has_required, missing, expired, earliest_expiry


def _entries_to_httpx_cookies(entries: list[CookieEntry]) -> httpx.Cookies:
    jar = httpx.Cookies()
    for e in entries:
        if e.name and e.value is not None:
            jar.set(e.name, e.value, domain=e.domain or None, path=e.path or "/")
    return jar


def _probe_live_session(
    platform: str,
    entries: list[CookieEntry],
    timeout: float = 12.0,
) -> tuple[bool | None, str | None, str]:
    """Lakukan pengecekan ringan ke endpoint platform untuk memverifikasi session aktif."""
    jar = _entries_to_httpx_cookies(entries)
    by_name = {e.name: e.value for e in entries}
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        with httpx.Client(cookies=jar, headers=headers, timeout=timeout, follow_redirects=False) as client:
            if platform == "x":
                resp = client.get("https://x.com/home")
                loc = resp.headers.get("location", "").lower()
                if resp.status_code == 200:
                    m = re.search(r'"screen_name":"([^"]+)"', resp.text)
                    uname = f"@{m.group(1)}" if m else None
                    return True, uname, "Session X aktif"
                if resp.status_code in (301, 302, 303, 307, 308, 401, 403):
                    if resp.status_code in (401, 403) or loc in ("/", "") or "login" in loc:
                        return False, None, f"Session X kedaluwarsa / perlu login ulang (HTTP {resp.status_code})"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

            if platform == "instagram":
                resp = client.get("https://www.instagram.com/accounts/edit/")
                loc = resp.headers.get("location", "").lower()
                if resp.status_code == 200:
                    m = re.search(r'"username":"([^"]+)"', resp.text)
                    uname = f"@{m.group(1)}" if m else None
                    return True, uname, "Session Instagram aktif"
                if resp.status_code in (301, 302, 303, 307, 308) and "login" in loc:
                    return False, None, "Session Instagram kedaluwarsa / perlu login ulang"
                if resp.status_code in (401, 403):
                    return False, None, f"Ditolak server Instagram (HTTP {resp.status_code})"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

            if platform == "threads":
                resp = client.get("https://www.threads.com/")
                loc = resp.headers.get("location", "").lower()
                if resp.status_code in (301, 302) and "login" in loc:
                    return False, None, "Redirect ke halaman login Threads"
                if resp.status_code == 200:
                    body = resp.text
                    if '"viewer_id"' in body or '"is_logged_in":true' in body or (by_name.get("ds_user_id") and by_name["ds_user_id"] in body):
                        return True, None, "Session Threads aktif"
                    if "login" in body.lower() and "sessionid" not in by_name:
                        return False, None, "Halaman Threads meminta login"
                    return True, None, "Cookie Threads diterima server"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

            if platform == "facebook":
                resp = client.get("https://www.facebook.com/")
                loc = resp.headers.get("location", "").lower()
                if resp.status_code in (301, 302, 303, 307, 308):
                    if "login" in loc or "checkpoint" in loc:
                        return False, None, f"Redirect ke {loc.split('?')[0]}"
                if resp.status_code == 200:
                    m = re.search(r'"USER_ID":"(\d+)"', resp.text)
                    if m and m.group(1) != "0":
                        return True, None, "Session Facebook aktif"
                    if (m and m.group(1) == "0") or "login_form" in resp.text:
                        return False, None, "Session Facebook tidak aktif / perlu login ulang"
                    return True, None, "Cookie Facebook diterima server"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

            if platform == "tiktok":
                resp = client.get("https://www.tiktok.com/passport/web/account/info/")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("message") == "success" and isinstance(data.get("data"), dict):
                        uname = data["data"].get("username") or data["data"].get("screen_name")
                        return True, f"@{uname}" if uname else None, "Session TikTok aktif"
                    return False, None, "Session TikTok tidak valid / kedaluwarsa"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

            if platform == "youtube":
                resp = client.get("https://www.youtube.com/feed/subscriptions")
                if resp.status_code in (301, 302) and "accounts.google.com" in resp.headers.get("location", "").lower():
                    return False, None, "Cookie YouTube kedaluwarsa (redirect login Google)"
                if resp.status_code == 200:
                    logged_in = '"loggedIn":true' in resp.text or '"LOGGED_IN":true' in resp.text
                    if logged_in:
                        return True, None, "Session YouTube aktif"
                    return False, None, "Cookie YouTube tidak terdeteksi login (tetap bisa crawl publik)"
                return None, None, f"Status tidak pasti (HTTP {resp.status_code})"

    except Exception as exc:
        return None, None, f"Gagal menghubungi server: {exc}"

    return None, None, "Tidak ada probe untuk platform ini"


def check_platform_session(
    platform: str,
    cookie_file: Path | str | None = None,
    base_dir: Path | None = None,
    live: bool = True,
    timeout: float = 12.0,
) -> SessionCheckResult:
    """Cek validitas file cookie & session untuk satu platform."""
    plat = normalize_platform(platform)
    resolved_path = Path(cookie_file) if cookie_file else find_platform_cookie(plat, base_dir=base_dir)

    if not resolved_path or not resolved_path.is_file():
        if plat == "youtube":
            return SessionCheckResult(
                platform=plat,
                cookie_path=None,
                file_exists=False,
                has_required_tokens=True,
                status="optional",
                detail="Cookie belum ada (opsional — YouTube tetap bisa crawl publik)",
            )
        return SessionCheckResult(
            platform=plat,
            cookie_path=None,
            file_exists=False,
            has_required_tokens=False,
            status="missing",
            detail=f"File cookie belum ada (letakkan di cookies/{plat}.txt)",
        )

    try:
        entries = load_cookie_entries(resolved_path)
    except Exception as exc:
        return SessionCheckResult(
            platform=plat,
            cookie_path=resolved_path,
            file_exists=True,
            has_required_tokens=False,
            status="error",
            detail=f"Gagal membaca file cookie: {exc}",
        )

    if not entries:
        return SessionCheckResult(
            platform=plat,
            cookie_path=resolved_path,
            file_exists=True,
            has_required_tokens=False,
            status="invalid",
            detail="File cookie kosong atau format tidak valid",
        )

    has_required, missing, expired, earliest_expiry = _evaluate_local_cookies(plat, entries)

    if not has_required:
        return SessionCheckResult(
            platform=plat,
            cookie_path=resolved_path,
            file_exists=True,
            has_required_tokens=False,
            missing_tokens=missing,
            expired_tokens=expired,
            earliest_expiry=earliest_expiry,
            status="invalid",
            detail=f"Token session wajib tidak ditemukan: {', '.join(missing)}",
        )

    if expired:
        return SessionCheckResult(
            platform=plat,
            cookie_path=resolved_path,
            file_exists=True,
            has_required_tokens=True,
            missing_tokens=missing,
            expired_tokens=expired,
            earliest_expiry=earliest_expiry,
            live_valid=False if live else None,
            status="expired",
            detail=f"Token session kedaluwarsa secara lokal: {', '.join(expired)}",
        )

    if not live:
        return SessionCheckResult(
            platform=plat,
            cookie_path=resolved_path,
            file_exists=True,
            has_required_tokens=True,
            earliest_expiry=earliest_expiry,
            live_valid=None,
            status="valid",
            detail="Cookie lokal lengkap & belum kedaluwarsa",
        )

    live_ok, username, live_detail = _probe_live_session(plat, entries, timeout=timeout)
    if live_ok is True:
        status = "valid"
    elif live_ok is False:
        status = "expired" if plat != "youtube" else "invalid"
    else:
        status = "unknown"

    return SessionCheckResult(
        platform=plat,
        cookie_path=resolved_path,
        file_exists=True,
        has_required_tokens=True,
        earliest_expiry=earliest_expiry,
        live_valid=live_ok,
        username=username,
        status=status,
        detail=live_detail,
    )


def check_all_sessions(
    platforms: tuple[str, ...] | list[str] = ALL_PLATFORMS,
    base_dir: Path | None = None,
    live: bool = True,
    timeout: float = 12.0,
) -> list[SessionCheckResult]:
    """Cek validitas session untuk semua platform yang diminta."""
    return [
        check_platform_session(p, base_dir=base_dir, live=live, timeout=timeout)
        for p in platforms
    ]
