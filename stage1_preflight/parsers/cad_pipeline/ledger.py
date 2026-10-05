"""
Ledger: every input entity ends up either consumed by a member or rejected
with a reason.  Nothing is dropped silently.
"""
from dataclasses import dataclass, field

# reason codes
NOT_PAIRED = "not_paired"
THICKNESS_OUT_OF_SET = "thickness_out_of_set"
COMPOSITE_UNRESOLVED = "composite_unresolved"
OPEN_POLYLINE = "open_polyline"
TOO_SHORT = "too_short"
TOO_FEW_VERTICES = "too_few_vertices"
DEDUP_LOSER = "dedup_loser"
DEGENERATE = "degenerate"
UNSUPPORTED_TYPE = "unsupported_type"
NO_LABEL_VOID = "no_label_void"


@dataclass
class LedgerEntry:
    handle: str
    layer: str
    kind: str       # extractor that saw it: wall/beam/slab/column
    status: str     # "consumed" | "rejected"
    reason: str = ""
    detail: str = ""
    member: str = ""


@dataclass
class Ledger:
    entries: list = field(default_factory=list)
    _seen: dict = field(default_factory=dict)

    def see(self, handle, layer="", kind=""):
        """Register an input entity as seen (status pending until consumed/rejected)."""
        if handle and handle not in self._seen:
            self._seen[handle] = LedgerEntry(handle, layer, kind, "pending")

    def consume(self, handle, member="", layer="", kind=""):
        e = self._seen.get(handle) or LedgerEntry(handle, layer, kind, "pending")
        self._seen[handle] = e
        e.status, e.member, e.reason, e.detail = "consumed", member or e.member, "", ""

    def reject(self, handle, reason, detail="", layer="", kind=""):
        e = self._seen.get(handle) or LedgerEntry(handle, layer, kind, "pending")
        self._seen[handle] = e
        if e.status == "consumed":
            return          # another piece of the same entity was used
        e.status, e.reason, e.detail = "rejected", reason, detail

    def finalize(self):
        """Anything seen but never consumed/rejected is a bug in an extractor: mark it loudly."""
        for e in self._seen.values():
            if e.status == "pending":
                e.status, e.reason, e.detail = "rejected", "unaccounted", "entity was read but never used"
        self.entries = list(self._seen.values())
        return self

    @property
    def rejected(self):
        return [e for e in self._seen.values() if e.status == "rejected"]

    @property
    def consumed(self):
        return [e for e in self._seen.values() if e.status == "consumed"]

    def summary(self) -> str:
        by = {}
        for e in self.rejected:
            by[e.reason] = by.get(e.reason, 0) + 1
        rej = ", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "none"
        return f"{len(self.consumed)} entities used, {len(self.rejected)} rejected ({rej})"
