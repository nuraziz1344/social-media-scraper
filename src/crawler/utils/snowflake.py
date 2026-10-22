"""Utilitas decoding timestamp berbasis Snowflake ID (Instagram & Threads)."""
from datetime import datetime, timezone

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
INSTAGRAM_EPOCH_MS = 1314220021721  # 24 Agustus 2011


def decode_instagram_shortcode(code: str) -> datetime | None:
    """Decode shortcode Instagram / Threads menjadi UTC datetime.
    
    Shortcode Instagram/Threads merupakan representasi base64url dari media ID (64-bit int).
    Formula:
        timestamp_ms = (media_id >> 23) + INSTAGRAM_EPOCH_MS
    """
    if not code or not isinstance(code, str):
        return None

    clean_code = code.strip().strip("/")
    if "/" in clean_code:
        clean_code = clean_code.split("/")[-1]

    media_id = 0
    for char in clean_code:
        idx = ALPHABET.find(char)
        if idx == -1:
            return None
        media_id = (media_id * 64) + idx

    ts_ms = (media_id >> 23) + INSTAGRAM_EPOCH_MS
    # Validasi batas kewajaran (antara epoch Instagram 2011 hingga tahun 2100)
    if ts_ms < INSTAGRAM_EPOCH_MS or ts_ms > 4102444800000:
        return None

    try:
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    except Exception:
        return None
