"""HTTP endpoints of the items feature. Thin: parse input, call the service, return its result.

Reads call service_query. Writes call service_command through core.idempotency.execute_mutation,
which handles the optional `Idempotency-Key` header and owns the commit.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.tokens import CurrentUserDep
from app.core.db import get_session
from app.core.idempotency import execute_mutation, request_fingerprint
from app.items import service_command, service_query
from app.items.enums import ItemType, Priority, Status
from app.items.schemas import (
    ActivityOut,
    ActivityPage,
    AssignIn,
    CommentIn,
    DashboardCounts,
    ItemCreate,
    ItemDetail,
    ItemPage,
    ItemUpdate,
    TransitionIn,
)

router = APIRouter(tags=["items"])

SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="Client-generated unique key; a retry with the same key replays the first response"),
]

CONFLICT_DOC = {status.HTTP_409_CONFLICT: {"description": "Version conflict, already claimed, or invalid workflow move"}}


# --- reads -------------------------------------------------------------------------------------


@router.get("/items", response_model=ItemPage)
def list_items(
    user: CurrentUserDep,
    session: SessionDep,
    team_id: int | None = None,
    status_filter: Annotated[list[Status] | None, Query(alias="status")] = None,
    priority: Annotated[list[Priority] | None, Query()] = None,
    type: Annotated[list[ItemType] | None, Query()] = None,
    assignee: Annotated[str | None, Query(description="'me', 'unassigned' or a user id")] = None,
    q: Annotated[str | None, Query(max_length=200, description="Full-text search (prefix match)")] = None,
    view: Annotated[
        str | None, Query(description="assigned_to_me | unassigned_urgent | awaiting_my_approval | overdue")
    ] = None,
    sort: Annotated[str | None, Query(description="updated (default) | priority")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ItemPage:
    filters = service_query.ItemFilters(
        team_id=team_id,
        status=status_filter,
        priority=priority,
        type=type,
        assignee=assignee,
        q=q,
        view=view,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return service_query.list_items(session, user, filters)


@router.get("/items/{item_id}", response_model=ItemDetail)
def get_item(item_id: int, user: CurrentUserDep, session: SessionDep) -> ItemDetail:
    return service_query.get_item(session, user, item_id)


@router.get("/items/{item_id}/activity", response_model=ActivityPage)
def list_activity(
    item_id: int,
    user: CurrentUserDep,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ActivityPage:
    return service_query.list_activity(session, user, item_id, cursor, limit)


@router.get("/dashboard/counts", response_model=DashboardCounts)
def dashboard_counts(user: CurrentUserDep, session: SessionDep) -> DashboardCounts:
    """Badge counts for the "needs attention" views. Each is one indexed COUNT, scoped to the user's teams."""
    return service_query.dashboard_counts(session, user)


# --- writes ------------------------------------------------------------------------------------


@router.post("/items", status_code=status.HTTP_201_CREATED, response_model=ItemDetail)
def create_item(body: ItemCreate, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", "/items", body.model_dump(mode="json")),
        action=lambda: service_command.create_item(session, user, body),
    )


@router.patch("/items/{item_id}", response_model=ItemDetail, responses=CONFLICT_DOC)
def update_item(
    item_id: int, body: ItemUpdate, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("PATCH", f"/items/{item_id}", body.model_dump(mode="json", exclude_unset=True)),
        action=lambda: service_command.update_item(session, user, item_id, body),
    )


@router.post("/items/{item_id}/claim", response_model=ItemDetail, responses=CONFLICT_DOC)
def claim_item(item_id: int, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/claim", {}),
        action=lambda: service_command.claim_item(session, user, item_id),
    )


@router.post("/items/{item_id}/assign", response_model=ItemDetail, responses=CONFLICT_DOC)
def assign_item(
    item_id: int, body: AssignIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/assign", body.model_dump(mode="json")),
        action=lambda: service_command.assign_item(session, user, item_id, body),
    )


@router.post("/items/{item_id}/transition", response_model=ItemDetail, responses=CONFLICT_DOC)
def transition_item(
    item_id: int, body: TransitionIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/transition", body.model_dump(mode="json")),
        action=lambda: service_command.transition_item(session, user, item_id, body),
    )


@router.post("/items/{item_id}/comments", status_code=status.HTTP_201_CREATED, response_model=ActivityOut)
def add_comment(
    item_id: int, body: CommentIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/comments", body.model_dump(mode="json")),
        action=lambda: service_command.add_comment(session, user, item_id, body),
    )
