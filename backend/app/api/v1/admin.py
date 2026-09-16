"""People, roles and plant settings — the configuration §9 Stage 1 lists.

Grant rules keep a plant's owner in charge of their own plant and nothing more:

* an owner (or consultant) manages who works in *this* plant, in plant roles;
* only a platform admin grants ``consultant`` or ``admin``, creates plants, or
  changes the login of someone who also works in another plant — otherwise one
  plant's owner could reset the password of the consultant who serves ten.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.core.security import hash_password
from app.kernel import rupees, to_rupees
from app.models import CostingMode, Plant, Role, User, UserPlantRole
from app.schemas import (
    AdminUserIn,
    AdminUserOut,
    AdminUserPatch,
    PlantCreateIn,
    PlantSettingsOut,
    PlantSettingsPatch,
)

router = APIRouter(prefix="/admin", tags=["admin"])

MANAGERS = (Role.OWNER, Role.CONSULTANT)
PLATFORM_ROLES = {Role.CONSULTANT, Role.ADMIN}
KEEP_ACCESS_ROLES = {Role.OWNER, Role.ADMIN}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get(
    "/users", response_model=list[AdminUserOut], dependencies=[Depends(require(*MANAGERS))]
)
async def list_users(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(User, UserPlantRole.role)
            .join(UserPlantRole, UserPlantRole.user_id == User.id)
            .where(UserPlantRole.plant_id == identity.plant_id)
            .order_by(User.full_name)
        )
    ).all()
    users: dict[uuid.UUID, AdminUserOut] = {}
    for user, role in rows:
        entry = users.setdefault(user.id, _user_out(user, []))
        entry.roles.append(role.value)
    return list(users.values())


@router.post(
    "/users",
    response_model=AdminUserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*MANAGERS))],
)
async def create_user(
    payload: AdminUserIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Add someone to this plant. An existing login keeps its password."""
    if not payload.email and not payload.phone:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Give an email or a phone number to sign in with"
        )
    roles = {Role(role) for role in payload.roles}
    _check_grants(identity, roles)

    user = await session.scalar(
        select(User).where(
            or_(
                *([User.email == payload.email] if payload.email else []),
                *([User.phone == payload.phone] if payload.phone else []),
            )
        )
    )
    if user is None:
        user = User(
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            password_hash=hash_password(payload.password),
        )
        session.add(user)
        await _flush_unique(session)

    existing = await _roles_of(session, user.id, identity.plant_id)
    await _replace_roles(session, user.id, identity.plant_id, roles | existing)
    return await _load_out(session, user, identity.plant_id)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserOut,
    dependencies=[Depends(require(*MANAGERS))],
)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    current = await _roles_of(session, user_id, identity.plant_id) if user else set()
    if user is None or not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found in this plant")

    changes = payload.model_dump(exclude_unset=True)
    login_fields = {"full_name", "email", "phone", "password"} & changes.keys()
    if (
        login_fields
        and not identity.has(Role.ADMIN)
        and await _works_elsewhere(session, user_id, identity.plant_id)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This person also works in another plant; ask a platform admin to change their login",
        )

    if "roles" in changes:
        new_roles = {Role(role) for role in changes.pop("roles") or []}
        _check_grants(identity, (new_roles ^ current))
        if user_id == identity.user.id and (current & KEEP_ACCESS_ROLES) - new_roles:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "You cannot remove your own owner or admin role"
            )
        await _replace_roles(session, user_id, identity.plant_id, new_roles)

    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
    for field, value in changes.items():
        setattr(user, field, value)
    await _flush_unique(session)
    return await _load_out(session, user, identity.plant_id)


# ---------------------------------------------------------------------------
# Plant settings
# ---------------------------------------------------------------------------


@router.get(
    "/plant", response_model=PlantSettingsOut, dependencies=[Depends(require(*MANAGERS))]
)
async def get_plant_settings(plant: Plant = Depends(get_plant)):
    return _plant_out(plant)


