"""Seed demo teams, users and a few thousand work items with consistent history.

    uv run python -m scripts.seed              # refuses if data already exists
    uv run python -m scripts.seed --reset      # wipe everything, then seed
    uv run python -m scripts.seed --if-empty   # seed only when the database is empty (docker)
    uv run python -m scripts.seed --items 20000

Every generated item has a history that the real workflow could have produced:
an item is only in_progress or later if it has an assignee, payment/compliance items only
reach resolved via awaiting_approval, and `version` equals 1 + number of change events.
"""

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.enums import ItemType, Priority, Role, Status
from app.models import ActivityEvent, Membership, Team, User, WorkItem
from app.passwords import hash_password

DEMO_PASSWORD = "demo1234"
# RFC 2606 reserved TLD: never a real domain, and accepted by EmailStr (".test" is not).
DEMO_EMAIL_DOMAIN = "newtonite.example"

TEAMS = ["Payments Ops", "Platform Engineering", "Customer Support", "Compliance & Risk"]

# (email prefix, display name, is_admin, {team: role})
USERS: list[tuple[str, str, bool, dict[str, Role]]] = [
    ("admin", "Asha Admin", True, {}),
    ("priya", "Priya Raman", False, {"Payments Ops": Role.LEAD, "Compliance & Risk": Role.MEMBER}),
    ("marco", "Marco Silva", False, {"Payments Ops": Role.MEMBER}),
    ("sam", "Sam Okafor", False, {"Payments Ops": Role.MEMBER, "Customer Support": Role.MEMBER}),
    ("lena", "Lena Novak", False, {"Platform Engineering": Role.LEAD}),
    ("dev", "Dev Kapoor", False, {"Platform Engineering": Role.MEMBER, "Payments Ops": Role.VIEWER}),
    ("nina", "Nina Berg", False, {"Platform Engineering": Role.MEMBER, "Compliance & Risk": Role.MEMBER}),
    ("chloe", "Chloe Martin", False, {"Customer Support": Role.LEAD}),
    ("omar", "Omar Haddad", False, {"Customer Support": Role.MEMBER}),
    ("rita", "Rita Gomez", False, {"Compliance & Risk": Role.LEAD}),
    ("victor", "Victor Auditor", False, {"Payments Ops": Role.VIEWER, "Compliance & Risk": Role.VIEWER}),
]

# Which item types each team typically handles.
TEAM_TYPES = {
    "Payments Ops": [ItemType.PAYMENT, ItemType.PAYMENT, ItemType.CUSTOMER_ISSUE, ItemType.TASK],
    "Platform Engineering": [ItemType.INCIDENT, ItemType.INCIDENT, ItemType.TASK, ItemType.CUSTOMER_ISSUE],
    "Customer Support": [ItemType.CUSTOMER_ISSUE, ItemType.CUSTOMER_ISSUE, ItemType.PAYMENT, ItemType.TASK],
    "Compliance & Risk": [ItemType.COMPLIANCE, ItemType.COMPLIANCE, ItemType.PAYMENT, ItemType.TASK],
}

