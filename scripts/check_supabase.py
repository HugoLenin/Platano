"""Supabase preflight - turns a silent storage failure into a loud one.

`Store` swallows every database error on purpose: a storage outage must never
end a live emergency call. The price is that a wrong key, an unapplied schema
and an RLS policy that blocks this key all look identical from the outside. The
stack runs, the call works, the interpretation is correct, and every report
quietly lands in `reports/spool/` instead of the database - which is exactly how
`/r/<token>` ends up 404-ing for a call that by every other measure succeeded.

So this checks the three failure modes separately and says which one it is:

    1. auth       - does the key authenticate against PostgREST at all
    2. schema     - does every table the code reads or writes actually exist
    3. permission - can this key really read AND write them

The write test is a full round trip through the shape the code depends on: a
call with its transcripts, events and metrics, a report, a report link that
then gets its open counted, and two identical deliveries that must collapse
into one row. It ends by deleting the call and checking the cascade took the
children with it, so it cleans up after itself.

Usage:
    py -3.13 scripts/check_supabase.py            # reads agent/.env
    py -3.13 scripts/check_supabase.py --web      # reads web/.env.local

Exit code is 0 only when the storage path is fully usable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]

# Every table the agent or the web app touches, and why it has to be there.
TABLES = {
    "profiles": "caller identity, read at call start",
    "trusted_contacts": "who gets notified - drives the whole notify path",
    "calls": "one row per call",
    "transcripts": "what was said, per turn",
    "events": "state transitions",
    "metrics": "latency samples that feed report section 6",
    "reports": "both scopes - this is what /r/<token> reads",
    "report_links": "audit + revocation for each handed-out link",
    "deliveries": "delivery log and the idempotency guard",
}
VIEWS = {"call_overview": "dispatcher list view"}

SEED_PROFILE = "11111111-1111-1111-1111-111111111111"

OK, BAD = "ok", "bad"


def api(key, base, method, path, params=None, body=None, prefer=None):
    url = base + "/rest/v1/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"message": raw[:300]}
    except Exception as exc:  # network, DNS, TLS
        return 0, {"message": type(exc).__name__ + ": " + str(exc)}


def diagnose(status, payload):
    """Map a PostgREST failure onto the actual thing that is wrong."""
    if 200 <= status < 300:
        return OK, ""
    p = payload if isinstance(payload, dict) else {}
    code = p.get("code", "")
    msg = p.get("message", "")
    if code == "PGRST205" or status == 404:
        return BAD, "table missing -> apply supabase/schema.sql"
    if status in (401, 403) or code in ("42501", "PGRST301"):
        return BAD, "permission denied (" + str(code or status) + ") -> publishable key?"
    if status == 0:
        return BAD, "unreachable: " + msg
    return BAD, ("HTTP " + str(status) + " " + str(code) + " " + str(msg)).strip()[:150]


def row(name, verdict, detail=""):
    mark = "  OK  " if verdict == OK else " FAIL "
    print("[" + mark + "] " + name.ljust(22) + " " + detail)
    return verdict == OK


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", action="store_true", help="read web/.env.local instead of agent/.env")
    args = ap.parse_args()

    env = REPO / ("web/.env.local" if args.web else "agent/.env")
    load_dotenv(env, override=True)

    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    if base.endswith("/rest/v1"):
        base = base[: -len("/rest/v1")]
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    print("=" * 78)
    print("  SUPABASE PREFLIGHT  (" + str(env.relative_to(REPO)) + ")")
    print("=" * 78)

    if not base or not key:
        print("[ FAIL ] SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set in " + str(env))
        return 2

    publishable = key.startswith("sb_publishable_")
    print("  url : " + base)
    print("  key : " + key[:14] + "... (" + ("PUBLISHABLE / anon" if publishable else "secret / service_role") + ")")
    if publishable:
        print("  NOTE: a publishable key is the `anon` role and IS subject to RLS.")
        print("        Every path here is server-side and assumes the service role.")
    print("-" * 78)

    ok = True

    # 1. auth ---------------------------------------------------------------
    status, payload = api(key, base, "GET", "")
    verdict, detail = diagnose(status, payload)
    ok = row("auth", verdict, detail) and ok
    if status == 0 or status in (401, 403):
        print("")
        print("Stopped: the key does not authenticate, so nothing below can pass.")
        return 1

    # 2. schema + read ------------------------------------------------------
    print("-" * 78)
    wanted = dict(TABLES)
    wanted.update(VIEWS)
    missing = []
    for table, why in wanted.items():
        status, payload = api(key, base, "GET", table, params={"select": "*", "limit": 1})
        verdict, detail = diagnose(status, payload)
        if verdict == BAD:
            missing.append(table)
        ok = row("read " + table, verdict, detail or why) and ok

    if missing:
        print("-" * 78)
        print("")
        print(str(len(missing)) + " object(s) unusable: " + ", ".join(missing))
        print("Apply the schema first, then re-run:")
        print("  Supabase dashboard -> SQL Editor -> paste supabase/schema.sql -> Run")
        return 1

    # 3. write round trip ---------------------------------------------------
    print("-" * 78)
    call_id = str(uuid.uuid4())
    report_id = "elb_preflight_" + uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    contact_id = "preflight-contact"

    def write(label, method, path, body, prefer=None, params=None):
        nonlocal ok
        status, payload = api(key, base, method, path, params=params, body=body, prefer=prefer)
        verdict, detail = diagnose(status, payload)
        ok = row(label, verdict, detail) and ok
        return status, payload

    write("write calls", "POST", "calls",
          {"id": call_id, "room": "preflight", "caller_lang": "en",
           "operator_lang": "es", "status": "active"})
    write("write transcripts", "POST", "transcripts",
          [{"call_id": call_id, "t_offset_ms": 0, "speaker": "caller", "lang": "en",
            "kind": "source", "text": "preflight"}])
    write("write events", "POST", "events",
          [{"call_id": call_id, "type": "preflight", "payload": {"ok": True}}])
    write("write metrics", "POST", "metrics",
          [{"call_id": call_id, "direction": "caller_to_operator",
            "metric": "translate_ms", "value": 1}])
    write("write reports", "POST", "reports",
          {"id": report_id, "call_id": call_id, "is_final": True,
           "operator_txt": "preflight operator", "family_txt": "preflight family",
           "extraction": {}, "critical_flags": []},
          prefer="return=representation,resolution=merge-duplicates",
          params={"on_conflict": "id"})
    _, links = write("write report_links", "POST", "report_links",
                     {"report_id": report_id, "contact_id": contact_id,
                      "scope": "family", "kind": "final",
                      "expires_at": (now + timedelta(hours=1)).isoformat()},
                     prefer="return=representation")

    # The viewer counts the open with a PATCH. If this key can insert but not
    # update, /r/<token> renders and then throws on the audit write.
    link_id = links[0]["id"] if isinstance(links, list) and links else None
    if link_id is not None:
        write("update report_links", "PATCH", "report_links",
              {"opened_at": now.isoformat(), "open_count": 1},
              params={"id": "eq." + str(link_id)})

    # The unique index is what keeps an agent retry from double-notifying a
    # frightened relative. Two identical upserts must collapse into one row.
    for _ in range(2):
        write("upsert deliveries", "POST", "deliveries",
              {"report_id": report_id, "contact_id": contact_id, "kind": "final",
               "channel": "none", "status": "skipped", "error": "preflight"},
              prefer="return=representation,resolution=merge-duplicates",
              params={"on_conflict": "report_id,contact_id,kind,channel"})
    status, payload = api(key, base, "GET", "deliveries",
                          params={"report_id": "eq." + report_id, "select": "id"})
    n = len(payload) if isinstance(payload, list) else -1
    ok = row("idempotency guard", OK if n == 1 else BAD,
             "two identical upserts -> one row" if n == 1
             else "expected 1 delivery row, found " + str(n) + " (unique index missing?)") and ok

    # Read the report back exactly the way /r/<token> does.
    status, payload = api(key, base, "GET", "reports",
                          params={"id": "eq." + report_id,
                                  "select": "id,operator_txt,family_txt,revoked_at"})
    found = isinstance(payload, list) and len(payload) == 1
    ok = row("read back report", OK if found else BAD,
             "the /r/<token> query works" if found
             else "wrote the report but cannot read it back") and ok

    # 4. cascade + cleanup --------------------------------------------------
    print("-" * 78)
    api(key, base, "DELETE", "deliveries", params={"report_id": "eq." + report_id})
    status, _ = api(key, base, "DELETE", "calls", params={"id": "eq." + call_id})
    verdict, detail = diagnose(status, None)
    ok = row("delete call", verdict, detail) and ok
    status, payload = api(key, base, "GET", "reports",
                          params={"id": "eq." + report_id, "select": "id"})
    gone = isinstance(payload, list) and not payload
    ok = row("cascade delete", OK if gone else BAD,
             "children removed with the call" if gone
             else "the report outlived its call - on delete cascade is missing") and ok

    # 5. demo seed ----------------------------------------------------------
    print("-" * 78)
    status, payload = api(key, base, "GET", "trusted_contacts",
                          params={"user_id": "eq." + SEED_PROFILE, "active": "eq.true",
                                  "select": "id,name,phone_e164"})
    seeded = payload if isinstance(payload, list) else []
    row("demo seed", OK if seeded else BAD,
        str(len(seeded)) + " trusted contact(s) for the seeded caller" if seeded
        else "no seeded contacts - the notify path would have nobody to notify")

    print("=" * 78)
    if ok:
        print("  STORAGE PATH USABLE. Reports will land in the database and")
        print("  /r/<token> can serve them.")
        if not seeded:
            print("  (Seed rows are missing, so a call would find nobody to notify.)")
    else:
        print("  STORAGE PATH BROKEN. The agent will keep interpreting and spool to")
        print("  reports/spool/ instead - see the failures above.")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
