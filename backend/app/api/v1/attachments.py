"""Photos of fixes (§5.1 "optional photo evidence", §3 Layer 4 question 3).

Taken on a phone in poor light and uploaded over plant Wi-Fi, so every photo is
re-encoded on arrival: checked to really be an image, turned upright, stripped
of its metadata (a phone's GPS has no business in a repair record), and
shrunk to a size that loads on the same Wi-Fi. Files are stored per plant and
only served to people in that plant.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session
from app.api.scoping import in_plant
from app.core.config import settings
from app.models import Action, Attachment, MemoryEntry

router = APIRouter(prefix="/attachments", tags=["attachments"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EDGE = 1600
ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}
BACKEND_ROOT = Path(__file__).resolve().parents[3]


def storage_root() -> Path:
    root = Path(settings.storage_dir)
    return root if root.is_absolute() else BACKEND_ROOT / root


def _out(row: Attachment) -> dict:
    return {
        "id": str(row.id),
        "url": f"{settings.api_prefix}/attachments/{row.id}",
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    entity_type: Literal["action", "memory"] = Form(...),
    entity_id: uuid.UUID = Form(...),
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    model = Action if entity_type == "action" else MemoryEntry
    await in_plant(session, model, entity_id, identity.plant_id, entity_type.title())

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photos must be under 10 MB")
    try:
        encoded, width, height = await asyncio.to_thread(_normalise, raw)
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That file is not a JPEG, PNG or WebP photo"
        ) from None

    attachment_id = uuid.uuid4()
    now = datetime.now(UTC)
    key = f"{identity.plant_id}/{now:%Y/%m}/{attachment_id}.jpg"
    path = storage_root() / key
    await asyncio.to_thread(_write, path, encoded)

    row = Attachment(
        id=attachment_id,
        plant_id=identity.plant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=(file.filename or "photo")[:200],
        content_type="image/jpeg",
        size_bytes=len(encoded),
        width=width,
        height=height,
        sha256=hashlib.sha256(encoded).hexdigest(),
        storage_key=key,
        uploaded_by_id=identity.user.id,
    )
    session.add(row)
    await session.flush()
    return _out(row)


@router.get("")
async def list_attachments(
    entity_type: Literal["action", "memory"],
    entity_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(Attachment)
            .where(
                Attachment.plant_id == identity.plant_id,
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
            )
            .order_by(Attachment.created_at)
        )
    ).all()
    return [_out(row) for row in rows]


@router.get("/{attachment_id}")
async def download(
    attachment_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    row = await in_plant(session, Attachment, attachment_id, identity.plant_id, "Photo")
    path = storage_root() / row.storage_key
    try:
        content = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The photo file is missing") from None
    return Response(
        content=content,
        media_type=row.content_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


def _normalise(raw: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ACCEPTED_FORMATS:
            raise ValueError(f"unsupported format {image.format}")
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        buffer = io.BytesIO()
        # Saved without the original's EXIF: orientation is already applied.
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue(), image.width, image.height


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
