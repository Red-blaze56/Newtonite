"""Lists stay correct as data grows: server-side filters, search and keyset pagination."""

from datetime import UTC, datetime, timedelta


def test_pagination_walks_every_item_exactly_once_even_while_items_are_added(world):
    lead = world.client("lead")
    created = {world.create_item(title=f"Item {i}")["id"] for i in range(23)}

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 5, **({"cursor": cursor} if cursor else {})}
        page = lead.get("/items", params=params).json()
        seen += [i["id"] for i in page["items"]]
        pages += 1
        if pages == 2:
            world.create_item(title="Arrived mid-browse")  # newest: sorts before the cursor, so not repeated
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert len(seen) == len(set(seen)) == 23
    assert set(seen) == created
    assert pages == 5


def test_priority_sort_and_unassigned_urgent_view(world):
    for p in ("low", "critical", "high", "medium", "critical"):
        world.create_item(priority=p)
    page = world.client("lead").get("/items", params={"view": "unassigned_urgent"}).json()["items"]
    assert [i["priority"] for i in page] == ["critical", "critical", "high"]


def test_prefix_search(world):
    world.create_item(title="Refund failed for order 123")
    world.create_item(title="Rotate credentials")
    found = world.client("lead").get("/items", params={"q": "refu fail"}).json()["items"]
    assert [i["title"] for i in found] == ["Refund failed for order 123"]


def test_overdue_view_and_dashboard_counts_agree(world):
    lead = world.client("lead")
    world.create_item(due_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    world.create_item(due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    overdue = lead.get("/items", params={"view": "overdue"}).json()["items"]
    assert len(overdue) == 1 and overdue[0]["overdue"] is True
    counts = lead.get("/dashboard/counts").json()
    assert counts["overdue"] == 1
    assert counts["unassigned_urgent"] == 2  # both are 'high' and unowned


def test_garbage_cursor_is_a_clean_422(world):
    r = world.client("lead").get("/items", params={"cursor": "not-a-cursor"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor"
