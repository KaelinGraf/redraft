"""design-storage.md §2 (title-to-id sanitization) and §3 (wikilink parsing)."""

from __future__ import annotations

import pytest

from redraft.errors import MalformedFrontmatterError
from redraft.ids import format_wikilink, parse_wikilink, sanitize_title_to_id


def test_title_to_id_strips_illegal_filesystem_characters():
    assert sanitize_title_to_id('A<B>C:D"E/F\\G|H?I*J') == "A B C D E F G H I J"


def test_title_to_id_collapses_whitespace_and_strips_trailing_dot_space():
    assert sanitize_title_to_id("  Multiple   spaces   here.  ") == "Multiple spaces here"


def test_title_to_id_length_cap_truncates_at_utf8_safe_boundary():
    # 4-byte-each emoji: a naive char-count truncation would blow the 197-byte budget.
    title = "\U0001f600" * 100  # 100 * 4 = 400 bytes
    result = sanitize_title_to_id(title)
    encoded = result.encode("utf-8")
    assert len(encoded) <= 200 - len(".md".encode())
    # must decode cleanly (no partial multibyte sequence left dangling)
    encoded.decode("utf-8")
    assert result == "\U0001f600" * (len(encoded) // 4)


def test_title_to_id_rejects_all_illegal_title():
    with pytest.raises(ValueError):
        sanitize_title_to_id('<>:"/\\|?*')
    with pytest.raises(ValueError):
        sanitize_title_to_id("   ")
    with pytest.raises(ValueError):
        sanitize_title_to_id("")


def test_title_to_id_rejects_title_that_is_empty_only_after_truncation():
    # Design defect fix (design-storage.md §2 lines 74-78): 500 dots then one real char is
    # non-empty pre-truncation (rstrip only strips a *trailing* run), but truncating to the
    # 197-byte budget lands entirely inside the all-dot prefix, which then rstrips to "".
    # The unpatched design pseudocode returns "" silently instead of raising.
    pathological = "." * 500 + "A"
    with pytest.raises(ValueError):
        sanitize_title_to_id(pathological)


def test_wikilink_parse_strips_heading_and_alias():
    assert parse_wikilink("[[Some Node]]") == "Some Node"
    assert parse_wikilink("[[Some Node#Heading]]") == "Some Node"
    assert parse_wikilink("[[Some Node|Alias text]]") == "Some Node"
    assert parse_wikilink("[[Some Node#Heading|Alias text]]") == "Some Node"


def test_wikilink_parse_rejects_non_wikilink_string():
    for bad in ["not a wikilink", "[[A]] extra text", "[[A", "A]]", "[[]]", "[[A]][[B]]"]:
        with pytest.raises(MalformedFrontmatterError):
            parse_wikilink(bad)


def test_format_wikilink_round_trips_with_parse():
    assert parse_wikilink(format_wikilink("Some Node")) == "Some Node"
