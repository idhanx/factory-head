"""Photo storage, in one place.

Taken on a phone in poor light and uploaded over plant Wi-Fi, so every photo is
re-encoded on arrival: checked to really be an image, turned upright, stripped
of its metadata (a phone's GPS has no business in a repair record), and shrunk
to a size that loads on the same Wi-Fi. Files are stored per plant and only
served to people in that plant.

This lives in a service rather than in the upload endpoint because two callers
need it: the Actions/Memory upload route, and Kaizen's Step 2, where the photo
is stored *and* read by a local vision model in the same request.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Attachment, Plant

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EDGE = 1600
ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class UnsupportedPhoto(ValueError):
    """The upload is not a photo we can read. A 422, not a 500."""


def storage_root() -> Path:
    root = Path(settings.storage_dir)
    return root if root.is_absolute() else BACKEND_ROOT / root


async def store(
    session: AsyncSession,
    *,
    plant: Plant,
    entity_type: str,
    entity_id: uuid.UUID,
    filename: str | None,
    content_type: str,
    data: bytes,
    uploaded_by_id: uuid.UUID | None = None,
) -> Attachment:
    """Normalise, write to disk, and record the row. Raises :class:`UnsupportedPhoto`."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsupportedPhoto("Photos must be under 10 MB")
    try:
        encoded, width, height = await asyncio.to_thread(normalise, data)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedPhoto("That file is not a JPEG, PNG or WebP photo") from exc

    attachment_id = uuid.uuid4()
    now = datetime.now(UTC)
    key = f"{plant.id}/{now:%Y/%m}/{attachment_id}.jpg"
    await asyncio.to_thread(_write, storage_root() / key, encoded)

    row = Attachment(
        id=attachment_id,
        plant_id=plant.id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=(filename or "photo")[:200],
        content_type="image/jpeg",
        size_bytes=len(encoded),
        width=width,
        height=height,
        sha256=hashlib.sha256(encoded).hexdigest(),
        storage_key=key,
        uploaded_by_id=uploaded_by_id,
    )
    session.add(row)
    await session.flush()
    return row


async def reassign(
    session: AsyncSession, *, plant: Plant, attachment_id: uuid.UUID, entity_id: uuid.UUID
) -> bool:
    """Point a photo at the record it turned out to belong to.

    Kaizen's Step 2 stores the photo before the Kaizen exists — the operator may
    look at the findings and abandon the submission. The row is created against
    a placeholder id and moved here once there is something to attach it to.
    """
    row = await session.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id, Attachment.plant_id == plant.id
        )
    )
    if row is None:
        return False
    await session.execute(
        update(Attachment)
        .where(Attachment.id == attachment_id, Attachment.plant_id == plant.id)
        .values(entity_id=entity_id)
    )
    return True


def normalise(raw: bytes) -> tuple[bytes, int, int]:
    """Upright, RGB, at most 1600px on the long edge, no EXIF."""
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ACCEPTED_FORMATS:
            raise UnsupportedPhoto(f"unsupported format {image.format}")
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
