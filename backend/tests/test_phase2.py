"""Phase 2: offline sync, delivery, corrections, photos, onboarding, report fixes."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from openpyxl import Workbook
from PIL import Image
from sqlalchemy import func, select

from app.delivery import transports, webpush
from app.kernel import rupees
from app.models import (
    Action,
    AlertRecipient,
    AuditLog,
    CostRate,
    DowntimeEvent,
    DowntimeStatus,
    Escalation,
    EscalationStatus,
    MarginBandValue,
    Notification,
    NotificationChannel,
    NotificationStatus,
    ProductionCount,
    ProductionTarget,
    ReasonCode,
    RootCauseAnalysis,
    ShiftSession,
    SyncHold,
)
from app.services import notifications
from app.services import shifts as shift_service
from app.services.shifts import resolve_business_date
from tests.test_api_phase1 import _job, _start_shift


def _ref() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Offline sync
# ---------------------------------------------------------------------------


async def test_a_retried_offline_batch_records_each_entry_once(api, session, fixtures):
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    station = str(fixtures["station"].id)
    started = datetime.now(UTC) - timedelta(minutes=30)
    start_ref, close_ref, count_ref = _ref(), _ref(), _ref()
    batch = {
        "device_id": "tablet-zone-1",
        "ops": [
            {
                "client_ref": start_ref,
                "kind": "downtime_start",
                "shift_session_id": shift["id"],
                "occurred_at": started.isoformat(),
                "payload": {
                    "station_id": station,
                    "reason_code_id": str(fixtures["reasons"]["TOOL_BREAKAGE"].id),
                },
            },
            {
                "client_ref": close_ref,
                "kind": "downtime_close",
                "shift_session_id": shift["id"],
                "occurred_at": (started + timedelta(minutes=22)).isoformat(),
                "payload": {"start_client_ref": start_ref},
            },
            {
                "client_ref": count_ref,
                "kind": "production",
                "shift_session_id": shift["id"],
                "occurred_at": (started + timedelta(minutes=25)).isoformat(),
                "payload": {"station_id": station, "count": 120},
            },
        ],
    }
    first = await api["client"].post("/api/v1/sync/batch", headers=api["as"]("supervisor"), json=batch)
    assert first.status_code == 200, first.text
    assert [r["status"] for r in first.json()["results"]] == ["applied", "applied", "applied"]

    again = await api["client"].post("/api/v1/sync/batch", headers=api["as"]("supervisor"), json=batch)
    assert [r["status"] for r in again.json()["results"]] == ["duplicate", "duplicate", "duplicate"]

    stop = await session.scalar(select(DowntimeEvent).where(DowntimeEvent.client_ref == start_ref))
    assert stop.status is DowntimeStatus.CLOSED
    assert round(float(stop.duration_minutes)) == 22  # the floor's times, not the sync's
    counts = await session.scalar(
        select(func.count(ProductionCount.id)).where(ProductionCount.client_ref == count_ref)
    )
    assert counts == 1


async def test_an_entry_for_a_locked_shift_is_held_then_applied_by_the_owner(
    api, session, fixtures
):
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    row = await session.get(ShiftSession, uuid.UUID(shift["id"]))
    await shift_service.close_shift(session, shift_session=row, closed_by_id=None)

    op_ref = _ref()
    result = (
        await api["client"].post(
            "/api/v1/sync/batch",
            headers=api["as"]("supervisor"),
            json={
                "ops": [
                    {
                        "client_ref": op_ref,
                        "kind": "production",
                        "shift_session_id": shift["id"],
                        "occurred_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                        "payload": {"station_id": str(fixtures["station"].id), "count": 40},
                    }
                ]
            },
        )
    ).json()["results"][0]
    assert result["status"] == "held"

    holds = await api["client"].get("/api/v1/sync/holds", headers=api["as"]("owner"))
    assert [h["id"] for h in holds.json()] == [result["hold_id"]]
    assert (
        await api["client"].get("/api/v1/sync/holds", headers=api["as"]("supervisor"))
    ).status_code == 403

    applied = await api["client"].post(
        f"/api/v1/sync/holds/{result['hold_id']}/apply",
        headers=api["as"]("owner"),
        json={"note": "Tablet was out of Wi-Fi range at the press"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"
    assert await session.scalar(
        select(func.count(ProductionCount.id)).where(ProductionCount.client_ref == op_ref)
    ) == 1
    assert await session.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.action == "sync_hold_applied")
    ) == 1


async def test_a_bad_entry_is_rejected_without_losing_the_rest_of_the_batch(
    api, session, fixtures
):
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    now = datetime.now(UTC)
    results = (
        await api["client"].post(
            "/api/v1/sync/batch",
            headers=api["as"]("supervisor"),
            json={
                "ops": [
                    {
                        "client_ref": _ref(),
                        "kind": "production",
                        "shift_session_id": shift["id"],
                        "occurred_at": now.isoformat(),
                        "payload": {"station_id": str(uuid.uuid4()), "count": 5},
                    },
                    {
                        "client_ref": _ref(),
                        "kind": "quality",
                        "shift_session_id": shift["id"],
                        "occurred_at": now.isoformat(),
                        "payload": {"part_id": str(fixtures["part"].id), "good_count": 5, "reject_count": 0},
                    },
                    {
                        "client_ref": _ref(),
                        "kind": "production",
                        "shift_session_id": shift["id"],
                        "occurred_at": now.isoformat(),
                        "payload": {"station_id": str(fixtures["station"].id), "count": 5},
                    },
                ]
            },
        )
    ).json()["results"]
    # Station unknown; a supervisor cannot record quality; the third still lands.
    assert [r["status"] for r in results] == ["rejected", "rejected", "applied"]


# ---------------------------------------------------------------------------
# Corrections and the audit trail
# ---------------------------------------------------------------------------


async def test_a_correction_recosts_the_stop_and_leaves_a_trail(api, session, fixtures):
    await _start_shift(api, fixtures, await _job(session, fixtures))
    started = datetime.now(UTC) - timedelta(minutes=58)
    stop = (
        await api["client"].post(
            "/api/v1/downtime/start",
            headers=api["as"]("supervisor"),
            json={
                "station_id": str(fixtures["station"].id),
                "reason_code_id": str(fixtures["reasons"]["TOOL_BREAKAGE"].id),
                "started_at": started.isoformat(),
            },
        )
    ).json()
    await api["client"].post(
        f"/api/v1/downtime/{stop['id']}/close",
        headers=api["as"]("supervisor"),
        json={"ended_at": (started + timedelta(minutes=55)).isoformat()},
    )

    corrected = await api["client"].post(
        f"/api/v1/corrections/downtime/{stop['id']}",
        headers=api["as"]("supervisor"),
        json={
            "ended_at": (started + timedelta(minutes=47)).isoformat(),
            "reason": "Tapped Running late — tool was fitted at 11:01",
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["cost_paise"] == 135650  # the §3 example: 47 minutes on CNC-3

    trail = await api["client"].get(
        f"/api/v1/audit-log?entity_type=downtime_event&entity_id={stop['id']}",
        headers=api["as"]("owner"),
    )
    entry = trail.json()[0]
    assert entry["action"] == "correction"
    assert entry["before"]["cost_paise"] != entry["after"]["cost_paise"]


async def test_a_supervisor_cannot_correct_a_locked_shift_but_the_owner_can(
    api, session, fixtures
):
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    count = (
        await api["client"].post(
            "/api/v1/production",
            headers=api["as"]("supervisor"),
            json={"station_id": str(fixtures["station"].id), "count": 100},
        )
    ).json()
    row = await session.get(ShiftSession, uuid.UUID(shift["id"]))
    await shift_service.close_shift(session, shift_session=row, closed_by_id=None)

    body = {"count": 90, "reason": "Ten were counted twice"}
    refused = await api["client"].post(
        f"/api/v1/corrections/production/{count['id']}", headers=api["as"]("supervisor"), json=body
    )
    assert refused.status_code == 409
    allowed = await api["client"].post(
        f"/api/v1/corrections/production/{count['id']}", headers=api["as"]("owner"), json=body
    )
    assert allowed.status_code == 200
    entries = (
        await api["client"].get(f"/api/v1/shifts/{shift['id']}/entries", headers=api["as"]("supervisor"))
    ).json()
    assert entries["can_correct"] is False
    assert entries["production"][0]["count"] == 90


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


async def test_a_photo_is_resized_stripped_and_served_only_in_its_plant(
    api, session, fixtures, tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    action = (
        await api["client"].post(
            "/api/v1/actions", headers=api["as"]("supervisor"), json={"title": "Insert wear on CNC-3"}
        )
    ).json()
    buffer = io.BytesIO()
    Image.new("RGB", (4000, 3000), "orange").save(buffer, format="JPEG")

    uploaded = await api["client"].post(
        "/api/v1/attachments",
        headers=api["as"]("maintenance"),
        data={"entity_type": "action", "entity_id": action["id"]},
        files={"file": ("fix.jpg", buffer.getvalue(), "image/jpeg")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert max(body["width"], body["height"]) == 1600

    image = await api["client"].get(body["url"], headers=api["as"]("owner"))
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"

    detail = (
        await api["client"].get(f"/api/v1/actions/{action['id']}", headers=api["as"]("owner"))
    ).json()
    assert detail["photos"][0]["id"] == body["id"]

    not_an_image = await api["client"].post(
        "/api/v1/attachments",
        headers=api["as"]("maintenance"),
        data={"entity_type": "action", "entity_id": action["id"]},
        files={"file": ("fix.jpg", b"<html>not a photo</html>", "image/jpeg")},
    )
    assert not_an_image.status_code == 422


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_web_push_payload_decrypts_with_the_browsers_keys():
    """RFC 8291 round trip: what the server encrypts, the browser can read."""
    browser = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    p256dh = webpush.b64url_encode(
        browser.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    message = b'{"title":"CNC-3 stopped","body":"15 min, \\u20b91,050"}'
    blob = webpush.encrypt(message, p256dh=p256dh, auth=webpush.b64url_encode(auth))

    import hashlib
    import hmac
    import struct

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, rs, idlen = blob[:16], *struct.unpack("!IB", blob[16:21])
    server_public = blob[21 : 21 + idlen]
    ciphertext = blob[21 + idlen :]
    assert rs == 4096
    shared = browser.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
    )

    def mac(key, data):
        return hmac.new(key, data, hashlib.sha256).digest()

    ua_public = webpush.b64url_decode(p256dh)
    ikm = mac(mac(auth, shared), b"WebPush: info\x00" + ua_public + server_public + b"\x01")
    prk = mac(salt, ikm)
    cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain == message + b"\x02"


async def test_with_no_channel_configured_the_gap_is_recorded_not_hidden(session, fixtures):
    plant = fixtures["plant"]
    session.add(
        AlertRecipient(
            plant_id=plant.id, name="Suresh K.", role_tag="maintenance", whatsapp_number="+919000000013"
        )
    )
    await session.flush()
    queued = await notifications.enqueue(
        session,
        plant=plant,
        kind="escalation",
        recipients=await notifications.recipients_for_route(session, plant.id, "maintenance"),
        body="CNC-3 waiting on tool breakage — 15 min, ₹1,050 and counting.",
        dedupe_key="test",
    )
    assert [(row.channel, row.status) for row in queued] == [
        (NotificationChannel.IN_APP, NotificationStatus.SKIPPED)
    ]
    assert not notifications.reached_anyone(queued)
    assert "No WhatsApp or SMS channel" in queued[0].last_error


async def test_whatsapp_failure_falls_back_to_sms(session, fixtures, monkeypatch):
    from app.core.config import settings

    plant = fixtures["plant"]
    for name, value in {
        "wati_enabled": True, "wati_base_url": "https://wati.example", "wati_api_token": "t",
        "msg91_enabled": True, "msg91_auth_key": "k", "msg91_template_id": "tpl",
        "delivery_max_attempts": 1,
    }.items():
        monkeypatch.setattr(settings, name, value)

    sent: list[str] = []

    async def whatsapp_down(**kwargs):
        sent.append("whatsapp")
        return transports.SendResult(ok=False, error="WATI 500")

    async def sms_ok(**kwargs):
        sent.append("sms")
        return transports.SendResult(ok=True, provider_id="req-1")

    monkeypatch.setattr(transports.whatsapp, "send", whatsapp_down)
    monkeypatch.setattr(transports.sms, "send", sms_ok)

    await notifications.enqueue(
        session,
        plant=plant,
        kind="shift_report",
        recipients=[notifications.Recipient(name="Mr. Kumaran", number="+919876543210")],
        body="Shift A: 2,140 parts (target 2,280). Recoverable loss ₹7,890.",
        dedupe_key="shift:test",
    )
    await notifications.dispatch_due(session, plant=plant)
    await notifications.dispatch_due(session, plant=plant)

    rows = (await session.scalars(select(Notification).order_by(Notification.created_at))).all()
    assert sent == ["whatsapp", "sms"]
    assert [(r.channel, r.status) for r in rows if r.kind == "shift_report"] == [
        (NotificationChannel.WHATSAPP, NotificationStatus.FAILED),
        (NotificationChannel.SMS, NotificationStatus.SENT),
    ]


async def test_an_escalation_nobody_can_receive_is_marked_failed(api, session, fixtures):
    from app.workers import jobs

    await _start_shift(api, fixtures, await _job(session, fixtures))
    stop = (
        await api["client"].post(
            "/api/v1/downtime/start",
            headers=api["as"]("supervisor"),
            json={
                "station_id": str(fixtures["station"].id),
                "reason_code_id": str(fixtures["reasons"]["TOOL_BREAKAGE"].id),
                "started_at": (datetime.now(UTC) - timedelta(minutes=16)).isoformat(),
            },
        )
    ).json()
    await jobs._fire_for_plant(session, fixtures["plant"], datetime.now(UTC))
    rungs = (
        await session.scalars(
            select(Escalation)
            .where(Escalation.downtime_event_id == uuid.UUID(stop["id"]))
            .order_by(Escalation.level)
        )
    ).all()
    # No WhatsApp, SMS or push configured in tests: honest failures, not "sent".
    assert [r.status for r in rungs] == [
        EscalationStatus.FAILED, EscalationStatus.FAILED, EscalationStatus.PENDING
    ]


# ---------------------------------------------------------------------------
# Actions engine
# ---------------------------------------------------------------------------


async def test_an_auto_action_gets_an_owner_a_deadline_and_an_rca_request(
    api, session, fixtures
):
    plant = fixtures["plant"]
    session.add(
        AlertRecipient(plant_id=plant.id, name="Suresh K.", role_tag="maintenance", escalation_level=2)
    )
    await session.flush()
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    started = datetime.now(UTC) - timedelta(minutes=50)
    stop = (
        await api["client"].post(
            "/api/v1/downtime/start",
            headers=api["as"]("supervisor"),
            json={
                "station_id": str(fixtures["station"].id),
                "reason_code_id": str(fixtures["reasons"]["TOOL_BREAKAGE"].id),
                "started_at": started.isoformat(),
            },
        )
    ).json()
    await api["client"].post(
        f"/api/v1/downtime/{stop['id']}/close", headers=api["as"]("supervisor"), json={}
    )
    await api["client"].post(
        "/api/v1/production",
        headers=api["as"]("supervisor"),
        json={"station_id": str(fixtures["station"].id), "count": 10},
    )
    closed = await api["client"].post(
        f"/api/v1/shifts/{shift['id']}/close", headers=api["as"]("owner"), json={"override_reason": "test"}
    )
    assert closed.status_code == 200, closed.text

    action = await session.scalar(select(Action).where(Action.plant_id == plant.id))
    assert action.owner_name == "Suresh K."
    assert action.due_date == date.fromisoformat(shift["business_date"]) + timedelta(days=3)
    rca = await session.scalar(select(RootCauseAnalysis).where(RootCauseAnalysis.action_id == action.id))
    assert rca.assigned_team == "maintenance"
    assert await session.scalar(
        select(func.count(Notification.id)).where(Notification.kind == "rca_assignment")
    ) == 1
    assert await session.scalar(
        select(func.count(Notification.id)).where(Notification.kind == "shift_report")
    ) >= 1


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


def _xlsx(headers, rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h) for h in headers])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def _upload(api, code, headers, rows):
    preview = await api["client"].post(
        f"/api/v1/onboarding/upload/{code}",
        headers=api["as"]("owner"),
        files={"file": (f"{code}.xlsx", _xlsx(headers, rows), "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["errors"] == [], preview.json()["errors"]
    confirmed = await api["client"].post(
        "/api/v1/onboarding/confirm",
        headers=api["as"]("owner"),
        json={"upload_id": preview.json()["upload_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text


async def test_t4_derives_rates_with_the_spec_formulas_and_loads_margin_bands(
    api, session, fixtures
):
    await _upload(
        api,
        "T4",
        ["Scope", "Reference", "Monthly depreciation", "Monthly power", "Monthly operator wages",
         "Monthly maintenance", "Monthly overhead share", "Available hours", "Monthly wage bill",
         "Supervisor share", "Worked hours", "Band L", "Band M", "Band H"],
        [
            # §4.2's worked example: ≈₹1.25L a month over 400 hours ≈ ₹310/hr.
            {"Scope": "cost_family", "Reference": "Blended-CNC", "Monthly depreciation": 29000,
             "Monthly power": 18000, "Monthly operator wages": 28000, "Monthly maintenance": 10000,
             "Monthly overhead share": 40000, "Available hours": 400},
            {"Scope": "section", "Reference": "assembly", "Monthly wage bill": 250000,
             "Supervisor share": 38000, "Worked hours": 1600},
            {"Scope": "margin_band", "Reference": "Brackets", "Band L": 15, "Band M": 35, "Band H": 60},
        ],
    )
    blended = await session.scalar(
        select(CostRate).where(CostRate.plant_id == fixtures["plant"].id, CostRate.scope == "cost_family",
                               CostRate.derivation.is_not(None))
    )
    assert blended.machine_rate == rupees("312.50")
    assert blended.derivation["machine"]["formula"] == "blended_machine_rate"
    labour = await session.scalar(
        select(CostRate).where(CostRate.plant_id == fixtures["plant"].id, CostRate.scope_ref == "assembly")
    )
    assert labour.labour_rate == rupees(180)
    bands = await session.scalar(
        select(func.count(MarginBandValue.id)).where(MarginBandValue.plant_id == fixtures["plant"].id)
    )
    assert bands == 3


async def test_t4_refuses_a_derived_rate_without_hours(api, fixtures):
    preview = await api["client"].post(
        "/api/v1/onboarding/upload/T4",
        headers=api["as"]("owner"),
        files={"file": ("T4.xlsx", _xlsx(["Scope", "Reference", "Monthly power"],
                                         [{"Scope": "cost_family", "Reference": "X", "Monthly power": 1}]),
                        "application/octet-stream")},
    )
    body = preview.json()
    assert body["upload_id"] is None
    assert body["errors"][0]["column"] == "Available hours"


async def test_line_targets_per_shift_reach_the_shift_report(api, session, fixtures):
    await _upload(
        api,
        "T7",
        ["Kind", "Name", "Line", "Shift", "Target parts"],
        [{"Kind": "target", "Name": "Line 1", "Line": "Line 1", "Shift": "Shift A", "Target parts": 380}],
    )
    await _upload(  # re-uploading replaces today's target instead of stacking a second
        api,
        "T7",
        ["Kind", "Name", "Line", "Shift", "Target parts"],
        [{"Kind": "target", "Name": "Line 1", "Line": "Line 1", "Shift": "Shift A", "Target parts": 400}],
    )
    assert await session.scalar(
        select(func.count(ProductionTarget.id)).where(ProductionTarget.plant_id == fixtures["plant"].id)
    ) == 1
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    kpis = (
        await api["client"].get(f"/api/v1/reports/kpis/{shift['id']}", headers=api["as"]("owner"))
    ).json()
    assert kpis["target"] == 400


async def test_default_codes_load_once(api, session, fixtures):
    first = (await api["client"].post("/api/v1/onboarding/defaults", headers=api["as"]("owner"))).json()
    assert first["reason_codes_added"] == 18 - 4  # the fixture plant already has four of them
    second = (await api["client"].post("/api/v1/onboarding/defaults", headers=api["as"]("owner"))).json()
    assert second == {"reason_codes_added": 0, "defect_codes_added": 0}
    no_operator = await session.scalar(
        select(ReasonCode).where(ReasonCode.plant_id == fixtures["plant"].id, ReasonCode.code == "NO_OPERATOR")
    )
    assert no_operator.recoverability.value == "R"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def test_business_dates_are_the_plants_not_utcs():
    from types import SimpleNamespace

    plant = SimpleNamespace(business_date_from_shift_start=True, timezone="Asia/Kolkata")
    shift = SimpleNamespace(crosses_midnight=False)
    # 01:00 on the 13th in Hosur is 19:30 on the 12th in UTC.
    assert resolve_business_date(plant, shift, datetime(2026, 9, 12, 19, 30, tzinfo=UTC)) == date(
        2026, 9, 13
    )


async def test_the_meeting_is_locked_only_when_every_shift_is_closed(api, session, fixtures):
    from app.models import Shift

    shift_b = Shift(
        plant_id=fixtures["plant"].id, name="Shift B", start_time=datetime(2000, 1, 1, 15, 30).time(),
        end_time=datetime(2000, 1, 1, 23, 59).time(), break_minutes=30, display_order=2,
    )
    session.add(shift_b)
    await session.flush()
    first = await _start_shift(api, fixtures)
    day = first["business_date"]
    second = await shift_service.start_shift(
        session, plant=fixtures["plant"], shift=shift_b, supervisor_id=None
    )
    await shift_service.close_shift(
        session, shift_session=await session.get(ShiftSession, uuid.UUID(first["id"])), closed_by_id=None
    )
    pack = (await api["client"].get(f"/api/v1/reports/meeting?business_date={day}", headers=api["as"]("owner"))).json()
    assert pack["locked"] is False

    await shift_service.close_shift(session, shift_session=second, closed_by_id=None)
    pack = (await api["client"].get(f"/api/v1/reports/meeting?business_date={day}", headers=api["as"]("owner"))).json()
    assert pack["locked"] is True


@pytest.mark.parametrize("hours", [0])
async def test_sync_holds_cannot_be_decided_twice(api, session, fixtures, hours):
    hold = SyncHold(
        plant_id=fixtures["plant"].id, client_ref=_ref(), kind="production",
        occurred_at=datetime.now(UTC), payload={}, reason="test",
    )
    session.add(hold)
    await session.flush()
    first = await api["client"].post(
        f"/api/v1/sync/holds/{hold.id}/discard", headers=api["as"]("owner"), json={"note": "Duplicate of a paper entry"}
    )
    assert first.status_code == 200
    second = await api["client"].post(
        f"/api/v1/sync/holds/{hold.id}/discard", headers=api["as"]("owner"), json={"note": "again"}
    )
    assert second.status_code == 409


def test_the_same_person_listed_twice_is_told_once():
    """A T5 owner row with a phone and the owner's email login are one person."""
    login = uuid.uuid4()
    merged = notifications.merge_recipients(
        [notifications.Recipient(name="Mr. Kumaran", number="+919828003233", user_id=login)],
        [notifications.Recipient(name="Mr. Kumaran", number=None, user_id=login)],
        [notifications.Recipient(name="Kumaran (WhatsApp)", number="98280 03233")],
    )
    assert len(merged) == 1
    assert merged[0].user_id == login and merged[0].number == "+919828003233"
