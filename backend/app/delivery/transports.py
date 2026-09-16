"""The three ways a message leaves the building: WhatsApp, SMS, browser push.

Each transport does one HTTP call and reports what happened. None of them
retries — the outbox does, so a failed attempt is a row the owner can see, not
a loop in a process that might be restarted halfway through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.delivery import webpush


@dataclass(slots=True)
class SendResult:
    ok: bool
    provider_id: str | None = None
    response: dict = field(default_factory=dict)
    error: str | None = None
    #: False when retrying cannot help: a bad number, a dead push subscription.
    retryable: bool = True


def digits(number: str | None) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit())


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


class WatiTransport:
    """WhatsApp Business API through WATI (§16.4)."""

    name = "whatsapp"

    @property
    def enabled(self) -> bool:
        return settings.wati_enabled

    @property
    def configured(self) -> bool:
        return bool(settings.wati_base_url and settings.wati_api_token)

    async def send(self, *, number: str, body: str, kind: str) -> SendResult:
        base = settings.wati_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.wati_api_token}"}
        to = digits(number)
        async with _client() as client:
            if settings.wati_use_templates:
                response = await client.post(
                    f"{base}/api/v1/sendTemplateMessage",
                    params={"whatsappNumber": to},
                    headers=headers,
                    json={
                        "template_name": f"{settings.wati_template_prefix}{kind}",
                        "broadcast_name": f"{settings.wati_template_prefix}{kind}",
                        "parameters": [{"name": "message", "value": body}],
                    },
                )
            else:
                response = await client.post(
                    f"{base}/api/v1/sendSessionMessage/{to}",
                    params={"messageText": body},
                    headers=headers,
                )
        return _result_from(response, ok_field="result")


class Msg91Transport:
    """SMS through MSG91's Flow API — the fallback while WhatsApp is pending (§12 risk 6)."""

    name = "sms"
    url = "https://control.msg91.com/api/v5/flow"

    @property
    def enabled(self) -> bool:
        return settings.msg91_enabled

    @property
    def configured(self) -> bool:
        return bool(settings.msg91_auth_key and settings.msg91_template_id)

    async def send(self, *, number: str, body: str, kind: str) -> SendResult:
        async with _client() as client:
            response = await client.post(
                self.url,
                headers={"authkey": settings.msg91_auth_key, "accept": "application/json"},
                json={
                    "template_id": settings.msg91_template_id,
                    "short_url": "0",
                    # SMS is 160 characters a part; the full text is in the app.
                    "recipients": [{"mobiles": digits(number), "message": body[:300]}],
                },
            )
        return _result_from(response, ok_field="type", ok_value="success")


class WebPushTransport:
    name = "push"

    @property
    def enabled(self) -> bool:
        return settings.webpush_enabled

    @property
    def configured(self) -> bool:
        return bool(settings.vapid_private_key)

    def public_key(self) -> str | None:
        if not (self.enabled and self.configured):
            return None
        return webpush.application_server_key(webpush.load_private_key(settings.vapid_private_key))

    async def send(
        self, *, endpoint: str, p256dh: str, auth: str, title: str, body: str, url: str | None, tag: str
    ) -> SendResult:
        key = webpush.load_private_key(settings.vapid_private_key)
        payload = json.dumps({"title": title, "body": body, "url": url or "/", "tag": tag}).encode()
        headers = {
            **webpush.vapid_headers(endpoint, key, settings.vapid_subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": "3600",
            "Urgency": "high",
        }
        async with _client() as client:
            response = await client.post(
                endpoint, content=webpush.encrypt(payload, p256dh=p256dh, auth=auth), headers=headers
            )
        if response.status_code in (404, 410):
            # The browser unsubscribed or the subscription expired.
            return SendResult(
                ok=False, error=f"Subscription gone ({response.status_code})", retryable=False
            )
        if response.status_code >= 400:
            return SendResult(
                ok=False,
                error=f"Push service returned {response.status_code}",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        return SendResult(ok=True, provider_id=response.headers.get("location"))


def _result_from(response: httpx.Response, *, ok_field: str, ok_value=True) -> SendResult:
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text[:500]}
    if response.status_code >= 400:
        return SendResult(
            ok=False,
            response=body,
            error=f"Provider returned {response.status_code}",
            # 4xx other than rate limiting means the request itself is wrong.
            retryable=response.status_code >= 500 or response.status_code == 429,
        )
    if not isinstance(body, dict):
        body = {"body": body}
    ok = body.get(ok_field) == ok_value
    provider_id = body.get("id") or body.get("request_id") or body.get("message")
    return SendResult(
        ok=ok,
        response=body,
        provider_id=str(provider_id)[:128] if ok and provider_id else None,
        error=None if ok else f"Provider did not accept the message: {str(body)[:200]}",
    )


whatsapp = WatiTransport()
sms = Msg91Transport()
push = WebPushTransport()


def channel_status() -> dict:
    return {
        transport.name: {"enabled": transport.enabled, "configured": transport.configured}
        for transport in (whatsapp, sms, push)
    }


