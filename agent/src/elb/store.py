"""Supabase persistence.

Storage only. Supabase is never on the signalling path - call setup and all
realtime fan-out go through LiveKit. This module talks to PostgREST directly
over httpx with the service-role key, which keeps the dependency surface small
and the failure behaviour obvious.

Every method is best-effort: a storage outage must not end a live emergency
call, so failures are logged and swallowed, and the agent keeps a local copy of
anything it would have written.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger("elb.store")


class Store:
    def __init__(self, settings: Settings):
        self.s = settings
        self.enabled = bool(settings.supabase_url and settings.supabase_service_key)
        self._client: httpx.AsyncClient | None = None
        self._spool = settings.reports_dir / "spool"
        if not self.enabled:
            logger.warning("Supabase not configured - persisting to %s instead", self._spool)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.s.supabase_url.rstrip("/") + "/rest/v1",
                headers={
                    "apikey": self.s.supabase_service_key,
                    "Authorization": f"Bearer {self.s.supabase_service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                timeout=httpx.Timeout(8.0, connect=4.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _spool_write(self, table: str, rows: Any) -> None:
        try:
            self._spool.mkdir(parents=True, exist_ok=True)
            with (self._spool / f"{table}.jsonl").open("a", encoding="utf-8") as fh:
                for row in rows if isinstance(rows, list) else [rows]:
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # pragma: no cover
            logger.error("spool write failed: %s", exc)

    async def insert(self, table: str, rows: Any) -> list[dict] | None:
        if not self.enabled:
            self._spool_write(table, rows)
            return None
        try:
            resp = await self._http().post(f"/{table}", json=rows)
            if resp.status_code >= 300:
                logger.error("insert %s -> %s %s", table, resp.status_code, resp.text[:300])
                self._spool_write(table, rows)
                return None
            return resp.json()
        except Exception as exc:
            logger.error("insert %s failed: %s", table, exc)
            self._spool_write(table, rows)
            return None

    async def upsert(self, table: str, rows: Any, on_conflict: str = "id") -> list[dict] | None:
        if not self.enabled:
            self._spool_write(table, rows)
            return None
        try:
            resp = await self._http().post(
                f"/{table}",
                json=rows,
                params={"on_conflict": on_conflict},
                headers={"Prefer": "return=representation,resolution=merge-duplicates"},
            )
            if resp.status_code >= 300:
                logger.error("upsert %s -> %s %s", table, resp.status_code, resp.text[:300])
                self._spool_write(table, rows)
                return None
            return resp.json()
        except Exception as exc:
            logger.error("upsert %s failed: %s", table, exc)
            self._spool_write(table, rows)
            return None

    async def patch(self, table: str, match: dict[str, str], values: dict) -> None:
        if not self.enabled:
            self._spool_write(f"{table}.patch", {"match": match, "values": values})
            return
        params = {k: f"eq.{v}" for k, v in match.items()}
        try:
            resp = await self._http().patch(f"/{table}", params=params, json=values)
            if resp.status_code >= 300:
                logger.error("patch %s -> %s %s", table, resp.status_code, resp.text[:300])
        except Exception as exc:
            logger.error("patch %s failed: %s", table, exc)

    async def select(self, table: str, params: dict[str, str]) -> list[dict]:
        if not self.enabled:
            return []
        try:
            resp = await self._http().get(f"/{table}", params=params)
            if resp.status_code >= 300:
                logger.error("select %s -> %s %s", table, resp.status_code, resp.text[:300])
                return []
            return resp.json()
        except Exception as exc:
            logger.error("select %s failed: %s", table, exc)
            return []

    # ------------------------------------------------------------ domain API
    async def create_call(self, row: dict) -> None:
        await self.upsert("calls", row, on_conflict="id")

    async def update_call(self, call_id: str, values: dict) -> None:
        await self.patch("calls", {"id": call_id}, values)

    async def add_transcripts(self, rows: list[dict]) -> None:
        if rows:
            await self.insert("transcripts", rows)

    async def add_events(self, rows: list[dict]) -> None:
        if rows:
            await self.insert("events", rows)

    async def add_metrics(self, rows: list[dict]) -> None:
        if rows:
            await self.insert("metrics", rows)

    async def save_report(self, row: dict) -> None:
        await self.upsert("reports", row, on_conflict="id")

    async def trusted_contacts(self, user_id: str) -> list[dict]:
        if not user_id:
            return []
        return await self.select(
            "trusted_contacts",
            {
                "user_id": f"eq.{user_id}",
                "active": "eq.true",
                "select": "id,name,phone_e164,email,relationship,locale,whatsapp_opt_in_at,push_token,notify_early,notify_final",
                "order": "priority.asc",
            },
        )

    async def caller_profile(self, user_id: str) -> dict | None:
        rows = await self.select(
            "profiles", {"id": f"eq.{user_id}", "select": "id,display_name,preferred_language,phone_e164"}
        )
        return rows[0] if rows else None

    async def record_link(self, row: dict) -> None:
        await self.insert("report_links", row)

    # ------------------------------------------------------------- local copy
    def write_local_report(self, filename: str, content: str) -> Path:
        path = self.s.reports_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
