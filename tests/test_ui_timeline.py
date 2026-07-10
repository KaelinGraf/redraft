"""GET /api/timeline (Timeline tab) -- organizing-protocol.md's Planning dates convention
(`properties.start`/`properties.due`): every node with a scheduled start/due (any type) plus
every dateless `milestone` (the unscheduled tray), with `part_of`/`depends_on` populated, and
robust to a malformed or off-spec date value (never 500s)."""
from __future__ import annotations


async def test_timeline_returns_scheduled_and_unscheduled_items(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Component A"})
    r = await ui_client.post(
        "/api/nodes",
        json={
            "type": "milestone",
            "title": "Phase One",
            "part_of": "Component A",
            "properties": {"start": "2026-01-01", "due": "2026-01-15"},
        },
    )
    assert r.status_code == 200
    await ui_client.post(
        "/api/nodes",
        json={"type": "milestone", "title": "Ship Beta", "properties": {"due": "2026-02-01"}},
    )
    await ui_client.post("/api/nodes", json={"type": "milestone", "title": "Future Work"})
    await ui_client.post(
        "/api/nodes",
        json={
            "type": "decision",
            "title": "Pick Vendor",
            "status": "accepted",
            "properties": {"due": "2026-03-01"},
        },
    )
    await ui_client.post(
        "/api/nodes",
        json={"type": "milestone", "title": "Kickoff", "properties": {"start": "2026-01-20"}},
    )
    r = await ui_client.post(
        "/api/nodes",
        json={"type": "milestone", "title": "Bad Date Milestone", "properties": {"due": "not-a-real-date"}},
    )
    assert r.status_code == 200  # malformed/non-ISO string is still a legal properties value at write time
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Untracked Concept"})
    r = await ui_client.post("/api/edges", json={"src": "Ship Beta", "dst": "Phase One", "type": "depends_on"})
    assert r.status_code == 200

    r = await ui_client.get("/api/timeline")
    assert r.status_code == 200  # the bad date string must not 500 the endpoint
    items = {item["id"]: item for item in r.json()["items"]}

    # exactly the scheduled items + the one unscheduled milestone; the dateless concepts
    # (Component A, Untracked Concept) are excluded entirely -- neither is a milestone.
    assert set(items) == {
        "Phase One", "Ship Beta", "Future Work", "Pick Vendor", "Bad Date Milestone", "Kickoff",
    }

    phase_one = items["Phase One"]
    assert phase_one["type"] == "milestone"
    assert phase_one["start"] == "2026-01-01"
    assert phase_one["due"] == "2026-01-15"
    assert phase_one["part_of"] == "Component A"  # swimlane
    assert phase_one["depends_on"] == []

    ship_beta = items["Ship Beta"]
    assert ship_beta["start"] is None
    assert ship_beta["due"] == "2026-02-01"  # due-only = a point milestone
    assert ship_beta["part_of"] is None
    assert ship_beta["depends_on"] == ["Phase One"]  # dependent -> prerequisite

    future_work = items["Future Work"]
    assert future_work["start"] is None
    assert future_work["due"] is None  # neither set -> the unscheduled tray
    assert future_work["part_of"] is None  # no swimlane -> "ungrouped", not an error
    assert future_work["status"] == "planned"  # milestone's create-time default status
    assert future_work["depends_on"] == []

    pick_vendor = items["Pick Vendor"]
    assert pick_vendor["type"] == "decision"  # non-milestone types are included when scheduled
    assert pick_vendor["due"] == "2026-03-01"
    assert pick_vendor["start"] is None

    bad_date = items["Bad Date Milestone"]
    assert bad_date["due"] == "not-a-real-date"  # passed through verbatim, never ISO-validated
    assert bad_date["start"] is None

    kickoff = items["Kickoff"]
    assert kickoff["start"] == "2026-01-20"  # start-only, the mirror image of Ship Beta's due-only
    assert kickoff["due"] is None


async def test_timeline_non_string_date_value_treated_as_absent_not_500(ui_client):
    """Off-spec input beyond a merely-malformed string: properties.start holding a JSON
    number (not a string at all) must not crash TimelineItem construction -- the value is
    treated as unset, so a milestone carrying only this lands in the unscheduled tray rather
    than raising a pydantic ValidationError that would 500 the whole endpoint."""
    r = await ui_client.post(
        "/api/nodes",
        json={"type": "milestone", "title": "Numeric Start Milestone", "properties": {"start": 20260101}},
    )
    assert r.status_code == 200
    await ui_client.post(
        "/api/nodes",
        json={"type": "concept", "title": "Numeric Start Concept", "properties": {"start": 20260101}},
    )

    r = await ui_client.get("/api/timeline")
    assert r.status_code == 200
    items = {item["id"]: item for item in r.json()["items"]}

    # milestone: non-string start counts as absent -> neither set -> still surfaces (unscheduled tray)
    assert items["Numeric Start Milestone"]["start"] is None
    assert items["Numeric Start Milestone"]["due"] is None
    # non-milestone with only an off-spec value: neither counts as set -> excluded entirely
    assert "Numeric Start Concept" not in items


async def test_timeline_empty_graph_returns_no_items(ui_client):
    r = await ui_client.get("/api/timeline")
    assert r.status_code == 200
    assert r.json() == {"items": []}
