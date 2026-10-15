"""Generate realistic, deterministic mock ServiceNow data (change_request + incident).

Output matches what the extractor writes to the raw zone, so the rest of the pipeline cannot tell
the difference:

    raw/<table>/dt=<date>/run=<run_id>/part-00000.jsonl     (one JSON object per line)

The data has built-in signal so the AI has something real to find:
  * some CIs are fragile (high failure rate), some are stable
  * changes with no backout plan / high risk fail more often
  * failed changes cause incidents (incident.caused_by -> change sys_id)
  * upcoming changes deliberately resemble past failures (and one overlaps another)

Usage:
    python -m tools.mock_data --out build/mock                 # write files locally
    python -m tools.mock_data --s3-bucket sn-change-intel-dev-raw-<acct>   # upload to the raw zone
"""
import argparse
import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

FMT = "%Y-%m-%d %H:%M:%S"

# (cmdb_ci, business_service, assignment_group, fragility 0..1)
CIS = [
    ("core-banking-db-01", "Core Banking", "DBA Team", 0.55),
    ("core-banking-app-02", "Core Banking", "Platform Team", 0.35),
    ("payments-gw-01", "Payments", "Integration Team", 0.40),
    ("payments-api-lb", "Payments", "Network Team", 0.20),
    ("crm-web-03", "CRM", "Platform Team", 0.15),
    ("hr-portal-01", "HR Portal", "Platform Team", 0.10),
    ("edge-fw-eu-01", "Network Edge", "Network Team", 0.45),
    ("data-warehouse-01", "Analytics", "Data Team", 0.25),
]

# category -> (summary templates, description, implementation plan)
TEMPLATES = {
    "Database": (
        ["Apply quarterly patch to {ci}", "Upgrade database engine on {ci}", "Add index and tune parameters on {ci}"],
        "Database maintenance on {ci} supporting {svc}. Requires a short write freeze.",
        "1. Snapshot. 2. Stop app writes. 3. Apply change. 4. Run smoke queries. 5. Resume writes.",
    ),
    "Network": (
        ["Replace firewall rule set on {ci}", "Firmware upgrade on {ci}", "Re-route traffic via new link for {ci}"],
        "Network change on {ci} affecting connectivity for {svc}.",
        "1. Export config. 2. Apply change in window. 3. Verify routes and rules. 4. Monitor 30 minutes.",
    ),
    "Application": (
        ["Deploy release {ver} to {ci}", "Roll out hotfix {ver} on {ci}", "Enable feature flag on {ci}"],
        "Application deployment on {ci} for {svc}.",
        "1. Drain node. 2. Deploy build. 3. Health check. 4. Return to rotation. 5. Repeat per node.",
    ),
    "Infrastructure": (
        ["OS security patching on {ci}", "Resize compute for {ci}", "Renew certificates on {ci}"],
        "Infrastructure maintenance on {ci} used by {svc}.",
        "1. Take backup. 2. Apply update. 3. Reboot if required. 4. Validate services.",
    ),
}
BACKOUT = "Restore from snapshot taken before the change and revert configuration. Estimated 45 minutes."
GOOD_JUSTIFICATION = "Vendor security advisory and scheduled maintenance policy."
WEAK_JUSTIFICATION = "Requested by team."
INCIDENT_SUMMARIES = [
    "{svc} slow or unavailable after maintenance on {ci}",
    "Errors reported by users of {svc} ({ci})",
    "{ci} not responding to health checks",
]
BACKGROUND_INCIDENTS = [
    "Intermittent timeouts on {ci}", "High CPU alert on {ci}", "Disk usage warning on {ci}",
    "Certificate expiry warning for {ci}",
]


def sys_id(kind: str, number: str) -> str:
    return hashlib.md5(f"{kind}:{number}".encode()).hexdigest()  # noqa: S324 - not security related


def fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def generate(seed: int = 42, today: datetime | None = None, n_history: int = 140, n_background: int = 60):
    rng = random.Random(seed)
    today = today or datetime(2026, 10, 15, 9, 0, 0)
    changes: list = []
    incidents: list = []
    inc_counter = [100000]
    chg_counter = [10000]

    def next_chg() -> str:
        chg_counter[0] += 1
        return f"CHG{chg_counter[0]:07d}"

    def next_inc() -> str:
        inc_counter[0] += 1
        return f"INC{inc_counter[0]:07d}"

    def make_change(ci_row, category, start, state, close_code="", notes="", risk=None, backout=True, risky_slot=False):
        ci, svc, group, _ = ci_row
        number = next_chg()
        summaries, desc, plan = TEMPLATES[category]
        summary = rng.choice(summaries).format(ci=ci, svc=svc, ver=f"{rng.randint(2, 9)}.{rng.randint(0, 20)}")
        created = start - timedelta(days=rng.randint(3, 14))
        end = start + timedelta(hours=rng.choice([1, 2, 3, 4]))
        row = {
            "sys_id": sys_id("chg", number), "number": number, "short_description": summary,
            "description": desc.format(ci=ci, svc=svc),
            "type": rng.choice(["Normal", "Normal", "Standard"]) if not risky_slot else "Normal",
            "state": state, "risk": risk or rng.choice(["Low", "Moderate", "High"]),
            "impact": rng.choice(["Low", "Medium", "High"]), "priority": rng.choice(["3", "3", "2"]),
            "category": category, "cmdb_ci": ci, "business_service": svc, "assignment_group": group,
            "start_date": fmt(start), "end_date": fmt(end),
            "justification": GOOD_JUSTIFICATION if backout else WEAK_JUSTIFICATION,
            "implementation_plan": plan, "backout_plan": BACKOUT if backout else "",
            "close_code": close_code, "close_notes": notes,
            "sys_created_on": fmt(created), "sys_updated_on": fmt(end if state == "3" else created + timedelta(days=1)),
        }
        changes.append(row)
        return row

    def make_incident(ci_row, opened, summary, caused_by=None, priority=None):
        ci, svc, group, _ = ci_row
        number = next_inc()
        resolved = opened + timedelta(hours=rng.randint(1, 30))
        row = {
            "sys_id": sys_id("inc", number), "number": number,
            "short_description": summary.format(ci=ci, svc=svc),
            "description": f"Reported against {ci} ({svc}).", "state": "6",
            "priority": priority or rng.choice(["2", "3", "3", "4"]),
            "urgency": rng.choice(["2", "3"]), "impact": rng.choice(["2", "3"]),
            "category": "Software", "cmdb_ci": ci, "business_service": svc, "assignment_group": group,
            "caused_by": caused_by or "", "rfc": caused_by or "",
            "close_code": "Solved (Permanently)",
            "close_notes": "Rolled back the change." if caused_by else "Resolved by operations.",
            "opened_at": fmt(opened), "resolved_at": fmt(resolved),
            "sys_created_on": fmt(opened), "sys_updated_on": fmt(resolved),
        }
        incidents.append(row)
        return row

    # ---- history: closed changes over ~18 months -------------------------------------------------
    for _ in range(n_history):
        ci_row = rng.choice(CIS)
        category = rng.choice(list(TEMPLATES))
        if ci_row[0].endswith("db-01") or "db" in ci_row[0]:
            category = rng.choice(["Database", "Database", "Infrastructure"])
        start = today - timedelta(days=rng.randint(10, 540), hours=rng.randint(0, 23))
        backout = rng.random() > 0.18
        risk = rng.choice(["Low", "Moderate", "High"])
        p_fail = ci_row[3] * 0.7 + (0.25 if not backout else 0) + (0.1 if risk == "High" else 0)
        p_fail += 0.08 if start.weekday() == 4 and start.hour >= 17 else 0  # Friday evening
        roll = rng.random()
        if roll < p_fail * 0.45:
            outcome = "failed"
        elif roll < p_fail:
            outcome = "caused_incident"
        elif roll < p_fail + 0.10:
            outcome = "successful_with_issues"
        else:
            outcome = "successful"
        code = {"failed": "unsuccessful", "caused_incident": "successful",
                "successful_with_issues": "successful_with_issues", "successful": "successful"}[outcome]
        failed_note = "Change failed and was backed out." if backout else "Failed; no backout plan, manual recovery."
        notes = {"failed": failed_note, "caused_incident": "Completed but caused a service incident.",
                 "successful_with_issues": "Completed with minor issues.",
                 "successful": "Completed as planned."}[outcome]
        chg = make_change(ci_row, category, start, "3", code, notes, risk, backout)
        if outcome in ("failed", "caused_incident"):
            opened = datetime.strptime(chg["end_date"], FMT) + timedelta(minutes=rng.randint(5, 90))
            make_incident(ci_row, opened, rng.choice(INCIDENT_SUMMARIES), caused_by=chg["sys_id"],
                          priority=rng.choice(["1", "2", "2"]))

    # a few cancelled changes (should be ignored as evidence of outcomes)
    for _ in range(6):
        make_change(rng.choice(CIS), rng.choice(list(TEMPLATES)), today - timedelta(days=rng.randint(20, 300)), "4",
                    "cancelled", "Cancelled by requester.")

    # ---- background incidents unrelated to changes (give "recent incidents on CI" something to find)
    for _ in range(n_background):
        ci_row = rng.choice(CIS)
        make_incident(ci_row, today - timedelta(days=rng.randint(1, 500), hours=rng.randint(0, 23)),
                      rng.choice(BACKGROUND_INCIDENTS))
    # a very recent incident on the fragile DB so the agent can flag recent instability
    make_incident(CIS[0], today - timedelta(days=3), "Replication lag on {ci}", priority="2")

    # ---- upcoming changes (state -2 scheduled / -5 new) ------------------------------------------
    # next Friday 18:00
    nxt = (today + timedelta(days=(4 - today.weekday()) % 7 or 7)).replace(hour=18, minute=0, second=0)
    db, fw, pay, crm, hr = CIS[0], CIS[6], CIS[2], CIS[4], CIS[5]
    scenarios = [
        # risky: fragile DB, patch, Friday evening, no backout plan, weak justification  -> should score high
        (db, "Database", nxt, "-2", "High", False),
        # overlaps the risky one on the same service (different CI)
        (CIS[1], "Application", nxt + timedelta(hours=1), "-2", "Moderate", True),
        # risky network change on a fragile CI
        (fw, "Network", nxt + timedelta(days=1, hours=-10), "-2", "High", True),
        # payments app release with backout, mid-week morning -> moderate
        (pay, "Application", (nxt - timedelta(days=3)).replace(hour=10), "-5", "Moderate", True),
        # stable, well-prepared, low risk -> should score low
        (crm, "Infrastructure", (nxt - timedelta(days=2)).replace(hour=7), "-2", "Low", True),
        (hr, "Application", (nxt + timedelta(days=4)).replace(hour=8), "-5", "Low", True),
    ]
    for ci_row, cat, start, state, risk, backout in scenarios:
        make_change(ci_row, cat, start, state, risk=risk, backout=backout, risky_slot=True)

    return changes, incidents


