"""Enumerated values of the auth feature. See app/items/enums.py for why these are StrEnums."""

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    MEMBER = "member"
    LEAD = "lead"
    # Effective role of a global admin. Never stored in memberships (see MEMBERSHIP_ROLES).
    ADMIN = "admin"


# Roles that may appear in the memberships table.
MEMBERSHIP_ROLES = (Role.VIEWER, Role.MEMBER, Role.LEAD)
