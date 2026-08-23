"""Support-network notification.

The agent decides WHEN and WHAT; the Next.js app decides HOW to deliver. Today
that means one channel - email over SMTP - but the split is still worth keeping:
the transport lives next to the templates that render it, and swapping or adding
a channel never touches this file.

Signed links are minted HERE, not in the web app, so the signing secret and the
scoping decision live next to the code that knows what is in the report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .signing import SCOPE_FAMILY, link_for, mint

logger = logging.getLogger("elb.notify")

KIND_EARLY = "early"
KIND_FINAL = "final"


@dataclass
class NotifyTarget:
    contact_id: str
    name: str
    phone_e164: str = ""
    email: str = ""
    locale: str = "es"
    relationship: str = ""


@dataclass
class NotifyPayload:
    call_id: str
    report_id: str
    kind: str
    caller_name: str
    emergency_type: str
    location: str
    severity: str
    summary: str
    lat: float | None
    lon: float | None
    targets: list[NotifyTarget]


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_links(self, payload: NotifyPayload) -> dict[str, dict[str, Any]]:
        """One distinct, scoped, short-lived link per contact.

        Per-contact rather than one shared link so that a forwarded link is
        attributable, and so revoking one recipient does not break the others.
        """
        ttl = self.s.link_ttl_early_s if payload.kind == KIND_EARLY else self.s.link_ttl_final_s
        links: dict[str, dict[str, Any]] = {}
        for t in payload.targets:
            try:
                token, claims = mint(
                    self.s.report_signing_secret,
                    report_id=payload.report_id,
                    contact_id=t.contact_id,
                    scope=SCOPE_FAMILY,
                    ttl_s=ttl,
                )
            except ValueError as exc:
                logger.error("cannot mint link: %s", exc)
                continue
            links[t.contact_id] = {
                "url": link_for(self.s.web_base_url, token),
                "token": token,
                "expires_at": claims.exp,
                "scope": claims.scope,
            }
        return links

    async def send(self, payload: NotifyPayload) -> dict[str, Any]:
        endpoint = self.s.notify_endpoint or f"{self.s.web_base_url}/api/notify"
        links = self.build_links(payload)
        body = {
            "call_id": payload.call_id,
            "report_id": payload.report_id,
            "kind": payload.kind,
            "caller_name": payload.caller_name,
            "emergency_type": payload.emergency_type,
            "location": payload.location,
            "severity": payload.severity,
            "summary": payload.summary,
            "lat": payload.lat,
            "lon": payload.lon,
            "contacts": [
                {
                    "id": t.contact_id,
                    "name": t.name,
                    "phone_e164": t.phone_e164,
                    "email": t.email,
                    "locale": t.locale,
                    "relationship": t.relationship,
                    "link": links.get(t.contact_id, {}).get("url", ""),
                    "link_expires_at": links.get(t.contact_id, {}).get("expires_at"),
                }
                for t in payload.targets
            ],
        }
        headers = {"content-type": "application/json"}
        if self.s.internal_api_token:
            headers["x-elb-internal-token"] = self.s.internal_api_token

        try:
            resp = await self._http().post(endpoint, json=body, headers=headers)
            if resp.status_code >= 300:
                logger.error("notify -> %s %s", resp.status_code, resp.text[:400])
                return {"ok": False, "status": resp.status_code, "links": links}
            logger.info(
                "notify %s dispatched to %d contact(s)", payload.kind, len(payload.targets)
            )
            return {"ok": True, "result": resp.json(), "links": links}
        except Exception as exc:
            logger.error("notify failed: %s", exc)
            return {"ok": False, "error": str(exc), "links": links}
