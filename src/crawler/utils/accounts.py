"""Utilitas parsing, normalisasi, dan loader target akun media sosial (username / URL)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PLATFORM_ALIASES = {
    "yt": "youtube",
    "youtube": "youtube",
    "x": "x",
    "twitter": "x",
    "ig": "instagram",
    "instagram": "instagram",
    "tt": "tiktok",
    "tiktok": "tiktok",
    "fb": "facebook",
    "facebook": "facebook",
    "th": "threads",
    "threads": "threads",
}

DOMAIN_TO_PLATFORM = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "x.com": "x",
    "twitter.com": "x",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "threads.net": "threads",
    "threads.com": "threads",
}


@dataclass(frozen=True)
class AccountTarget:
    """Representasi terstruktur akun target yang sudah dinormalisasi."""

    platform: str
    identifier: str
    url: str
    kind: str
    slug: str
    raw_input: str


def normalize_platform(platform: str | None) -> str:
    """Normalisasi nama platform ke kunci kanonikal."""
    if not platform:
        return ""
    p = platform.strip().lower()
    return PLATFORM_ALIASES.get(p, p)


def _sanitize_slug(value: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip().lstrip("@"))
    cleaned = cleaned.strip("_.")
    return (cleaned or "account")[:max_len]


def _extract_host(raw: str) -> str:
    text = raw.strip()
    if not re.match(r"^https?://", text, re.IGNORECASE):
        if any(domain in text.lower() for domain in DOMAIN_TO_PLATFORM):
            text = f"https://{text}"
        else:
            return ""
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower().split(":")[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def infer_platform_from_input(raw: str) -> str | None:
    """Deteksi platform secara otomatis dari URL akun, atau None jika bukan URL yang dikenal."""
    host = _extract_host(raw)
    if not host:
        return None
    for domain, plat in DOMAIN_TO_PLATFORM.items():
        if host == domain or host.endswith(f".{domain}"):
            return plat
    return None


def parse_account_target(raw: str, default_platform: str | None = None) -> AccountTarget:
    """Parse username atau URL akun menjadi AccountTarget terstandarisasi."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Target akun tidak boleh kosong.")

    inferred = infer_platform_from_input(text)
    platform = normalize_platform(default_platform) if default_platform else (inferred or "")
    if inferred and default_platform and normalize_platform(default_platform) != inferred:
        raise ValueError(
            f"URL '{text}' terdeteksi sebagai platform '{inferred}', "
            f"tidak cocok dengan --platform '{default_platform}'."
        )
    if not platform:
        raise ValueError(
            f"Tidak dapat menentukan platform untuk '{text}'. "
            "Sertakan --platform atau gunakan URL akun yang lengkap."
        )

    is_url = bool(inferred) or bool(re.match(r"^https?://", text, re.IGNORECASE))
    if is_url and not re.match(r"^https?://", text, re.IGNORECASE):
        text = f"https://{text}"

    if platform == "youtube":
        return _parse_youtube_account(text, is_url, raw)
    if platform == "x":
        return _parse_x_account(text, is_url, raw)
    if platform == "instagram":
        return _parse_instagram_account(text, is_url, raw)
    if platform == "tiktok":
        return _parse_tiktok_account(text, is_url, raw)
    if platform == "facebook":
        return _parse_facebook_account(text, is_url, raw)
    if platform == "threads":
        return _parse_threads_account(text, is_url, raw)

    raise ValueError(f"Platform tidak didukung: '{platform}'.")


def _parse_youtube_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "channel":
            cid = parts[1]
            return AccountTarget(
                platform="youtube",
                identifier=cid,
                url=f"https://www.youtube.com/channel/{cid}",
                kind="channel_id",
                slug=_sanitize_slug(cid),
                raw_input=raw,
            )
        if parts and parts[0].startswith("@"):
            handle = parts[0].lstrip("@")
            return AccountTarget(
                platform="youtube",
                identifier=handle,
                url=f"https://www.youtube.com/@{handle}",
                kind="username",
                slug=_sanitize_slug(handle),
                raw_input=raw,
            )
        if len(parts) >= 2 and parts[0] in ("c", "user"):
            name = parts[1]
            return AccountTarget(
                platform="youtube",
                identifier=name,
                url=f"https://www.youtube.com/{parts[0]}/{name}",
                kind="channel_url",
                slug=_sanitize_slug(name),
                raw_input=raw,
            )
        if parts:
            handle = parts[0].lstrip("@")
            return AccountTarget(
                platform="youtube",
                identifier=handle,
                url=f"https://www.youtube.com/@{handle}",
                kind="username",
                slug=_sanitize_slug(handle),
                raw_input=raw,
            )
        raise ValueError(f"URL channel YouTube tidak valid: '{raw}'.")

    clean = text.strip().lstrip("@")
    if clean.startswith("UC") and len(clean) >= 20 and "/" not in clean:
        return AccountTarget(
            platform="youtube",
            identifier=clean,
            url=f"https://www.youtube.com/channel/{clean}",
            kind="channel_id",
            slug=_sanitize_slug(clean),
            raw_input=raw,
        )
    return AccountTarget(
        platform="youtube",
        identifier=clean,
        url=f"https://www.youtube.com/@{clean}",
        kind="username",
        slug=_sanitize_slug(clean),
        raw_input=raw,
    )


