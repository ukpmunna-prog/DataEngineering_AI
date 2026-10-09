"""Tool specs (Bedrock Converse format) and their implementations."""

TOOL_SPECS = [
    {"toolSpec": {
        "name": "get_change",
        "description": "Fetch the full details of one change request by number.",
        "inputSchema": {"json": {"type": "object", "properties": {"number": {"type": "string"}},
                                 "required": ["number"]}}}},
    {"toolSpec": {
        "name": "search_similar_changes",
        "description": "Semantic search for historical changes (closed) similar to the given change. "
                       "Optionally restrict to the same CI or business service.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "number": {"type": "string"},
            "same_ci_only": {"type": "boolean"},
            "same_service_only": {"type": "boolean"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 15}},
            "required": ["number"]}}}},
    {"toolSpec": {
        "name": "get_outcome_stats",
        "description": "Exact outcome counts and failure rate for closed historical changes, filtered by "
                       "CI, service, category or type. Use this for any number you quote.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "cmdb_ci": {"type": "string"}, "business_service": {"type": "string"},
            "category": {"type": "string"}, "change_type": {"type": "string"}}}}}},
    {"toolSpec": {
        "name": "get_incidents_for_ci",
        "description": "Most recent incidents on a configuration item.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "cmdb_ci": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 25}},
            "required": ["cmdb_ci"]}}}},
    {"toolSpec": {
        "name": "find_overlapping_changes",
        "description": "Other upcoming changes overlapping in time on the same CI or service.",
        "inputSchema": {"json": {"type": "object", "properties": {"number": {"type": "string"}},
                                 "required": ["number"]}}}},
    {"toolSpec": {
        "name": "submit_assessment",
        "description": "Submit the final structured assessment. Call exactly once when done.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
            "similar_changes": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "similarity": {"type": "number"}, "outcome": {"type": "string"},
                "why_similar": {"type": "string"}}, "required": ["id", "outcome"]}},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "recommended_checks": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["risk", "confidence", "summary", "reasons", "recommended_checks", "evidence_ids"]}}}},
]

HISTORY_FILTER = {"phase": "history"}
SLIM_FIELDS = ("number", "short_description", "type", "category", "risk", "impact", "state", "cmdb_ci",
               "business_service", "start_date", "end_date", "outcome", "close_code", "incident_numbers")


class ToolRunner:
    """Runs data tools and records every record number returned, for citation validation."""

    def __init__(self, facts, embedder, vector_store):
        self.facts, self.embedder, self.vectors = facts, embedder, vector_store
        self.seen_ids = set()

    def run(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        return fn(**args)

    def _remember(self, *numbers):
        self.seen_ids.update(n for n in numbers if n)

    def _get_change(self, number: str) -> dict:
        c = self.facts.get_change(number)
        if not c:
            return {"error": f"change {number} not found"}
        self._remember(number, *c["incident_numbers"])
        return {k: c.get(k) for k in SLIM_FIELDS} | {
            "description": (c.get("description") or "")[:1500],
            "implementation_plan": (c.get("implementation_plan") or "")[:1500],
            "backout_plan": (c.get("backout_plan") or "")[:1000]}

    def _search_similar_changes(self, number: str, same_ci_only: bool = False,
                                same_service_only: bool = False, top_k: int = 8) -> dict:
        target = self.facts.get_change(number)
        doc = self.facts.document_for(number)
        if not target or not doc:
            return {"error": f"change {number} not found"}
        conditions = [HISTORY_FILTER, {"doc_type": "change"}]
        if same_ci_only and target.get("cmdb_ci"):
            conditions.append({"cmdb_ci": target["cmdb_ci"]})
        if same_service_only and target.get("business_service"):
            conditions.append({"business_service": target["business_service"]})
        hits = self.vectors.query(self.embedder.embed(doc["text"]), min(max(top_k, 1), 15),
                                  {"$and": conditions})
        results = []
        for h in hits:
            num = h["metadata"].get("number")
            if num == number:
                continue
            self._remember(num)
            results.append({"number": num, "similarity": round(1 - (h["distance"] or 1), 3),
                            "outcome": h["metadata"].get("outcome"), "cmdb_ci": h["metadata"].get("cmdb_ci"),
                            "incident_count": h["metadata"].get("incident_count")})
        return {"matches": results}

    def _get_outcome_stats(self, cmdb_ci: str = "", business_service: str = "", category: str = "",
                           change_type: str = "") -> dict:
        return self.facts.outcome_stats(cmdb_ci, business_service, category, change_type)

    def _get_incidents_for_ci(self, cmdb_ci: str, limit: int = 10) -> dict:
        rows = self.facts.incidents_for_ci(cmdb_ci, limit)
        self._remember(*[r["number"] for r in rows])
        return {"incidents": rows}

    def _find_overlapping_changes(self, number: str) -> dict:
        rows = self.facts.overlapping_changes(number)
        self._remember(*[r["number"] for r in rows])
        return {"overlapping": rows}


REQUIRED = ("risk", "confidence", "summary", "reasons", "recommended_checks", "evidence_ids")


def validate_assessment(a: dict, seen_ids: set, change_number: str) -> list:
    """Return a list of problems; empty means valid."""
    problems = [f"missing field: {k}" for k in REQUIRED if k not in a]
    if a.get("risk") not in {"low", "medium", "high", "critical"}:
        problems.append("risk must be one of low, medium, high, critical")
    evidence = a.get("evidence_ids") or []
    if not evidence:
        problems.append("evidence_ids must not be empty")
    allowed = seen_ids | {change_number}
    unknown = [e for e in evidence if e not in allowed]
    if unknown:
        problems.append(f"evidence_ids not retrieved by any tool: {unknown}")
    unknown_sim = [s.get("id") for s in a.get("similar_changes", []) if s.get("id") not in allowed]
    if unknown_sim:
        problems.append(f"similar_changes contains records not retrieved: {unknown_sim}")
    return problems
