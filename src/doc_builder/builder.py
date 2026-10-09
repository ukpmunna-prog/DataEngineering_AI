"""Turn raw ServiceNow rows into embeddable documents with filterable metadata.

A change document includes its outcome and the incidents it caused, so that similarity search
over documents also surfaces consequences ("changes like this caused incidents").
"""
import hashlib
from datetime import UTC, datetime

from src.common.pii import mask_pii

FMT = "%Y-%m-%d %H:%M:%S"
CLOSED_STATE = "3"
CANCELLED_STATE = "4"
FAILED_CLOSE_CODES = {"unsuccessful", "failed"}
MAX_TEXT_CHARS = 12000


def to_epoch(value) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.strptime(value, FMT).replace(tzinfo=UTC).timestamp())
    except ValueError:
        return None


def latest_by_sys_id(rows: list, watermark_column: str = "sys_updated_on") -> list:
    """Keep only the newest version of each record (raw zone is append-only)."""
    best = {}
    for row in rows:
        key = row["sys_id"]
        if key not in best or (row.get(watermark_column) or "") >= (best[key].get(watermark_column) or ""):
            best[key] = row
    return list(best.values())


def link_incidents(changes: list, incidents: list) -> dict:
    """Map change sys_id -> incidents that it caused or that reference it."""
    by_change = {c["sys_id"]: [] for c in changes}
    for inc in incidents:
        for field in ("caused_by", "rfc"):
            ref = inc.get(field)
            if ref in by_change and inc not in by_change[ref]:
                by_change[ref].append(inc)
    return by_change


def phase_of(change: dict) -> str:
    state = str(change.get("state", ""))
    if state == CLOSED_STATE:
        return "history"
    if state == CANCELLED_STATE:
        return "cancelled"
    return "upcoming"


def outcome_of(change: dict, linked_incidents: list) -> str:
    if phase_of(change) != "history":
        return "open"
    if (change.get("close_code") or "").lower() in FAILED_CLOSE_CODES:
        return "failed"
    if linked_incidents:
        return "caused_incident"
    if (change.get("close_code") or "").lower() == "successful_with_issues":
        return "successful_with_issues"
    return "successful"


def _clean(value) -> str:
    return mask_pii(str(value)).strip() if value else ""


def build_change_doc(change: dict, linked_incidents: list) -> dict:
    outcome = outcome_of(change, linked_incidents)
    lines = [
        f"Change {change.get('number')}: {_clean(change.get('short_description'))}",
        f"Description: {_clean(change.get('description'))}",
        f"Type: {change.get('type')} | Category: {change.get('category')} | Risk: {change.get('risk')} | "
        f"Impact: {change.get('impact')}",
        f"CI: {change.get('cmdb_ci')} | Service: {change.get('business_service')}",
        f"Justification: {_clean(change.get('justification'))}",
        f"Implementation plan: {_clean(change.get('implementation_plan'))}",
        f"Backout plan: {_clean(change.get('backout_plan'))}",
        f"Outcome: {outcome}. Close code: {change.get('close_code')}. Close notes: {_clean(change.get('close_notes'))}",
    ]
    for inc in linked_incidents[:10]:
        lines.append(
            f"Followed by incident {inc.get('number')} (priority {inc.get('priority')}): "
            f"{_clean(inc.get('short_description'))}. Resolution: {_clean(inc.get('close_notes'))}"
        )
    text = "\n".join(lines)[:MAX_TEXT_CHARS]
    metadata = {
        "doc_type": "change",
        "number": change.get("number"),
        "phase": phase_of(change),
        "outcome": outcome,
        "state": str(change.get("state", "")),
        "type": change.get("type") or "",
        "category": change.get("category") or "",
        "risk": str(change.get("risk") or ""),
        "cmdb_ci": change.get("cmdb_ci") or "",
        "business_service": change.get("business_service") or "",
        "start_epoch": to_epoch(change.get("start_date")) or 0,
        "incident_count": len(linked_incidents),
    }
    return _finish(change["sys_id"], text, metadata)


def build_incident_doc(incident: dict) -> dict:
    text = "\n".join([
        f"Incident {incident.get('number')}: {_clean(incident.get('short_description'))}",
        f"Description: {_clean(incident.get('description'))}",
        f"Priority: {incident.get('priority')} | Category: {incident.get('category')}",
        f"CI: {incident.get('cmdb_ci')} | Service: {incident.get('business_service')}",
        f"Resolution: {incident.get('close_code')}. {_clean(incident.get('close_notes'))}",
    ])[:MAX_TEXT_CHARS]
    metadata = {
        "doc_type": "incident",
        "number": incident.get("number"),
        "phase": "history",
        "priority": str(incident.get("priority") or ""),
        "cmdb_ci": incident.get("cmdb_ci") or "",
        "business_service": incident.get("business_service") or "",
        "start_epoch": to_epoch(incident.get("opened_at")) or 0,
        "caused_by": incident.get("caused_by") or "",
    }
    return _finish(incident["sys_id"], text, metadata)


def _finish(key: str, text: str, metadata: dict) -> dict:
    return {"key": key, "text": text, "metadata": metadata,
            "content_hash": hashlib.sha256(text.encode()).hexdigest()}


def build_documents(changes: list, incidents: list) -> list:
    changes, incidents = latest_by_sys_id(changes), latest_by_sys_id(incidents)
    links = link_incidents(changes, incidents)
    docs = [build_change_doc(c, links[c["sys_id"]]) for c in changes]
    docs += [build_incident_doc(i) for i in incidents]
    return docs