def _parse_x_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"URL akun X tidak valid: '{raw}'.")
        handle = parts[0].lstrip("@")
    else:
        handle = text.strip().lstrip("@")

    return AccountTarget(
        platform="x",
        identifier=handle,
        url=f"https://x.com/{handle}",
        kind="username",
        slug=_sanitize_slug(handle),
        raw_input=raw,
    )


def _parse_instagram_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"URL akun Instagram tidak valid: '{raw}'.")
        handle = parts[0].lstrip("@")
    else:
        handle = text.strip().lstrip("@")

    return AccountTarget(
        platform="instagram",
        identifier=handle,
        url=f"https://www.instagram.com/{handle}/",
        kind="username",
        slug=_sanitize_slug(handle),
        raw_input=raw,
    )


def _parse_tiktok_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"URL akun TikTok tidak valid: '{raw}'.")
        handle = parts[0].lstrip("@")
    else:
        handle = text.strip().lstrip("@")

    return AccountTarget(
        platform="tiktok",
        identifier=handle,
        url=f"https://www.tiktok.com/@{handle}",
        kind="username",
        slug=_sanitize_slug(handle),
        raw_input=raw,
    )


def _parse_facebook_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        qs = parse_qs(parsed.query)

        if parts and parts[0] == "profile.php":
            fb_id = (qs.get("id") or [""])[0].strip()
            if not fb_id:
                raise ValueError(f"URL profile.php Facebook harus memiliki parameter id: '{raw}'.")
            return AccountTarget(
                platform="facebook",
                identifier=fb_id,
                url=f"https://www.facebook.com/profile.php?id={fb_id}",
                kind="profile_id",
                slug=_sanitize_slug(f"fb_{fb_id}"),
                raw_input=raw,
            )

        if len(parts) >= 2 and parts[0] == "p":
            page_slug = parts[1]
            return AccountTarget(
                platform="facebook",
                identifier=page_slug,
                url=f"https://www.facebook.com/p/{page_slug}/",
                kind="page_p",
                slug=_sanitize_slug(page_slug),
                raw_input=raw,
            )

        if parts:
            handle = parts[0].lstrip("@")
            return AccountTarget(
                platform="facebook",
                identifier=handle,
                url=f"https://www.facebook.com/{handle}",
                kind="username",
                slug=_sanitize_slug(handle),
                raw_input=raw,
            )
        raise ValueError(f"URL akun Facebook tidak valid: '{raw}'.")

    clean = text.strip().lstrip("@")
    if clean.isdigit():
        return AccountTarget(
            platform="facebook",
            identifier=clean,
            url=f"https://www.facebook.com/profile.php?id={clean}",
            kind="profile_id",
            slug=_sanitize_slug(f"fb_{clean}"),
            raw_input=raw,
        )
    return AccountTarget(
        platform="facebook",
        identifier=clean,
        url=f"https://www.facebook.com/{clean}",
        kind="username",
        slug=_sanitize_slug(clean),
        raw_input=raw,
    )


def _parse_threads_account(text: str, is_url: bool, raw: str) -> AccountTarget:
    if is_url:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"URL akun Threads tidak valid: '{raw}'.")
        handle = parts[0].lstrip("@")
    else:
        handle = text.strip().lstrip("@")

    return AccountTarget(
        platform="threads",
        identifier=handle,
        url=f"https://www.threads.com/@{handle}",
        kind="username",
        slug=_sanitize_slug(handle),
        raw_input=raw,
    )


def load_accounts_file(file_path: Path | str) -> dict[str, list[AccountTarget]]:
    """Muat daftar akun dari file JSON.

    Mendukung dua format JSON:
    1. Object keyed by platform:
       {"instagram": ["user1", "https://..."], "x": ["user2"]}
    2. Array berisi string URL atau object {platform, account/url}:
       [{"platform": "instagram", "account": "user1"}, "https://x.com/user2"]
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File akun tidak ditemukan: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    grouped: dict[str, list[AccountTarget]] = {}
    seen_urls: set[tuple[str, str]] = set()

    def _add(raw_val: str, plat: str | None = None):
        val = (raw_val or "").strip()
        if not val or val in ("-", "–"):
            return
        target = parse_account_target(val, default_platform=plat)
        key = (target.platform, target.url.lower())
        if key not in seen_urls:
            seen_urls.add(key)
            grouped.setdefault(target.platform, []).append(target)

    if isinstance(data, dict):
        for raw_plat, entries in data.items():
            plat = normalize_platform(raw_plat)
            if isinstance(entries, str):
                _add(entries, plat)
            elif isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, str):
                        _add(entry, plat)
                    elif isinstance(entry, dict):
                        val = entry.get("url") or entry.get("account") or entry.get("username") or ""
                        _add(str(val), entry.get("platform") or plat)
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, str):
                _add(entry, None)
            elif isinstance(entry, dict):
                val = entry.get("url") or entry.get("account") or entry.get("username") or ""
                _add(str(val), entry.get("platform"))
    else:
        raise ValueError("Format JSON account-file harus berupa object ({platform: [...]}) atau array ([...]).")

    return grouped
