"""design-storage.md §1 (frontmatter schema) and §4 (round-trip serialization)."""

from __future__ import annotations

import re
import unicodedata

import pytest

from redraft.errors import MalformedFrontmatterError
from redraft.ids import format_wikilink
from redraft.nodefile import _atomic_write, dump_node_file, load_node_file
from redraft.schema import FRONTMATTER_KEY_ORDER, LIST_EDGE_TYPES, Node, NodeType


def _write_raw(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _full_node(**overrides):
    kwargs = dict(
        id="Fork GeoTransformer",
        type=NodeType.DECISION,
        title="Fork GeoTransformer",
        body="Body prose in Markdown.",
        status="accepted",
        properties={"effort": "medium"},
        part_of="Point-cloud registration",
        justifies=["Fork beats training from scratch on our data"],
        references=["GeoTransformer paper", "GeoTransformer upstream repo"],
        created="2026-07-08T10:15:00Z",
        updated="2026-07-08T10:15:00Z",
    )
    kwargs.update(overrides)
    return Node(**kwargs)


def test_create_node_writes_expected_frontmatter_and_body(tmp_path):
    node = _full_node()
    text = dump_node_file(node)
    path = tmp_path / f"{node.id}.md"
    _atomic_write(path, text)
    on_disk = path.read_text()
    assert "type: decision" in on_disk
    assert "title: Fork GeoTransformer" in on_disk
    assert "status: accepted" in on_disk
    assert "created: '2026-07-08T10:15:00Z'" in on_disk
    assert "part_of: '[[Point-cloud registration]]'" in on_disk
    assert "- '[[Fork beats training from scratch on our data]]'" in on_disk
    assert on_disk.strip().endswith("Body prose in Markdown.")
    assert on_disk.endswith("\n")


def test_serialization_is_stable_no_op_write_produces_zero_diff(tmp_path):
    node = _full_node()
    first = dump_node_file(node)
    second = dump_node_file(node)
    assert first == second

    path = tmp_path / "n.md"
    _atomic_write(path, first)
    reloaded = load_node_file(path)
    reloaded_text = dump_node_file(reloaded)
    assert reloaded_text == first


def test_writer_never_emits_unquoted_wikilink():
    node = _full_node()
    text = dump_node_file(node)
    for line in text.splitlines():
        if "[[" not in line:
            continue
        assert re.search(r"""['"]\[\[""", line), f"unquoted wikilink in line: {line!r}"


def test_timestamp_fields_round_trip_as_str_not_datetime(tmp_path):
    node = _full_node()
    path = tmp_path / "n.md"
    _atomic_write(path, dump_node_file(node))
    loaded = load_node_file(path)
    assert isinstance(loaded.created, str)
    assert isinstance(loaded.updated, str)
    assert loaded.created == "2026-07-08T10:15:00Z"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", loaded.updated)


def test_externally_authored_unquoted_timestamp_is_normalized_on_load(tmp_path):
    # Unquoted ISO-8601 is YAML 1.1's implicit timestamp resolver territory (design §4.3) —
    # yaml.SafeLoader parses this as a datetime.datetime, not a string, unless guarded.
    raw = (
        "---\n"
        "type: concept\n"
        "title: Hand Edited\n"
        "created: 2026-07-08T10:15:00Z\n"
        "updated: 2026-07-08T10:15:00Z\n"
        "---\n\n"
        "Body.\n"
    )
    path = _write_raw(tmp_path / "Hand Edited.md", raw)
    loaded = load_node_file(path)
    assert loaded.created == "2026-07-08T10:15:00Z"
    assert isinstance(loaded.created, str)
    assert loaded.updated == "2026-07-08T10:15:00Z"


def test_externally_authored_unquoted_date_only_timestamp_is_normalized_on_load(tmp_path):
    # A bare YAML date (no time component) resolves to datetime.date, not datetime.datetime —
    # design §4.3 explicitly calls out both types needing coercion.
    raw = "---\ntype: concept\ntitle: Date Only\ncreated: 2026-07-08\nupdated: 2026-07-08\n---\n\nBody.\n"
    path = _write_raw(tmp_path / "Date Only.md", raw)
    loaded = load_node_file(path)
    assert loaded.created == "2026-07-08T00:00:00Z"


def test_unicode_title_nfc_round_trip(tmp_path):
    nfd_title = unicodedata.normalize("NFD", "Caféétude")  # decomposed form on disk
    assert nfd_title != unicodedata.normalize("NFC", nfd_title)  # sanity: the fixture is genuinely NFD
    node = _full_node(id="Cafe", title=nfd_title)
    path = tmp_path / "Cafe.md"
    _atomic_write(path, dump_node_file(node))
    loaded = load_node_file(path)
    assert loaded.title == unicodedata.normalize("NFC", nfd_title)


def test_unknown_frontmatter_keys_preserved_through_load_modify_dump(tmp_path):
    raw = (
        "---\n"
        "type: concept\n"
        "title: Has Aliases\n"
        "created: '2026-07-08T10:15:00Z'\n"
        "updated: '2026-07-08T10:15:00Z'\n"
        "aliases:\n- Alt Name\ncssclasses: my-class\n"
        "---\n\n"
        "Body.\n"
    )
    path = _write_raw(tmp_path / "Has Aliases.md", raw)
    loaded = load_node_file(path)
    assert loaded.extra == {"aliases": ["Alt Name"], "cssclasses": "my-class"}

    modified = loaded.model_copy(update={"body": "New body."})
    text = dump_node_file(modified)
    assert "aliases:" in text
    assert "- Alt Name" in text
    assert "cssclasses: my-class" in text
    reloaded = load_node_file(_write_raw(tmp_path / "Has Aliases 2.md", text))
    assert reloaded.extra == {"aliases": ["Alt Name"], "cssclasses": "my-class"}


def test_key_order_is_canonical_and_deterministic():
    node = _full_node(
        justifies=["a"], supersedes=["b"], addresses=["c"], depends_on=["d"],
        contradicts=["e"], references=["f"], derived_from=["g"], relates_to=["h"],
        extra={"zeta": 1, "aliases": ["x"]},
    )
    text = dump_node_file(node)
    fm = text.split("---")[1]
    keys_in_order = [line.split(":", 1)[0] for line in fm.splitlines() if line and not line.startswith(("-", " "))]
    expected_known = [k for k in FRONTMATTER_KEY_ORDER if k in keys_in_order]
    expected_extra = sorted(k for k in keys_in_order if k not in FRONTMATTER_KEY_ORDER)
    assert keys_in_order == expected_known + expected_extra
    # run it twice more to confirm determinism, not a lucky dict-ordering accident
    assert dump_node_file(node) == text == dump_node_file(node)


def test_body_with_horizontal_rule_and_table_separator_survives_round_trip(tmp_path):
    body = "Intro.\n\n---\n\nMore text.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    node = _full_node(body=body)
    path = tmp_path / "n.md"
    _atomic_write(path, dump_node_file(node))
    loaded = load_node_file(path)
    assert loaded.body == body.strip()


def test_part_of_as_yaml_list_is_malformed(tmp_path):
    raw = (
        "---\n"
        "type: concept\n"
        "title: Bad Part Of\n"
        "created: '2026-07-08T10:15:00Z'\n"
        "updated: '2026-07-08T10:15:00Z'\n"
        "part_of: [[Point-cloud registration]]\n"  # unquoted -> [['Point-cloud registration']]
        "---\n\nBody.\n"
    )
    path = _write_raw(tmp_path / "Bad Part Of.md", raw)
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(path)


@pytest.mark.parametrize("missing_key", ["type", "title", "created", "updated"])
def test_missing_required_key_is_malformed(tmp_path, missing_key):
    fields = {
        "type": "type: concept",
        "title": "title: Missing Key Test",
        "created": "created: '2026-07-08T10:15:00Z'",
        "updated": "updated: '2026-07-08T10:15:00Z'",
    }
    del fields[missing_key]
    raw = "---\n" + "\n".join(fields.values()) + "\n---\n\nBody.\n"
    path = _write_raw(tmp_path / "Missing.md", raw)
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(path)


def test_unknown_type_value_is_malformed(tmp_path):
    raw = (
        "---\ntype: not_a_real_type\ntitle: Bad Type\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n---\n\nBody.\n"
    )
    path = _write_raw(tmp_path / "Bad Type.md", raw)
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(path)


def test_status_invalid_for_type_is_malformed(tmp_path):
    # case 1: status present for a type that must not have one
    raw1 = (
        "---\ntype: concept\ntitle: Concept With Status\nstatus: proposed\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n---\n\nBody.\n"
    )
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(_write_raw(tmp_path / "Concept With Status.md", raw1))

    # case 2: status absent for a type that requires one
    raw2 = (
        "---\ntype: decision\ntitle: Decision No Status\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n---\n\nBody.\n"
    )
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(_write_raw(tmp_path / "Decision No Status.md", raw2))

    # case 3: status present but not a valid value for the type's enum
    raw3 = (
        "---\ntype: decision\ntitle: Decision Bad Status\nstatus: maybe\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n---\n\nBody.\n"
    )
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(_write_raw(tmp_path / "Decision Bad Status.md", raw3))


def test_edge_list_tolerates_bare_scalar_on_read(tmp_path):
    raw = (
        "---\ntype: decision\ntitle: Scalar Edge\nstatus: proposed\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n"
        "references: '[[Solo Reference]]'\n---\n\nBody.\n"
    )
    path = _write_raw(tmp_path / "Scalar Edge.md", raw)
    loaded = load_node_file(path)
    assert loaded.references == ["Solo Reference"]


def test_edge_list_of_lists_is_malformed(tmp_path):
    raw = (
        "---\ntype: decision\ntitle: Bad Edge List\nstatus: proposed\n"
        "created: '2026-07-08T10:15:00Z'\nupdated: '2026-07-08T10:15:00Z'\n"
        "references: [[Unquoted Wikilink]]\n---\n\nBody.\n"  # same unquoted-[[ footgun as §4.2, on a list key
    )
    path = _write_raw(tmp_path / "Bad Edge List.md", raw)
    with pytest.raises(MalformedFrontmatterError):
        load_node_file(path)


def test_dump_omits_empty_optional_keys():
    node = _full_node(status=None, type=NodeType.CONCEPT, properties={}, part_of=None,
                       justifies=[], references=[])
    text = dump_node_file(node)
    assert "status:" not in text
    assert "properties:" not in text
    assert "part_of:" not in text
    assert "justifies:" not in text
    assert "references:" not in text


def test_format_wikilink_matches_writer_output():
    node = _full_node()
    text = dump_node_file(node)
    assert f"part_of: '{format_wikilink(node.part_of)}'" in text
