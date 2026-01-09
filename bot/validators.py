from __future__ import annotations

def parse_int(text: str) -> int | None:
    try:
        text = (text or "").strip().replace(",", "")
        return int(text)
    except Exception:
        return None

def safe_str(s: str | None, max_len: int = 128) -> str:
    s = (s or "").strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s
