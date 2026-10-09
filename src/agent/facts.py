"""Exact, queryable facts about changes and incidents. The LLM calls these as tools and never counts itself.

In-memory implementation built from the raw zone; swap for an Athena-backed class with the same
methods when volumes outgrow a Lambda's memory.
"""
from src.doc_builder.builder import build_documents, latest_by_sys_id, link_incidents, outcome_of, phase_of, to_epoch

FAIL_OUTCOMES = {"failed", "caused_incident"}


class FactsStore:
    def __init__(self, changes: list, incidents: list):
        self.changes = latest_by_sys_id(changes)
        self.incidents = latest_by_sys_id(incidents)
        links = link_incidents(self.changes, self.incidents)
        self.by_number = {}
        for c in self.changes:
            linked = links[c["sys_id"]]
            self.by_number[c["number"]] = {
                **c, "phase": phase_of(c), "outcome": outcome_of(c, linked),
                "incident_numbers": [i["number"] for i in linked],
            }

    def get_change(self, number: str) -> dict | None:
        return self.by_number.get(number)

    def upcoming_numbers(self) -> list:
        return sorted(n for n, c in self.by_number.items() if c["phase"] == "upcoming")

    def outcome_stats(self, cmdb_ci: str = "", business_service: str = "", category: str = "",
                      change_type: str = "") -> dict:
        rows = [c for c in self.by_number.values() if c["phase"] == "history"
                and (not cmdb_ci or c.get("cmdb_ci") == cmdb_ci)
                and (not business_service or c.get("business_service") == business_service)
                and (not category or c.get("category") == category)
                and (not change_type or c.get("type") == change_type)]
        counts = {}
        for c in rows:
            counts[c["outcome"]] = counts.get(c["outcome"], 0) + 1
        total = len(rows)
        bad = sum(counts.get(o, 0) for o in FAIL_OUTCOMES)
        return {"total_closed_changes": total, "by_outcome": counts, "failed_or_caused_incident": bad,
                "failure_rate": round(bad / total, 3) if total else None}

    def incidents_for_ci(self, cmdb_ci: str, limit: int = 10) -> list:
        rows = sorted((i for i in self.incidents if i.get("cmdb_ci") == cmdb_ci),
                      key=lambda i: i.get("opened_at") or "", reverse=True)
        return [{"number": i["number"], "priority": i.get("priority"), "opened_at": i.get("opened_at"),
                 "short_description": i.get("short_description"), "caused_by": i.get("caused_by")}
                for i in rows[:limit]]

    def overlapping_changes(self, number: str) -> list:
        target = self.by_number.get(number)
        if not target:
            return []
        s, e = to_epoch(target.get("start_date")), to_epoch(target.get("end_date"))
        if s is None or e is None:
            return []
        out = []
        for n, c in self.by_number.items():
            if n == number or c["phase"] != "upcoming":
                continue
            cs, ce = to_epoch(c.get("start_date")), to_epoch(c.get("end_date"))
            same_scope = (c.get("cmdb_ci") and c.get("cmdb_ci") == target.get("cmdb_ci")) or (
                c.get("business_service") and c.get("business_service") == target.get("business_service"))
            if cs is not None and ce is not None and cs < e and s < ce and same_scope:
                out.append({"number": n, "short_description": c.get("short_description"),
                            "start_date": c.get("start_date"), "end_date": c.get("end_date"),
                            "cmdb_ci": c.get("cmdb_ci")})
        return out

    def document_for(self, number: str) -> dict | None:
        c = self.by_number.get(number)
        if not c:
            return None
        linked = [i for i in self.incidents if i.get("caused_by") == c["sys_id"] or i.get("rfc") == c["sys_id"]]
        return next((d for d in build_documents([c], linked) if d["metadata"]["doc_type"] == "change"), None)
