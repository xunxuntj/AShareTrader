"""Outbound research link resolver for FullScanner (https://fs.3x90.com/).

Pure outbound link helper: no network calls, no click tracking, no private parameters.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urljoin, urlparse

FULLSCANNER_HOME_URL = "https://fs.3x90.com/"
FULLSCANNER_ALLOWED_HOST = "fs.3x90.com"
DEFAULT_LINK_LABEL = "打开 FullScanner 研究首页 ↗"
VERIFIED_ETF_LINK_LABEL = "研究这只 ETF ↗"

# Initially empty mapping: ETF symbol -> verified FullScanner relative path.
# Add entries here ONLY after a dedicated ETF research page is live and verified on fs.3x90.com,
# e.g. "512890": "/etf/512890".
VERIFIED_ETF_PATHS: dict[str, str] = {}


def _sanitize_verified_target(raw_target: str) -> str | None:
    """Validate that raw_target resolves to a clean HTTPS URL on fs.3x90.com with no query/fragment."""
    candidate = raw_target.strip()
    if not candidate or any(ch.isspace() for ch in candidate):
        return None
    if candidate.startswith("//"):
        return None

    if candidate.startswith("/"):
        resolved = urljoin(FULLSCANNER_HOME_URL, candidate)
    else:
        resolved = candidate

    parsed = urlparse(resolved)
    if parsed.scheme != "https":
        return None
    if parsed.hostname != FULLSCANNER_ALLOWED_HOST or parsed.netloc != FULLSCANNER_ALLOWED_HOST:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    # Disallow query strings or fragments so private parameters can never be attached
    if parsed.query or parsed.fragment:
        return None
    if not parsed.path or parsed.path == "/":
        return None

    return f"https://{FULLSCANNER_ALLOWED_HOST}{parsed.path}"


def resolve_fullscanner_link(
    symbol: str,
    verified_paths: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Return (url, label) for a strategy ETF symbol.

    Falls back to (FULLSCANNER_HOME_URL, DEFAULT_LINK_LABEL) whenever no verified mapping exists
    or the mapped target fails strict fs.3x90.com HTTPS validation.
    """
    mapping = VERIFIED_ETF_PATHS if verified_paths is None else verified_paths
    clean_sym = str(symbol or "").strip()
    if not clean_sym:
        return FULLSCANNER_HOME_URL, DEFAULT_LINK_LABEL

    raw_target = mapping.get(clean_sym)
    if not raw_target:
        return FULLSCANNER_HOME_URL, DEFAULT_LINK_LABEL

    safe_url = _sanitize_verified_target(raw_target)
    if safe_url is None:
        return FULLSCANNER_HOME_URL, DEFAULT_LINK_LABEL

    return safe_url, VERIFIED_ETF_LINK_LABEL
