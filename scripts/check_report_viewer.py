"""Report viewer check - proves the scope split over the wire, not in a unit test.

The interop between `agent/src/elb/signing.py` and `web/lib/signing.ts` is
covered by tests. What tests cannot cover is the property the whole design rests
on: that a family-scope link never puts operator text *in the HTTP response*.
That claim is only true if the real server, reading the real database, resolves
the scope before rendering - so this checks it against both.

It seeds a report whose two scopes contain deliberately distinct markers, then:

    1. opens the family link and asserts the operator marker is ABSENT
    2. opens the operator link and asserts its marker is present
    3. checks the download route independently (a download URL is shareable on
       its own, so it must not inherit trust from a page render)
    4. forges three attacks and asserts all three are refused

The forgeries are done properly. The token is `v1.<b64url(payload)>.<b64url(sig)>`
and the payload uses short keys, so the string "family" never appears literally:
a `str.replace` on the token forges nothing and would pass a test vacuously. Each
attack decodes the payload, edits the field, re-encodes and keeps the old
signature.

Prerequisites: schema applied (see RUNBOOK.md section 3), web dev server up.

Usage:
    py -3.13 scripts/check_report_viewer.py

Windows console: set PYTHONIOENCODING=utf-8 first, the pages contain accents.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent" / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / "agent" / ".env", override=True)

from elb.signing import SCOPE_FAMILY, SCOPE_OPERATOR, _b64d, _b64e, mint  # noqa: E402

BASE = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SECRET = os.getenv("ELB_REPORT_SIGNING_SECRET", "")
WEB = os.getenv("ELB_WEB_BASE_URL", "http://localhost:3000").rstrip("/")

# Invented so they can be searched for literally in the response body.
FAM_MARK = "FAMILY-SCOPE-MARKER-7f3a"
OP_MARK = "OPERATOR-CLINICAL-MARKER-9b2e"

DENIED = "Enlace no válido"
EXPIRED = "expiró"

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("[" + ("  OK  " if ok else " FAIL ") + "] " + label.ljust(38) + " " + detail)


def sb(method, path, params=None, body=None, prefer=None):
    url = BASE + "/rest/v1/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"apikey": KEY, "Authorization": "Bearer " + KEY,
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, {"message": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:
        return 0, {"message": str(exc)}


def get(path: str, token: str):
    try:
        with urllib.request.urlopen(WEB + path + urllib.parse.quote(token, safe=""),
                                    timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def retoken(token: str, **edits) -> str:
    """Edit a signed payload in place and keep the original signature."""
    version, body_b64, sig_b64 = token.split(".")
    payload = json.loads(_b64d(body_b64))
    payload.update(edits)
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return version + "." + body + "." + sig_b64


def run_checks(report_id: str, contact_id: str, now, link_row) -> None:
    fam, _ = mint(SECRET, report_id=report_id, contact_id=contact_id,
                  scope=SCOPE_FAMILY, ttl_s=3600)
    op, _ = mint(SECRET, report_id=report_id, contact_id=contact_id,
                 scope=SCOPE_OPERATOR, ttl_s=3600)

    # --- the property the whole design rests on --------------------------
    print("-" * 78)
    status, html = get("/r/", fam)
    check("GET /r/<family>", status == 200, "HTTP " + str(status))
    check("renders the family text", FAM_MARK in html)
    check("operator text NOT in response", OP_MARK not in html,
          "this is the claim in D3")
    check("labels the scope", "Contacto de confianza" in html)
    check("map card present", "openstreetmap.org" in html)

    status, html = get("/r/", op)
    check("GET /r/<operator>", status == 200 and OP_MARK in html, "HTTP " + str(status))
    check("labels professional use", "Uso profesional" in html)

    # The download route re-verifies on its own, so it gets its own assertions.
    status, txt = get("/api/report/", fam)
    check("download honours family scope",
          status == 200 and FAM_MARK in txt and OP_MARK not in txt, "HTTP " + str(status))

    # --- forgeries -------------------------------------------------------
    print("-" * 78)
    check("'family' is not literal in the token", SCOPE_FAMILY not in fam,
          "so str.replace forges nothing")

    # 1. escalate the scope, keep the signature. The payload key is `s`.
    status, html = get("/r/", retoken(fam, s=SCOPE_OPERATOR))
    check("scope escalation refused", OP_MARK not in html and DENIED in html,
          "family -> operator, HTTP " + str(status))

    # 2. re-sign the whole thing with a secret we made up.
    forged, _ = mint("wrong-secret" * 4, report_id=report_id, contact_id=contact_id,
                     scope=SCOPE_OPERATOR, ttl_s=3600)
    status, html = get("/r/", forged)
    check("re-signed with wrong secret refused", OP_MARK not in html and DENIED in html,
          "HTTP " + str(status))

    # 3. push `exp` into the future on an already-expired token.
    expired, _ = mint(SECRET, report_id=report_id, contact_id=contact_id,
                      scope=SCOPE_FAMILY, ttl_s=-60)
    status, html = get("/r/", retoken(expired, exp=int((now + timedelta(days=30)).timestamp())))
    check("extended expiry refused", FAM_MARK not in html and DENIED in html,
          "HTTP " + str(status))

    status, html = get("/r/", expired)
    check("genuinely expired -> expiry page", EXPIRED in html and FAM_MARK not in html,
          "HTTP " + str(status))

    status, txt = get("/api/report/", retoken(fam, s=SCOPE_OPERATOR))
    check("download refuses a forgery too", status in (403, 410) and OP_MARK not in txt,
          "HTTP " + str(status))

    # --- the open audit --------------------------------------------------
    print("-" * 78)
    if link_row is not None:
        _, rows = sb("GET", "report_links",
                     params={"id": "eq." + str(link_row), "select": "open_count"})
        n = rows[0]["open_count"] if isinstance(rows, list) and rows else 0
        check("opens are audited", bool(n), "open_count=" + str(n))


def cleanup(report_id: str) -> None:
    sb("DELETE", "report_links", params={"report_id": "eq." + report_id})
    sb("DELETE", "reports", params={"id": "eq." + report_id})
    _, rows = sb("GET", "reports", params={"id": "eq." + report_id, "select": "id"})
    check("cleanup", isinstance(rows, list) and not rows)


def main() -> int:
    if not (BASE and KEY and SECRET):
        print("SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and "
              "ELB_REPORT_SIGNING_SECRET must be set in agent/.env")
        return 2

    report_id = "elb_viewer_" + uuid.uuid4().hex[:8]
    contact_id = "viewer-check"
    now = datetime.now(timezone.utc)

    print("=" * 78)
    print("  REPORT VIEWER CHECK  ->  " + WEB + "/r/<token>")
    print("=" * 78)

    status, _ = sb("POST", "reports",
                   body={"id": report_id, "call_id": None, "is_final": True,
                         "operator_txt": "OPERATOR REPORT\n" + OP_MARK
                                         + "\nclinical: not breathing",
                         "family_txt": "FAMILY REPORT\n" + FAM_MARK
                                       + "\nno clinical detail",
                         "extraction": {}, "critical_flags": ["NOT BREATHING"],
                         "lat": 4.6533, "lon": -74.0836,
                         "caller_lang": "en", "operator_lang": "es"},
                   prefer="return=representation,resolution=merge-duplicates",
                   params={"on_conflict": "id"})
    check("seed report", 200 <= status < 300, report_id)
    if not 200 <= status < 300:
        print("\nCannot seed - is the schema applied? py -3.13 scripts/check_supabase.py")
        return 1

    status, links = sb("POST", "report_links",
                       body={"report_id": report_id, "contact_id": contact_id,
                             "scope": "family", "kind": "final",
                             "expires_at": (now + timedelta(hours=2)).isoformat()},
                       prefer="return=representation")
    check("seed report_link", 200 <= status < 300)
    link_row = links[0]["id"] if isinstance(links, list) and links else None

    # Cleanup lives in `finally`, not at the end of the happy path. A run that
    # died mid-assertion used to leave its seeded report behind, and then the
    # next run inherits rows it did not create.
    try:
        run_checks(report_id, contact_id, now, link_row)
    finally:
        print("-" * 78)
        cleanup(report_id)

    print("=" * 78)
    ok = all(results)
    if ok:
        print("  VIEWER WORKS AND THE SCOPE SPLIT HOLDS OVER THE WIRE.")
    else:
        print("  FAILURES ABOVE. A failing scope assertion is a data leak, not a bug.")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
