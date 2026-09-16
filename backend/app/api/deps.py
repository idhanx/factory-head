"""Request-scoped dependencies: session, identity, plant scope, role guards."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import SessionFactory, set_plant_scope
from app.core.security import decode_token
from app.models import Plant, Role, User

bearer = HTTPBearer(auto_error=False)


@dataclass(slots=True)
class Identity:
    """Who is asking, for which plant, wearing which hats."""

    user: User
    plant_id: uuid.UUID
    roles: frozenset[Role]

    def has(self, *roles: Role) -> bool:
        return bool(self.roles.intersection(roles))

    @property
    def display_name(self) -> str:
        return self.user.full_name


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Identity:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None

    plant_id = payload.get("plant")
    if not plant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token is not plant-scoped")

    user = await session.scalar(
        select(User)
        .where(User.id == uuid.UUID(payload["sub"]), User.is_active.is_(True))
        .options(selectinload(User.plants))
    )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled user")

    # Roles are re-read from the database, never trusted from the token: a role
    # revoked this morning must not stay valid until the token expires tonight.
    roles = frozenset(
        link.role for link in user.plants if str(link.plant_id) == str(plant_id)
    )
    if not roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this plant")

    # Bind the connection to this plant for the life of the transaction; RLS
    # policies do the filtering from here on.
    await set_plant_scope(session, uuid.UUID(plant_id))

    return Identity(user=user, plant_id=uuid.UUID(plant_id), roles=roles)


async def get_plant(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
) -> Plant:
    plant = await session.get(Plant, identity.plant_id)
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    return plant


def require(*allowed: Role):
    """Role guard.

    Screen 5 is the clearest reason this exists: the quality inspector's counts
    are only a cross-check on the supervisor's if the supervisor cannot enter
    them (§2, "two people would have to collude").
    """

    async def guard(identity: Identity = Depends(get_identity)) -> Identity:
        if Role.ADMIN in identity.roles or identity.has(*allowed):
            return identity
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires one of: {', '.join(r.value for r in allowed)}",
        )

    return guard