TITLES = {
    ItemType.INCIDENT: [
        "API latency spike on {svc}",
        "Elevated 5xx errors from {svc}",
        "{svc} pods crash-looping after deploy",
        "Database connection pool exhausted on {svc}",
        "Disk usage above 90% on {svc} nodes",
    ],
    ItemType.CUSTOMER_ISSUE: [
        "Customer {cust} cannot log in",
        "{cust} reports missing invoice for {month}",
        "Escalation: {cust} unhappy with response time",
        "{cust} requests data export",
        "Duplicate account reported by {cust}",
    ],
    ItemType.PAYMENT: [
        "Refund failed for order #{num}",
        "Chargeback dispute from {cust}",
        "Payout to {cust} stuck in pending",
        "Investigate duplicate charge on order #{num}",
        "Reconcile settlement mismatch for {month}",
    ],
    ItemType.COMPLIANCE: [
        "KYC review for {cust}",
        "GDPR deletion request from {cust}",
        "Quarterly access review: {svc}",
        "Sanctions screening hit for {cust}",
        "Audit evidence request for {month} controls",
    ],
    ItemType.TASK: [
        "Rotate credentials for {svc}",
        "Update runbook for {svc}",
        "Approve vendor contract renewal for {cust}",
        "Onboard new team member to {svc}",
        "Clean up stale alerts in {svc}",
    ],
}
SERVICES = ["checkout", "ledger", "auth", "notifications", "search", "billing", "payouts", "gateway"]
CUSTOMERS = ["Acme Corp", "Globex", "Initech", "Umbrella", "Stark Retail", "Wayne Logistics", "Hooli", "Soylent"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September"]

STATUS_WEIGHTS = {
    Status.OPEN: 30,
    Status.IN_PROGRESS: 25,
    Status.AWAITING_APPROVAL: 10,
    Status.RESOLVED: 15,
    Status.CLOSED: 20,
}
PRIORITY_WEIGHTS = {Priority.CRITICAL: 5, Priority.HIGH: 20, Priority.MEDIUM: 45, Priority.LOW: 30}
REQUIRES_APPROVAL = {ItemType.PAYMENT, ItemType.COMPLIANCE}


def database_has_data(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(User)) > 0


def reset(session: Session) -> None:
    session.execute(
        text(
            "TRUNCATE notifications, jobs, idempotency_keys, activity_events, work_items, "
            "memberships, teams, users RESTART IDENTITY CASCADE"
        )
    )


def status_path(item_type: ItemType, final: Status, rng: random.Random) -> list[Status]:
    """A legal sequence of statuses from OPEN to `final`."""
    if final is Status.OPEN:
        return [Status.OPEN]
    path = [Status.OPEN, Status.IN_PROGRESS]
    if final is Status.IN_PROGRESS:
        return path
    if final is Status.AWAITING_APPROVAL:
        return path + [Status.AWAITING_APPROVAL]
    # resolved / closed: approval is mandatory for some types, optional for the rest.
    if item_type in REQUIRES_APPROVAL or rng.random() < 0.3:
        path.append(Status.AWAITING_APPROVAL)
    path.append(Status.RESOLVED)
    if final is Status.CLOSED:
        path.append(Status.CLOSED)
    return path


def seed(session: Session, item_count: int, rng: random.Random) -> None:
    teams = {name: session.scalar(insert(Team).values(name=name).returning(Team.id)) for name in TEAMS}

    users: dict[str, int] = {}
    for prefix, name, is_admin, _ in USERS:
        users[prefix] = session.scalar(
            insert(User)
            .values(
                email=f"{prefix}@{DEMO_EMAIL_DOMAIN}",
                name=name,
                password_hash=hash_password(DEMO_PASSWORD),
                is_admin=is_admin,
            )
            .returning(User.id)
        )

    # team id -> user ids who can do the work (members and leads), and leads only
    workers: dict[int, list[int]] = {tid: [] for tid in teams.values()}
    leads: dict[int, list[int]] = {tid: [] for tid in teams.values()}
    memberships = []
    for prefix, _, _, roles in USERS:
        for team_name, role in roles.items():
            tid = teams[team_name]
            memberships.append({"user_id": users[prefix], "team_id": tid, "role": role})
            if role in (Role.MEMBER, Role.LEAD):
                workers[tid].append(users[prefix])
            if role is Role.LEAD:
                leads[tid].append(users[prefix])
    session.execute(insert(Membership), memberships)

    now = datetime.now(UTC)
    item_rows: list[dict] = []
    histories: list[list[tuple[int, str, dict, datetime]]] = []  # per item: (actor, kind, payload, at)

    for _ in range(item_count):
        team_name = rng.choice(TEAMS)
        tid = teams[team_name]
        item_type = rng.choice(TEAM_TYPES[team_name])
        priority = rng.choices(list(PRIORITY_WEIGHTS), weights=list(PRIORITY_WEIGHTS.values()))[0]
        final = rng.choices(list(STATUS_WEIGHTS), weights=list(STATUS_WEIGHTS.values()))[0]
        title = rng.choice(TITLES[item_type]).format(
            svc=rng.choice(SERVICES), cust=rng.choice(CUSTOMERS), month=rng.choice(MONTHS), num=rng.randint(10000, 99999)
        )
        creator = rng.choice(workers[tid])
        created_at = now - timedelta(days=rng.uniform(0, 120))

        path = status_path(item_type, final, rng)
        # Open items: most are unowned (that is what makes "unassigned urgent" interesting).
        assignee = None
        if final is not Status.OPEN or rng.random() < 0.25:
            assignee = rng.choice(workers[tid])

        events: list[tuple[int, str, dict, datetime]] = [
            (creator, "created", {"title": title, "type": item_type, "priority": priority}, created_at)
        ]
        t = created_at
        if assignee is not None:
            t += timedelta(minutes=rng.uniform(1, 600))
            via = "claim" if assignee == creator or rng.random() < 0.6 else "assign"
            actor = assignee if via == "claim" else rng.choice(leads[tid] or workers[tid])
            events.append((actor, "assignee_changed", {"from": None, "to": assignee, "via": via}, t))
        for prev, nxt in zip(path, path[1:]):
            t += timedelta(minutes=rng.uniform(5, 2880))
            # Approvals, rejections and closing are lead actions; the rest is done by the assignee.
            lead_action = prev is Status.AWAITING_APPROVAL or nxt is Status.CLOSED
            actor = rng.choice(leads[tid] or workers[tid]) if lead_action else assignee
            events.append((actor, "status_changed", {"from": prev, "to": nxt}, t))
        if rng.random() < 0.4:
            t += timedelta(minutes=rng.uniform(1, 600))
            events.append((rng.choice(workers[tid]), "comment", {"body": rng.choice(COMMENTS)}, t))
        if t > now:  # recent item whose generated history runs into the future: shift it all back
            shift = t - now
            created_at -= shift
            events = [(a, k, p, at - shift) for a, k, p, at in events]
            t = now

        item_rows.append(
            {
                "team_id": tid,
                "type": item_type,
                "title": title,
                "description": f"Auto-generated demo item. Team: {team_name}. Raised by an internal user.",
                "priority": priority,
                "status": final,
                "assignee_id": assignee,
                "created_by": creator,
                "due_at": (created_at + timedelta(days=rng.choice([1, 2, 3, 7, 14])))
                if priority in (Priority.CRITICAL, Priority.HIGH)
                else None,
                # version counts every change after creation (comments do not change the item)
                "version": 1 + sum(1 for e in events[1:] if e[1] != "comment"),
                "created_at": created_at,
                "updated_at": t,
            }
        )
        histories.append(events)

    # Bulk insert; RETURNING with sort_by_parameter_order keeps ids aligned with item_rows.
    item_ids = session.scalars(
        insert(WorkItem).returning(WorkItem.id, sort_by_parameter_order=True), item_rows
    ).all()

    event_rows = [
        {"item_id": item_id, "actor_id": actor, "kind": kind, "payload": payload, "created_at": at}
        for item_id, events in zip(item_ids, histories)
        for actor, kind, payload, at in events
    ]
    for start in range(0, len(event_rows), 5000):
        session.execute(insert(ActivityEvent), event_rows[start : start + 5000])

    print(f"Seeded {len(TEAMS)} teams, {len(USERS)} users, {len(item_ids)} items, {len(event_rows)} activity events.")


COMMENTS = [
    "Looking into this now.",
    "Reproduced on staging, digging into logs.",
    "Waiting on the customer to confirm.",
    "Root cause identified, fix in review.",
    "Escalated to the on-call engineer.",
    "Added evidence to the shared drive.",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", type=int, default=5000)
    parser.add_argument("--reset", action="store_true", help="wipe all data before seeding")
    parser.add_argument("--if-empty", action="store_true", help="do nothing if users already exist")
    parser.add_argument("--random-seed", type=int, default=42)
    args = parser.parse_args()

    with SessionLocal() as session, session.begin():  # one transaction: all or nothing
        if database_has_data(session):
            if args.if_empty:
                print("Database already has data; skipping seed.")
                return 0
            if not args.reset:
                print("Database already has data. Use --reset to wipe and reseed.", file=sys.stderr)
                return 1
            reset(session)
        seed(session, args.items, random.Random(args.random_seed))

    print(f"\nDemo users (password for all: {DEMO_PASSWORD}):")
    for prefix, name, is_admin, roles in USERS:
        desc = "global admin" if is_admin else ", ".join(f"{role} of {team}" for team, role in roles.items())
        print(f"  {f'{prefix}@{DEMO_EMAIL_DOMAIN}':<27} {name:<16} {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
