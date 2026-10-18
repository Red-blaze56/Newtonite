"""Work-item endpoints. Thin: parse input, call app.services.items, return its result.

Every mutating endpoint accepts an optional `Idempotency-Key` header and runs through
idempotency.execute_mutation, which also owns the commit.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import CurrentUserDep
from app.db import get_session
from app.enums import ItemType, Priority, Status
from app.idempotency import execute_mutation, request_fingerprint
from app.schemas import (
    ActivityOut,
    ActivityPage,
    AssignIn,
    CommentIn,
    ItemCreate,
    ItemDetail,
    ItemPage,
    ItemUpdate,
    TransitionIn,
)
from app.services import items as svc

router = APIRouter(prefix="/items", tags=["items"])

SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="Client-generated unique key; a retry with the same key replays the first response"),
]

CONFLICT_DOC = {409: {"description": "Version conflict, already claimed, or invalid workflow move"}}


@router.get("", response_model=ItemPage)
def list_items(
    user: CurrentUserDep,
    session: SessionDep,
    team_id: int | None = None,
    status: Annotated[list[Status] | None, Query()] = None,
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
    filters = svc.ItemFilters(
        team_id=team_id,
        status=status,
        priority=priority,
        type=type,
        assignee=assignee,
        q=q,
        view=view,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return svc.list_items(session, user, filters)


@router.post("", status_code=201, response_model=ItemDetail)
def create_item(body: ItemCreate, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", "/items", body.model_dump(mode="json")),
        action=lambda: svc.create_item(session, user, body),
    )


@router.get("/{item_id}", response_model=ItemDetail)
def get_item(item_id: int, user: CurrentUserDep, session: SessionDep) -> ItemDetail:
    return svc.get_item(session, user, item_id)


@router.patch("/{item_id}", response_model=ItemDetail, responses=CONFLICT_DOC)
def update_item(
    item_id: int, body: ItemUpdate, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("PATCH", f"/items/{item_id}", body.model_dump(mode="json", exclude_unset=True)),
        action=lambda: svc.update_item(session, user, item_id, body),
    )


@router.post("/{item_id}/claim", response_model=ItemDetail, responses=CONFLICT_DOC)
def claim_item(item_id: int, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/claim", {}),
        action=lambda: svc.claim_item(session, user, item_id),
    )


@router.post("/{item_id}/assign", response_model=ItemDetail, responses=CONFLICT_DOC)
def assign_item(
    item_id: int, body: AssignIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/assign", body.model_dump(mode="json")),
        action=lambda: svc.assign_item(session, user, item_id, body),
    )


@router.post("/{item_id}/transition", response_model=ItemDetail, responses=CONFLICT_DOC)
def transition_item(
    item_id: int, body: TransitionIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/transition", body.model_dump(mode="json")),
        action=lambda: svc.transition_item(session, user, item_id, body),
    )


@router.get("/{item_id}/activity", response_model=ActivityPage)
def list_activity(
    item_id: int,
    user: CurrentUserDep,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ActivityPage:
    return svc.list_activity(session, user, item_id, cursor, limit)


@router.post("/{item_id}/comments", status_code=201, response_model=ActivityOut)
def add_comment(
    item_id: int, body: CommentIn, user: CurrentUserDep, session: SessionDep, key: IdempotencyKey = None
) -> JSONResponse:
    return execute_mutation(
        session,
        user_id=user.id,
        key=key,
        fingerprint=request_fingerprint("POST", f"/items/{item_id}/comments", body.model_dump(mode="json")),
        action=lambda: svc.add_comment(session, user, item_id, body),
    )
