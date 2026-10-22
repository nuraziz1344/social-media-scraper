"""Utilitas manajemen dan parser cookie.

Mendukung pembacaan file .txt format Netscape / Mozilla Cookie File
(hasil export ekstensi browser seperti Get cookies.txt locally, CookieJar, Cookie-Editor)
dan mengonversinya ke format yang dibutuhkan Playwright, twscrape, instagrapi, dll.
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    ):
        self.domain = domain
        self.include_subdomains = include_subdomains
        self.path = path
        self.secure = secure
        self.expires = expires
        self.name = name
        self.value = value

    def to_playwright_dict(self) -> dict[str, Any]:
        cookie_dict: dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": False,
            "sameSite": "Lax",
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


def cookies_to_dict(entries: list[CookieEntry]) -> dict[str, str]:
    """Konversi daftar cookie ke mapping key-value {name: value}."""
    return {entry.name: entry.value for entry in entries}


def to_playwright_storage_state(
    file_path: Path | str, output_path: Path | str | None = None
) -> dict[str, Any]:
    """Konversi file cookies.txt Netscape ke format storage_state Playwright."""
    entries = parse_netscape_cookies(file_path)
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


def extract_twitter_tokens(file_path: Path | str) -> dict[str, str]:
    """Mengekstrak token esensial Twitter/X (auth_token dan ct0)."""
    entries = parse_netscape_cookies(file_path)
    c_dict = cookies_to_dict(entries)
    auth_token = c_dict.get("auth_token", "")
    ct0 = c_dict.get("ct0", "")
    return {"auth_token": auth_token, "ct0": ct0}


def extract_instagram_tokens(file_path: Path | str) -> dict[str, str]:
    """Mengekstrak sessionid dan csrftoken Instagram."""
    entries = parse_netscape_cookies(file_path)
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
