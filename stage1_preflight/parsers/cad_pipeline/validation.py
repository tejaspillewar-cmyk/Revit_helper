"""
Stage 9: closed-loop validation of the extracted model.

  * Coverage: buffer each member axis by half its thickness (flat caps) and
    compare with the source wall geometry -> IoU per layer and the missing
    part (a shapely geometry, so a viewer can draw it in red).
  * Orientation: flag any member whose stored angle disagrees with its
    p1->p2 direction by more than 0.5 deg (i.e. it rotated).
  * Topology: dangling ends, overlapping duplicates, crossings without a
    node, connected components.
  * Ledger: rejected entities with reason codes.
"""
import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from .members import angle_diff, member_from_points, intersect_axes, PARALLEL_TOL_DEG
from .graph import build_nodes

ANGLE_TOL_DEG = 0.5


@dataclass
class ValidationReport:
    coverage: dict = field(default_factory=dict)        # layer -> IoU
    missing: dict = field(default_factory=dict)         # layer -> shapely geometry (unexplained source area)
    orientation_flags: list = field(default_factory=list)
    dangling_ends: int = 0
    overlaps: list = field(default_factory=list)
    crossings_without_node: int = 0
    components: int = 0
    slab_area_ratio: float | None = None
    ledger_summary: str = ""
    rejected: list = field(default_factory=list)        # LedgerEntry rows

    def lines(self):
        out = []
        for layer, iou in self.coverage.items():
            out.append(f"Coverage {layer}: IoU {iou:.2f}" + ("  <-- low, see missing area" if iou < 0.9 else ""))
        if self.orientation_flags:
            out.append(f"Orientation: {len(self.orientation_flags)} member(s) rotated > {ANGLE_TOL_DEG} deg: "
                       + ", ".join(self.orientation_flags[:8]))
        out.append(f"Topology: {self.components} connected component(s), {self.dangling_ends} free end(s), "
                   f"{len(self.overlaps)} overlapping duplicate(s), {self.crossings_without_node} crossing(s) without a node")
        if self.slab_area_ratio is not None:
            out.append(f"Slab area vs original outlines: {self.slab_area_ratio * 100:.0f}%")
        if self.ledger_summary:
            out.append(f"Ledger: {self.ledger_summary}")
        return out


def _members(parsed):
    ms = []
    for w in parsed.get("walls", []):
        if w.thickness_mm > 0 and w.length_m > 0:
            ms.append((w, member_from_points("wall", w.p1, w.p2, w.thickness_mm, ref=w)))
    for b in parsed.get("beams", []):
        if b.width_mm > 0 and b.length_m > 0:
            ms.append((b, member_from_points("beam", b.p1, b.p2, b.width_mm, ref=b)))
    return ms


def validate(parsed, regions=None, ledger=None, original_slab_area_sqmm: float | None = None) -> ValidationReport:
    rep = ValidationReport()
    pairs = _members(parsed)
    members = [m for _, m in pairs]

    # coverage per layer (walls)
    if regions:
        by_layer = {}
        for layer, poly in regions:
            by_layer.setdefault(layer, []).append(poly)
        for layer, polys in by_layer.items():
            src = unary_union(polys)
            legs = [LineString([m.p1, m.p2]).buffer(m.thickness / 2.0, cap_style=2)
                    for e, m in pairs if m.kind == "wall" and e.layer == layer and 50 <= e.thickness_mm <= 1200]
            cov = unary_union(legs) if legs else None
            if cov is None or src.area <= 0:
                rep.coverage[layer] = 0.0
                rep.missing[layer] = src
                continue
            inter = src.intersection(cov).area
            union = src.union(cov).area
            rep.coverage[layer] = inter / union if union else 0.0
            rep.missing[layer] = src.difference(cov)

    # orientation
    for e, m in pairs:
        stored = getattr(e, "angle_deg", m.angle_deg)
        if angle_diff(stored, m.angle_deg) > ANGLE_TOL_DEG:
            rep.orientation_flags.append(e.name)

    # topology
    nodes = build_nodes(members)
    rep.dangling_ends = sum(1 for n in nodes if n["type"] == "end")
    parent = list(range(len(members)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for nd in nodes:
        for a in nd["members"][1:]:
            ra, rb = find(nd["members"][0]), find(a)
            if ra != rb:
                parent[ra] = rb
    rep.components = len({find(i) for i in range(len(members))})

    for i, a in enumerate(members):
        for j in range(i + 1, len(members)):
            b = members[j]
            if a.kind != b.kind:
                continue        # beam on a wall is legitimate: both are kept
            if angle_diff(a.angle_deg, b.angle_deg) <= PARALLEL_TOL_DEG:
                if abs(a.axis.offset - b.axis.offset) < 0.25 * min(a.thickness, b.thickness):
                    lo, hi = max(a.s0, b.s0), min(a.s1, b.s1)
                    if hi - lo > 0.5 * min(a.length, b.length) and hi - lo > 50:
                        rep.overlaps.append((pairs[i][0].name, pairs[j][0].name))
                continue
            X = intersect_axes(a.axis, b.axis)
            if X is None:
                continue
            sa, sb = a.axis.s_of(X), b.axis.s_of(X)
            interior_a = a.s0 + 5 < sa < a.s1 - 5
            interior_b = b.s0 + 5 < sb < b.s1 - 5
            if interior_a and interior_b:
                rep.crossings_without_node += 1

    if original_slab_area_sqmm:
        new = sum(getattr(s, "area_sqm", 0.0) for s in parsed.get("slabs", [])) * 1e6
        rep.slab_area_ratio = new / original_slab_area_sqmm if original_slab_area_sqmm else None

    if ledger is not None:
        ledger.finalize()
        rep.ledger_summary = ledger.summary()
        rep.rejected = ledger.rejected
    return rep
