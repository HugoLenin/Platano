"""Short-lived signed report links.

DECISION: detached HMAC-SHA256 tokens minted by us, not Supabase Storage
signed URLs. See docs/DECISIONS.md for the trade-off. In short: a storage
signed URL points at one immutable blob, so it cannot carry a per-recipient
scope and cannot be revoked before it expires. Our token carries `scope`, and
the viewer renders the report for that scope at request time - a family link
never even transmits the operator-only fields.

Token layout (URL-safe, no padding, no dependencies):

    v1.<b64url(payload_json)>.<b64url(hmac_sha256(secret, "v1." + b64payload))>

The signature covers the version prefix too, so we can rotate the scheme
without creating a downgrade path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

VERSION = "v1"

SCOPE_FAMILY = "family"
SCOPE_OPERATOR = "operator"
VALID_SCOPES = (SCOPE_FAMILY, SCOPE_OPERATOR)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class LinkClaims:
    report_id: str
    contact_id: str
    scope: str
    exp: int
    nonce: str

    @property
    def expired(self) -> bool:
        return time.time() > self.exp

    def as_dict(self) -> dict:
        return {
            "r": self.report_id,
            "c": self.contact_id,
            "s": self.scope,
            "exp": self.exp,
            "n": self.nonce,
        }


class BadToken(Exception):
    pass


def mint(
    secret: str,
    *,
    report_id: str,
    contact_id: str,
    scope: str,
    ttl_s: int,
) -> tuple[str, LinkClaims]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if not secret:
        raise ValueError("ELB_REPORT_SIGNING_SECRET is not set")
    claims = LinkClaims(
        report_id=report_id,
        contact_id=contact_id,
        scope=scope,
        exp=int(time.time()) + int(ttl_s),
        nonce=secrets.token_urlsafe(8),
    )
    body = _b64e(json.dumps(claims.as_dict(), separators=(",", ":"), sort_keys=True).encode())
    signed = f"{VERSION}.{body}"
    sig = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).digest()
    return f"{signed}.{_b64e(sig)}", claims


def verify(secret: str, token: str) -> LinkClaims:
    """Raise BadToken unless the signature is valid and the token is unexpired.

    Revocation is intentionally NOT handled here - the caller checks
    report_links.revoked_at in the database. Keeping revocation out of the
    crypto means we can kill a link instantly without rotating the secret.
    """
    try:
        version, body, sig = token.split(".")
    except ValueError as exc:
        raise BadToken("malformed token") from exc
    if version != VERSION:
        raise BadToken(f"unsupported version {version!r}")

    expected = hmac.new(secret.encode(), f"{version}.{body}".encode(), hashlib.sha256).digest()
    try:
        given = _b64d(sig)
    except Exception as exc:
        raise BadToken("malformed signature") from exc
    if not hmac.compare_digest(expected, given):
        raise BadToken("bad signature")

    try:
        data = json.loads(_b64d(body))
        claims = LinkClaims(
            report_id=data["r"],
            contact_id=data["c"],
            scope=data["s"],
            exp=int(data["exp"]),
            nonce=data["n"],
        )
    except Exception as exc:
        raise BadToken("malformed payload") from exc

    if claims.scope not in VALID_SCOPES:
        raise BadToken("unknown scope")
    if claims.expired:
        raise BadToken("expired")
    return claims


def link_for(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/r/{token}"
