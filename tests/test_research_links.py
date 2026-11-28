"""Tests for FullScanner outbound research link resolver."""

from urllib.parse import urlparse

import pytest

from ashare_trader.research_links import (
    DEFAULT_LINK_LABEL,
    FULLSCANNER_ALLOWED_HOST,
    FULLSCANNER_HOME_URL,
    VERIFIED_ETF_LINK_LABEL,
    VERIFIED_ETF_PATHS,
    resolve_fullscanner_link,
)


def test_default_mapping_is_empty_and_falls_back_to_home():
    assert VERIFIED_ETF_PATHS == {}
    url, label = resolve_fullscanner_link("512890")
    assert url == FULLSCANNER_HOME_URL
    assert label == DEFAULT_LINK_LABEL


def test_unknown_or_empty_symbol_falls_back_to_home():
    custom_map = {"512890": "/etf/512890"}
    for sym in ["510300", "", "   ", "999999"]:
        url, label = resolve_fullscanner_link(sym, verified_paths=custom_map)
        assert url == FULLSCANNER_HOME_URL
        assert label == DEFAULT_LINK_LABEL


def test_verified_relative_and_same_host_https_paths_pair_with_etf_label():
    custom_map = {
        "512890": "/etf/512890",
        "518880": "https://fs.3x90.com/etf/518880",
    }
    url1, label1 = resolve_fullscanner_link("512890", verified_paths=custom_map)
    assert url1 == "https://fs.3x90.com/etf/512890"
    assert label1 == VERIFIED_ETF_LINK_LABEL

    url2, label2 = resolve_fullscanner_link("518880", verified_paths=custom_map)
    assert url2 == "https://fs.3x90.com/etf/518880"
    assert label2 == VERIFIED_ETF_LINK_LABEL


@pytest.mark.parametrize(
    "bad_target",
    [
        "https://evil.com/etf/512890",
        "http://fs.3x90.com/etf/512890",
        "//evil.com/etf/512890",
        "javascript:alert(1)",
        "https://fs.3x90.com.evil.com/etf/512890",
        "https://user@fs.3x90.com/etf/512890",
        "https://fs.3x90.com:8443/etf/512890",
        "/etf/512890?user_id=admin&token=xyz",
        "https://fs.3x90.com/etf/512890?shares=1000",
        "/etf/512890#private",
        "/",
        "   ",
    ],
)
def test_invalid_or_external_targets_fall_back_safely(bad_target: str):
    url, label = resolve_fullscanner_link("512890", verified_paths={"512890": bad_target})
    assert url == FULLSCANNER_HOME_URL
    assert label == DEFAULT_LINK_LABEL


def test_generated_links_never_contain_private_parameters():
    cases = [
        resolve_fullscanner_link("512890"),
        resolve_fullscanner_link("512890", verified_paths={"512890": "/etf/512890"}),
    ]
    for url, _ in cases:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == FULLSCANNER_ALLOWED_HOST
        assert parsed.query == ""
        assert parsed.fragment == ""
        for forbidden in ["user", "token", "shares", "price", "weight", "action", "strategy", "cash"]:
            assert forbidden not in url.lower()
