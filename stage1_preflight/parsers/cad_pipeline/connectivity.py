"""
Post-extraction, pre-drawing connectivity pass (axis + interval model).

Members are an axis (angle, offset, thickness) plus an interval along it, see
members.py.  This pass may only:
  - slide a member end along its own axis onto an exact axis intersection or
    a column footprint (graph.extend_trim), and
  - shift a whole cluster of collinear members to one shared offset
    (graph.consolidate_axes).
Angles never change, and nodes are exact axis intersections -- never averages.

Slabs are polygonized from the wall/beam graph (graph.slab_faces); if that
fails a sanity check, the original outlines are expanded to the member
centerlines instead (_expand_slab, directions preserved).

Returns a NEW dict (via dataclasses.replace()) -- extraction stays a pure step.
"""
import math
from dataclasses import replace

from .geometry import (
    distance, line_angle, line_intersection, closest_point_on_segment, project_point_on_line,
    polygon_area_sqmm, sqmm_to_sqm, polygon_centroid,
)

NODE_SNAP_MIN_MM = 150
NODE_SNAP_MAX_MM = 750
NODE_SNAP_FACTOR = 1.5  # snap tolerance = clamp(factor * max(thickness_a, thickness_b), MIN, MAX)

SLAB_EXPAND_MAX_GAP_MM = 600
SLAB_EXPAND_ANGLE_TOL_DEG = 5.0


class _ConnResult:
    def __init__(self):
        self.slab_edges_expanded = 0   # fallback path only
        self.ends_moved = 0
        self.offsets_merged = 0
        self.beams_slid = 0
        self.nodes = {}
        self.slab_faces = 0
        self.slab_fallback = 0


# ── shared line/endpoint helpers ─────────────────────────────────────────

def _rebuild(p1, p2):
    """(centroid, angle_deg, length_m) from two endpoints."""
    cx, cy = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    return (cx, cy), line_angle(p1, p2), distance(p1, p2) / 1000.0


def _snap_tolerance(width_a, width_b):
    return max(NODE_SNAP_MIN_MM, min(NODE_SNAP_MAX_MM, NODE_SNAP_FACTOR * max(width_a, width_b)))


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


# ── slab edge expansion (PRESERVES EDGE DIRECTIONS) ────────────────────────

def _polygon_signed_area(vertices):
    n = len(vertices)
    area = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _are_parallel(d1, d2, tol_deg):
    a1 = math.degrees(math.atan2(d1[1], d1[0])) % 180.0
    a2 = math.degrees(math.atan2(d2[1], d2[0])) % 180.0
    diff = abs(a1 - a2)
    return diff < tol_deg or abs(diff - 180.0) < tol_deg