def to_jsonl(rows: list, run_id: str, loaded_at: datetime) -> str:
    stamp = loaded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join(json.dumps({**r, "_loaded_at": stamp, "_run_id": run_id}, separators=(",", ":")) for r in rows)


def raw_key(table: str, run_id: str, loaded_at: datetime, part: int = 0) -> str:
    return f"raw/{table}/dt={loaded_at:%Y-%m-%d}/run={run_id}/part-{part:05d}.jsonl"


def write_local(out: Path, files: dict) -> None:
    for key, body in files.items():
        path = out / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n", encoding="utf-8")


def upload_s3(bucket: str, files: dict) -> None:
    import boto3

    s3 = boto3.client("s3")
    for key, body in files.items():
        s3.put_object(Bucket=bucket, Key=key, Body=(body + "\n").encode(), ContentType="application/x-ndjson",
                      ServerSideEncryption="aws:kms")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--history", type=int, default=140, help="number of closed historical changes")
    ap.add_argument("--out", type=Path, help="write files under this directory")
    ap.add_argument("--s3-bucket", help="upload to this raw bucket instead of / as well as writing locally")
    args = ap.parse_args(argv)

    loaded_at = datetime(2026, 10, 15, 9, 0, 0)
    run_id = f"mock-{args.seed}"
    changes, incidents = generate(args.seed, n_history=args.history)
    files = {
        raw_key("change_request", run_id, loaded_at): to_jsonl(changes, run_id, loaded_at),
        raw_key("incident", run_id, loaded_at): to_jsonl(incidents, run_id, loaded_at),
    }
    if args.out:
        write_local(args.out, files)
    if args.s3_bucket:
        upload_s3(args.s3_bucket, files)
    if not args.out and not args.s3_bucket:
        ap.error("give --out and/or --s3-bucket")
    upcoming = [c["number"] for c in changes if c["state"] not in ("3", "4")]
    print(f"change_request={len(changes)} incident={len(incidents)} upcoming={upcoming}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
