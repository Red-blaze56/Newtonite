"""Authorization is enforced by the API itself, per team and per item, not by hiding buttons."""

import pytest


def test_requests_without_a_token_are_rejected(world):
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/items")
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/items/{id}", None),
        ("get", "/items/{id}/activity", None),
        ("patch", "/items/{id}", {"expected_version": 1, "title": "x"}),
        ("post", "/items/{id}/claim", None),
        ("post", "/items/{id}/transition", {"to": "in_progress", "expected_version": 1}),
        ("post", "/items/{id}/comments", {"body": "hello"}),
    ],
)
def test_other_teams_items_look_like_they_do_not_exist(world, method, path, body):
    """404, not 403: answering 403 would confirm the item exists."""
    item = world.create_item()
    outsider = world.client("outsider")
    kwargs = {"json": body} if body is not None else {}
    r = getattr(outsider, method)(path.format(id=item["id"]), **kwargs)
    assert r.status_code == 404


def test_lists_never_include_other_teams_items(world):
    world.create_item(title="Payments secret")
    outsider = world.client("outsider")
    assert outsider.get("/items").json()["items"] == []
    assert outsider.get("/items", params={"q": "secret"}).json()["items"] == []
    assert outsider.get("/items", params={"team_id": world.payments}).status_code == 403


def test_viewer_can_read_and_comment_but_not_change_anything(world):
    item = world.create_item()
    viewer = world.client("viewer")
    assert viewer.get(f"/items/{item['id']}").status_code == 200
    assert viewer.post(f"/items/{item['id']}/comments", json={"body": "Noted"}).status_code == 201
    assert viewer.post(f"/items/{item['id']}/claim").status_code == 403
    assert viewer.patch(f"/items/{item['id']}", json={"expected_version": item["version"], "title": "x"}).status_code == 403
    assert viewer.post("/items", json={"team_id": world.payments, "type": "task", "title": "x"}).status_code == 403


def test_member_cannot_move_an_item_someone_else_owns(world):
    item = world.create_item()
    v = world.client("member").post(f"/items/{item['id']}/claim").json()["version"]
    r = world.client("member2").post(f"/items/{item['id']}/transition", json={"to": "in_progress", "expected_version": v})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_item_owner"


def test_only_leads_assign_and_only_to_people_who_can_do_the_work(world):
    item = world.create_item()
    member, lead = world.client("member"), world.client("lead")
    body = {"assignee_id": world.users["member2"], "expected_version": item["version"]}

    assert member.post(f"/items/{item['id']}/assign", json=body).status_code == 403

    to_viewer = lead.post(f"/items/{item['id']}/assign", json={**body, "assignee_id": world.users["viewer"]})
    assert to_viewer.status_code == 422 and to_viewer.json()["error"]["code"] == "invalid_assignee"

    to_outsider = lead.post(f"/items/{item['id']}/assign", json={**body, "assignee_id": world.users["outsider"]})
    assert to_outsider.status_code == 422

    ok = lead.post(f"/items/{item['id']}/assign", json=body)
    assert ok.status_code == 200 and ok.json()["assignee"]["id"] == world.users["member2"]


def test_allowed_actions_match_what_the_server_enforces(world):
    item = world.create_item()
    viewer_view = world.client("viewer").get(f"/items/{item['id']}").json()["allowed"]
    lead_view = world.client("lead").get(f"/items/{item['id']}").json()["allowed"]
    assert viewer_view == {"edit": False, "claim": False, "assign": False, "release": False, "comment": True, "transitions": []}
    assert lead_view["assign"] and lead_view["claim"] and lead_view["edit"]


def test_admin_sees_every_team(world):
    world.create_item()
    assert len(world.client("admin").get("/items").json()["items"]) == 1
