"""Request and response bodies of the auth feature (validated at the API boundary by Pydantic)."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.auth.enums import Role


class LoginIn(BaseModel):
    # EmailStr rejects malformed addresses with a 422 before any database work. It only
    # lower-cases the domain part, so authenticate() still normalises the whole address.
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TeamRoleOut(BaseModel):
    id: int
    name: str
    role: Role  # the caller's effective role in this team (admin for global admins)


class MeOut(BaseModel):
    id: int
    email: str
    name: str
    is_admin: bool
    teams: list[TeamRoleOut]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: MeOut


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str


class TeamMemberOut(UserBrief):
    role: Role