def _expand_slab(vertices, members, result):
    """
    Expand each slab edge OUTWARD along its normal to the nearest parallel
    member centerline. New vertices are computed by intersecting the EXTENDED
    original edge lines (not the member centerlines), preserving all edge
    directions exactly.
    
    Only expands to members that SPAN the full slab edge (projection of edge
    endpoints onto member covers [0,1] with tolerance).
    """
    n = len(vertices)
    if n < 3:
        return vertices

    signed_area = _polygon_signed_area(vertices)
    ccw = signed_area > 0

    # For each edge, find the target member centerline and compute offset distance
    edge_offsets = [None] * n  # distance to expand each edge (positive = outward)
    
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        edge_dir = (b[0] - a[0], b[1] - a[1])
        edge_len = math.hypot(*edge_dir)
        if edge_len == 0:
            continue
        
        # Outward normal (perpendicular to edge, pointing outside slab)
        if ccw:
            normal = (edge_dir[1] / edge_len, -edge_dir[0] / edge_len)
        else:
            normal = (-edge_dir[1] / edge_len, edge_dir[0] / edge_len)
        
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

        best = None
        best_score = float('inf')
        for m in members:
            mdir = (m["p2"][0] - m["p1"][0], m["p2"][1] - m["p1"][1])
            if not _are_parallel(edge_dir, mdir, SLAB_EXPAND_ANGLE_TOL_DEG):
                continue
            
            # Project slab edge MIDPOINT onto member centerline
            proj, dist_to_line = closest_point_on_segment(mid, m["p1"], m["p2"])
            
            # NEW: Check if member spans the slab edge (projection of edge endpoints onto member)
            t_a = project_point_on_line(a, m["p1"], m["p2"])
            t_b = project_point_on_line(b, m["p1"], m["p2"])
            t_min, t_max = min(t_a, t_b), max(t_a, t_b)
            # Member covers slab edge if [t_min, t_max] covers [0, 1] with some tolerance
            covers = (t_min <= 0.1 and t_max >= 0.9)
            
            # Gap along slab edge normal (positive = member is outward)
            gap = (proj[0] - mid[0]) * normal[0] + (proj[1] - mid[1]) * normal[1]
            if gap <= 0:
                continue  # member is inward, not an outward support
            
            # Perpendicular distance from slab edge to member centerline
            perp_gap = abs((mid[0] - m["p1"][0]) * normal[0] + (mid[1] - m["p1"][1]) * normal[1])
            if perp_gap > SLAB_EXPAND_MAX_GAP_MM:
                continue
            
            # Prefer members that fully span the edge
            score = perp_gap + (0.0 if covers else 1000.0)
            if score < best_score:
                best_score = score
                best = gap
        
        if best is not None:
            edge_offsets[i] = best

    if not any(edge_offsets):
        return vertices

    # NEW VERTICES: Intersect the OFFSET original edge lines
    # Each edge line is shifted outward by its offset along its normal
    # This PRESERVES the original edge directions exactly
    new_vertices = []
    expanded_edges = 0
    
    for i in range(n):
        prev_i = (i - 1) % n
        
        # Edge i-1 (incoming to vertex i)
        a1, b1 = vertices[prev_i], vertices[i]
        dir1 = (b1[0] - a1[0], b1[1] - a1[1])
        len1 = math.hypot(*dir1)
        if len1 == 0:
            new_vertices.append(vertices[i])
            continue
        if ccw:
            norm1 = (dir1[1] / len1, -dir1[0] / len1)
        else:
            norm1 = (-dir1[1] / len1, dir1[0] / len1)
        offset1 = edge_offsets[prev_i] if edge_offsets[prev_i] is not None else 0.0
        
        # Edge i (outgoing from vertex i)
        a2, b2 = vertices[i], vertices[(i + 1) % n]
        dir2 = (b2[0] - a2[0], b2[1] - a2[1])
        len2 = math.hypot(*dir2)
        if len2 == 0:
            new_vertices.append(vertices[i])
            continue
        if ccw:
            norm2 = (dir2[1] / len2, -dir2[0] / len2)
        else:
            norm2 = (-dir2[1] / len2, dir2[0] / len2)
        offset2 = edge_offsets[i] if edge_offsets[i] is not None else 0.0
        
        # Offset the two edge lines
        p1 = (a1[0] + offset1 * norm1[0], a1[1] + offset1 * norm1[1])
        p2 = (a2[0] + offset2 * norm2[0], a2[1] + offset2 * norm2[1])
        
        pt = line_intersection(p1, (p1[0] + dir1[0], p1[1] + dir1[1]),
                               p2, (p2[0] + dir2[0], p2[1] + dir2[1]))
        
        if pt is not None:
            max_move = SLAB_EXPAND_MAX_GAP_MM * 3
            if distance(pt, vertices[i]) <= max_move:
                new_vertices.append(pt)
            else:
                new_vertices.append(vertices[i])
        else:
            new_vertices.append(vertices[i])
        
        if edge_offsets[i] is not None:
            expanded_edges += 1

    # Validation: expansion should only grow the slab
    if polygon_area_sqmm(new_vertices) < polygon_area_sqmm(vertices) - 1.0:
        return vertices

    result.slab_edges_expanded += expanded_edges
    return new_vertices


# ── entry point ───────────────────────────────────────────────────────────

MIN_SNAPPED_LENGTH_M = 0.05
SLAB_AREA_TOL = 0.15  # faces vs original outlines: warn beyond +/-15% total area


