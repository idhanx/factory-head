"""Photos of fixes (§5.1 "optional photo evidence", §3 Layer 4 question 3).

The normalisation and storage rules live in
:mod:`app.services.attachments_store`, because Kaizen's Step 2 stores a photo
and reads it with a vision model in the same request and must not have a second
copy of them.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session
from app.api.scoping import in_plant
from app.core.config import settings
from app.models import Action, Attachment, KaizenIdea, MemoryEntry, Plant
from app.services.attachments_store import (
    MAX_UPLOAD_BYTES,
    UnsupportedPhoto,
    storage_root,
)
from app.services.attachments_store import store as store_photo

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _out(row: Attachment) -> dict:
    return {
        "id": str(row.id),
        "url": f"{settings.api_prefix}/attachments/{row.id}",
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
    }


#: Everything a photo can hang off. Kaizen joins the list because Step 2's
#: photo is evidence in exactly the same sense as a photo of a fix.
ENTITY_MODELS = {"action": Action, "memory": MemoryEntry, "kaizen": KaizenIdea}


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    entity_type: Literal["action", "memory", "kaizen"] = Form(...),
    entity_id: uuid.UUID = Form(...),
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    await in_plant(
        session, ENTITY_MODELS[entity_type], entity_id, identity.plant_id, entity_type.title()
    )
    plant = await session.get(Plant, identity.plant_id)

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        row = await store_photo(
            session,
            plant=plant,
            entity_type=entity_type,
            entity_id=entity_id,
            filename=file.filename,
            content_type=file.content_type or "image/jpeg",
            data=raw,
            uploaded_by_id=identity.user.id,
        )
    except UnsupportedPhoto as exc:
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "10 MB" in str(exc)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, str(exc)) from None
    return _out(row)


@router.get("")
async def list_attachments(
    entity_type: Literal["action", "memory", "kaizen"],
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
