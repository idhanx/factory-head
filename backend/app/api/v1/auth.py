"""Authentication and plant switching."""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import Identity, get_identity, get_session
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models import Plant, User
from app.schemas import (
    LoginRequest,
    PlantMembership,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(
        select(User)
        .where(
            or_(User.email == payload.identifier, User.phone == payload.identifier),
            User.is_active.is_(True),
        )
        .options(selectinload(User.plants))
    )

    # Same response whether the account is unknown or the password is wrong —
    # a login form should not confirm which phone numbers exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    memberships = await _memberships(session, user)
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No plant access")

    chosen = memberships[0]
    if payload.plant_code:
        matched = next((m for m in memberships if m.plant_code == payload.plant_code), None)
        if matched is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to that plant")
        chosen = matched

    user.last_login_at = datetime.now(UTC)

    return _token_response(user, chosen, memberships)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: AsyncSession = Depends(get_session)):
    """Also the plant-switch endpoint: a different plant means a different token."""
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None

    user = await session.scalar(
        select(User)
        .where(User.id == claims["sub"], User.is_active.is_(True))
        .options(selectinload(User.plants))
    )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")

    memberships = await _memberships(session, user)
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No plant access")

    target = str(payload.plant_id or claims.get("plant") or memberships[0].plant_id)
    chosen = next((m for m in memberships if str(m.plant_id) == target), None)
    if chosen is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to that plant")

    return _token_response(user, chosen, memberships)


@router.get("/me", response_model=TokenResponse)
async def me(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    memberships = await _memberships(session, identity.user)
    chosen = next(m for m in memberships if str(m.plant_id) == str(identity.plant_id))
    return _token_response(identity.user, chosen, memberships)


async def _memberships(session: AsyncSession, user: User) -> list[PlantMembership]:
    plant_ids = {link.plant_id for link in user.plants}
    if not plant_ids:
        return []

    plants = {
        plant.id: plant
        for plant in (
            await session.scalars(
                select(Plant).where(Plant.id.in_(plant_ids), Plant.is_active.is_(True))
            )
        ).all()
    }

    grouped: dict = {}
    for link in user.plants:
        plant = plants.get(link.plant_id)
        if plant is None:
            continue
        grouped.setdefault(
            plant.id,
            PlantMembership(
                plant_id=plant.id,
                plant_name=plant.name,
                plant_code=plant.code,
                roles=[],
            ),
        ).roles.append(link.role.value)

    return list(grouped.values())


def _token_response(
    user: User, chosen: PlantMembership, memberships: list[PlantMembership]
) -> TokenResponse:
    return TokenResponse(
        access_token=create_token(
            user_id=user.id, plant_id=chosen.plant_id, roles=chosen.roles
        ),
        refresh_token=create_token(
            user_id=user.id,
            plant_id=chosen.plant_id,
            roles=chosen.roles,
            token_type="refresh",
        ),
        user_id=user.id,
        full_name=user.full_name,
        plant_id=chosen.plant_id,
        plant_name=chosen.plant_name,
        roles=chosen.roles,
        memberships=memberships,
        preferred_language=user.preferred_language,
    )