def snap_connections(parsed: dict, log=lambda m: None) -> dict:
    """
    Axis-based connectivity.  Members keep their axis angle; only their
    interval (slide along the axis) or a cluster's shared offset may change.
    Nodes are exact axis intersections -- endpoints are never averaged.
    """
    from .members import member_from_points
    from .graph import consolidate_axes, extend_trim, slab_faces, build_nodes, snap_beams_to_wall_ends

    walls = list(parsed.get("walls", []))
    beams = list(parsed.get("beams", []))
    slabs = list(parsed.get("slabs", []))
    columns = list(parsed.get("columns", []))

    result = _ConnResult()

    eligible_walls = [w for w in walls if not w.warnings and w.thickness_mm > 0 and w.length_m > 0]
    # A beam whose only problem is a missing depth label still has good geometry: keep it in the
    # network so its neighbours connect to it (the ETABS writer skips it for lack of a depth).
    eligible_beams = [b for b in beams if b.width_mm > 0 and b.length_m > 0
                      and (not b.warnings or b.dimension_source == "unmatched")]
    eligible_columns = [c for c in columns if not c.warnings and c.width_mm > 0 and c.depth_mm > 0]

    members = []
    for w in eligible_walls:
        members.append(member_from_points("wall", w.p1, w.p2, w.thickness_mm, ref=w))
    for b in eligible_beams:
        members.append(member_from_points("beam", b.p1, b.p2, b.width_mm, ref=b))

    angles_before = [m.angle_deg for m in members]
    result.offsets_merged = consolidate_axes(members)
    result.beams_slid = snap_beams_to_wall_ends(members)
    result.ends_moved = extend_trim(members, columns=eligible_columns)
    assert all(abs(a - m.angle_deg) < 1e-9 for a, m in zip(angles_before, members)), "axis angle changed"
    by_ref = {id(m.ref): m for m in members}

    new_walls = []
    for w in walls:
        m = by_ref.get(id(w))
        if m is None:
            new_walls.append(w)
            continue
        warnings = list(w.warnings)
        if m.length / 1000.0 < MIN_SNAPPED_LENGTH_M:
            warnings.append(f"Wall '{w.name}' collapsed to ~0 length after connecting -- not drawn.")
        c = m.centroid
        new_walls.append(replace(w, p1=m.p1, p2=m.p2, centroid=(round(c[0], 1), round(c[1], 1)),
                                 angle_deg=round(m.angle_deg, 2), length_m=round(m.length / 1000.0, 3),
                                 warnings=warnings))
    new_beams = []
    for b in beams:
        m = by_ref.get(id(b))
        if m is None:
            new_beams.append(b)
            continue
        warnings = list(b.warnings)
        if m.length / 1000.0 < MIN_SNAPPED_LENGTH_M:
            warnings.append(f"Beam '{b.name}' collapsed to ~0 length after connecting -- not drawn.")
        c = m.centroid
        new_beams.append(replace(b, p1=m.p1, p2=m.p2, centroid=(round(c[0], 1), round(c[1], 1)),
                                 angle_deg=round(m.angle_deg, 2), length_m=round(m.length / 1000.0, 3),
                                 warnings=warnings))

    nodes = build_nodes(members)
    result.nodes = {"L": 0, "T": 0, "X": 0, "end": 0}
    for nd in nodes:
        result.nodes[nd["type"]] += 1

    new_slabs = _slabs_from_graph(slabs, members, parsed.get("void_points", []), parsed.get("void_polys", []), result, log)

    log(f"Connectivity: {result.ends_moved} end(s) slid along their own axis, "
        f"{result.offsets_merged} collinear axis offset(s) consolidated, "
        f"{result.beams_slid} beam(s) slid onto wall ends; "
        f"nodes L={result.nodes['L']} T={result.nodes['T']} X={result.nodes['X']} free ends={result.nodes['end']}; "
        f"slabs: {result.slab_faces} from member-graph faces, {result.slab_fallback} outline(s) expanded to members "
        f"(axis angles unchanged).")

    out = dict(parsed)
    out.update({"slabs": new_slabs, "walls": new_walls, "beams": new_beams, "columns": columns})
    return out


FACE_MATCH_MIN_COVER = 0.85    # face must cover this much of the slab outline...
FACE_MATCH_MAX_GROWTH = 1.40   # ...without ballooning beyond this multiple of it


def _slabs_from_graph(slabs, members, void_points, void_polys, result, log):
    """
    Per slab: use the polygonized member-graph face that matches the outline; where the graph has
    gaps (no closed face), expand that outline to its supporting members instead.
    """
    from shapely.geometry import Polygon
    from .graph import slab_faces, polygon_ccw_vertices

    result.slab_faces = 0
    result.slab_fallback = 0
    good = [s for s in slabs if not s.warnings and s.vertices]
    if not good or not members:
        return slabs

    seeds = [{"vertices": s.vertices, "thickness_mm": s.thickness_mm} for s in good]
    faces, warns = slab_faces(members, seeds, void_points=void_points, void_polys=void_polys, log=log)
    shapes = [Polygon(s.vertices).buffer(0) for s in good]
    mdicts = [{"p1": m.p1, "p2": m.p2, "half_width": m.thickness / 2} for m in members]

    used_faces = set()
    out = [s for s in slabs if s.warnings or not s.vertices]     # flagged originals stay visible
    counters = {}

    def emit(src, verts, poly_area, centroid, extra_warnings=()):
        thk = src.thickness_mm
        counters[thk] = counters.get(thk, 0) + 1
        area_sqm = sqmm_to_sqm(poly_area)
        out.append(replace(src, name=f"S{counters[thk]}-{thk}", vertices=verts, area_sqm=round(area_sqm, 3),
                           volume_cum=round(area_sqm * thk / 1000.0, 4),
                           centroid=(round(centroid[0], 1), round(centroid[1], 1)),
                           warnings=list(src.warnings) + list(extra_warnings)))

    for i, (src, sh) in enumerate(zip(good, shapes)):
        best = None
        for fi, f in enumerate(faces):
            if i not in f["sources"]:
                continue
            inter = f["poly"].intersection(sh).area
            if sh.area <= 0 or inter / sh.area < FACE_MATCH_MIN_COVER or f["poly"].area / sh.area > FACE_MATCH_MAX_GROWTH:
                continue
            if best is None or inter > best[0]:
                best = (inter, fi)
        if best is not None:
            fi = best[1]
            if fi in used_faces:
                continue                       # one face shared by several outlines: emit once
            used_faces.add(fi)
            f = faces[fi]
            c = f["poly"].centroid
            emit(src, polygon_ccw_vertices(f["poly"]), f["poly"].area, (c.x, c.y),
                 ["Face spans slab outlines of different thickness: a supporting member is probably missing."]
                 if f.get("mixed") else [])
            result.slab_faces += 1
            continue
        nv = _expand_slab(src.vertices, mdicts, result)
        result.slab_fallback += 1
        c = polygon_centroid(nv)
        emit(src, list(nv), polygon_area_sqmm(nv), c)

    for w in warns:
        log(w)
    return out