@router.patch(
    "/plant", response_model=PlantSettingsOut, dependencies=[Depends(require(*MANAGERS))]
)
async def update_plant_settings(
    payload: PlantSettingsPatch,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Thresholds and modes are set in onboarding and tightened as the plant
    matures (§5.2). Changing them affects what happens next, never history:
    costed events and frozen baselines keep the values they were made with."""
    changes = payload.model_dump(exclude_unset=True)
    if "action_cost_threshold_rupees" in changes:
        plant.action_cost_threshold = rupees(str(changes.pop("action_cost_threshold_rupees")))
    if "monthly_fee_rupees" in changes:
        plant.monthly_fee = rupees(str(changes.pop("monthly_fee_rupees")))
    if "reconciliation_tolerance_percent" in changes:
        percent = Decimal(str(changes.pop("reconciliation_tolerance_percent")))
        plant.reconciliation_tolerance_bp = int(percent * 100)
    if "costing_mode" in changes:
        plant.costing_mode = CostingMode(changes.pop("costing_mode"))
    for field, value in changes.items():
        setattr(plant, field, value)
    await session.flush()
    return _plant_out(plant)


@router.post(
    "/plants",
    response_model=PlantSettingsOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Role.ADMIN))],
)
async def create_plant(
    payload: PlantCreateIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """A new plant with its owner. The admin who creates it can switch into it."""
    if not identity.has(Role.ADMIN):
        # require() lets admin through; an owner of another plant must not.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a platform admin can create plants")
    if not payload.owner.email and not payload.owner.phone:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The owner needs an email or a phone number"
        )

    plant = Plant(
        name=payload.name,
        code=payload.code,
        location=payload.location,
        timezone=payload.timezone,
        costing_mode=CostingMode(payload.costing_mode),
    )
    session.add(plant)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Plant code {payload.code} already exists"
        ) from None

    owner = await session.scalar(
        select(User).where(
            or_(
                *([User.email == payload.owner.email] if payload.owner.email else []),
                *([User.phone == payload.owner.phone] if payload.owner.phone else []),
            )
        )
    )
    if owner is None:
        owner = User(
            full_name=payload.owner.full_name,
            email=payload.owner.email,
            phone=payload.owner.phone,
            password_hash=hash_password(payload.owner.password),
        )
        session.add(owner)
        await _flush_unique(session)

    session.add_all(
        [
            UserPlantRole(user_id=owner.id, plant_id=plant.id, role=Role.OWNER),
            UserPlantRole(user_id=identity.user.id, plant_id=plant.id, role=Role.ADMIN),
        ]
    )
    await session.flush()
    # §15: "T6 has working defaults out of the box." The new plant's rows are
    # written under its own plant scope, which row-level security requires.
    from app.api.v1.onboarding import load_default_codes
    from app.core.db import set_plant_scope

    await set_plant_scope(session, plant.id)
    await load_default_codes(session, plant)
    await set_plant_scope(session, identity.plant_id)
    return _plant_out(plant)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _check_grants(identity: Identity, roles: set[Role]) -> None:
    if roles & PLATFORM_ROLES and not identity.has(Role.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only a platform admin can grant or remove consultant or admin"
        )


async def _roles_of(session: AsyncSession, user_id: uuid.UUID, plant_id: uuid.UUID) -> set[Role]:
    return set(
        (
            await session.scalars(
                select(UserPlantRole.role).where(
                    UserPlantRole.user_id == user_id, UserPlantRole.plant_id == plant_id
                )
            )
        ).all()
    )


async def _works_elsewhere(
    session: AsyncSession, user_id: uuid.UUID, plant_id: uuid.UUID
) -> bool:
    return bool(
        await session.scalar(
            select(UserPlantRole.id)
            .where(UserPlantRole.user_id == user_id, UserPlantRole.plant_id != plant_id)
            .limit(1)
        )
    )


async def _replace_roles(
    session: AsyncSession, user_id: uuid.UUID, plant_id: uuid.UUID, roles: set[Role]
) -> None:
    await session.execute(
        delete(UserPlantRole).where(
            UserPlantRole.user_id == user_id, UserPlantRole.plant_id == plant_id
        )
    )
    session.add_all(
        UserPlantRole(user_id=user_id, plant_id=plant_id, role=role) for role in sorted(roles)
    )
    await session.flush()


async def _flush_unique(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email or phone number is already in use"
        ) from None


async def _load_out(session: AsyncSession, user: User, plant_id: uuid.UUID) -> AdminUserOut:
    roles = await _roles_of(session, user.id, plant_id)
    return _user_out(user, sorted(role.value for role in roles))


def _user_out(user: User, roles: list[str]) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        roles=roles,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
    )


def _plant_out(plant: Plant) -> PlantSettingsOut:
    return PlantSettingsOut(
        id=plant.id,
        name=plant.name,
        code=plant.code,
        location=plant.location,
        timezone=plant.timezone,
        costing_mode=getattr(plant.costing_mode, "value", plant.costing_mode),
        action_cost_threshold_rupees=float(to_rupees(plant.action_cost_threshold)),
        action_duration_minutes=plant.action_duration_minutes,
        recurrence_count=plant.recurrence_count,
        recurrence_window_shifts=plant.recurrence_window_shifts,
        reconciliation_tolerance_parts=plant.reconciliation_tolerance_parts,
        reconciliation_tolerance_percent=plant.reconciliation_tolerance_bp / 100,
        monthly_fee_rupees=float(to_rupees(plant.monthly_fee)),
        go_live_date=plant.go_live_date,
        no_blame_until=plant.no_blame_until,
        action_due_days=plant.action_due_days or 3,
    )
