"""
Stage 4: axis consolidation, extend/trim to nodes, node classification and
slab faces polygonized from the member graph.

Members are axis + interval (see members.py).  Nothing here ever changes an
axis angle.  Ends slide along their own axis; consolidation may shift a whole
cluster's offset.
"""
import math

from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from .members import Member, angle_diff, intersect_axes, PARALLEL_TOL_DEG

EXTEND_FACTOR = 0.75        # reach = factor * max(thickness) + EXTEND_PAD
EXTEND_PAD_MM = 50.0
EXTEND_PASSES = 1           # an end moved in one pass can create the node another end needs
CONSOLIDATE_TOL_FACTOR = 0.1    # jitter floor: axes closer than factor * thinner member are one line
CONTAIN_SLACK_MM = 10.0     # a strip may poke out of the thicker member's strip by this much
BUTT_FACE_TOL_MM = 30.0     # a wall end this far outside a beam face still counts as inside its strip
SPAN_END_REACH_MM = 150.0   # a wall end this close (along the beam) to a beam end holds that end up
ON_AXIS_TOL_MM = 5.0        # a wall end this close to a beam axis is already connected
NODE_MERGE_MM = 1.0
MIN_MEMBER_MM = 50.0
SLAB_SUPPORT_PAD_MM = 150.0
SLAB_MIN_COVERAGE = 0.30


# ── axis consolidation ───────────────────────────────────────────────────

def consolidate_axes(members):
    """
    Per direction, cluster perpendicular offsets in 1D and merge axes whose strips are nested
    (see _merge_tol) so collinear members share one exact line.
    Only members whose spans touch/overlap are merged, so two unrelated parallel walls far apart
    along the line keep their own offset.

    The shared line is the DOMINANT member's own axis (a wall outranks a beam, then the thicker, then the
    longer member wins) -- never an average: a beam running on into a wall is moved onto the wall's line.
    Returns the number of members whose offset changed.
    """
    changed = 0
    by_dir = []
    for m in members:
        for grp in by_dir:
            if angle_diff(grp[0].axis.angle_deg, m.axis.angle_deg) <= PARALLEL_TOL_DEG:
                grp.append(m)
                break
        else:
            by_dir.append([m])

    for grp in by_dir:
        # One shared frame per direction group: an axis at 179.9999 deg and one at 0 deg have
        # opposite normals, so their own offsets are not comparable.
        ref = grp[0].axis
        roff = {id(m): ref.off_of(m.centroid) for m in grp}
        grp.sort(key=lambda m: roff[id(m)])
        cluster = [grp[0]]
        clusters = []
        for m in grp[1:]:
            prev = cluster[-1]
            if roff[id(m)] - roff[id(prev)] <= _merge_tol(prev, m):
                cluster.append(m)
            else:
                clusters.append(cluster)
                cluster = [m]
        clusters.append(cluster)
        for cl in clusters:
            if len(cl) < 2:
                continue
            # only merge chains connected along the axis
            for sub in _connected_along_axis(cl):
                if len(sub) < 2:
                    continue
                dom = max(sub, key=lambda m: (m.kind == "wall", m.thickness, m.length))
                for m in sub:
                    if m is not dom and _move_to_line(m, ref, roff[id(dom)]):
                        changed += 1
    return changed


def _merge_tol(a: Member, b: Member) -> float:
    """
    Largest perpendicular offset at which two parallel members still share one line: the thinner
    member's strip must lie inside the thicker one's (half the thickness difference), plus a small
    jitter allowance.  A 200 beam flush with a 350 wall joins the wall's line; a 300 wall next to a
    350 wall does not, because its strip would stick out of the 350 one.
    """
    contain = abs(a.thickness - b.thickness) / 2.0 + CONTAIN_SLACK_MM
    return max(CONSOLIDATE_TOL_FACTOR * min(a.thickness, b.thickness), contain)


def _move_to_line(m: Member, ref, ref_off: float) -> bool:
    """Shift m sideways onto the line at offset ref_off in ref's frame; the interval is unchanged."""
    d, n = ref.d, ref.n
    s_mid = ref.s_of(m.centroid)
    target = (s_mid * d[0] + ref_off * n[0], s_mid * d[1] + ref_off * n[1])
    off = m.axis.off_of(target)
    if abs(off - m.axis.offset) <= 1e-6:
        return False
    _set_offset(m, off)
    return True


def _set_offset(m: Member, off: float):
    """Shift the member sideways; the interval (position along the axis) is unchanged."""
    m.axis = type(m.axis)(m.axis.angle_deg, off, m.axis.thickness)


def _connected_along_axis(cl):
    ref = cl[0].axis
    items = sorted(cl, key=lambda m: ref.s_of(m.p1) if False else min(ref.s_of(m.p1), ref.s_of(m.p2)))
    out, cur, cur_max = [], [], None
    for m in items:
        lo = min(ref.s_of(m.p1), ref.s_of(m.p2))
        hi = max(ref.s_of(m.p1), ref.s_of(m.p2))
        gap_tol = 1.05 * max(m.thickness, 1.0)
        if cur and lo - cur_max <= gap_tol:
            cur.append(m)
            cur_max = max(cur_max, hi)
        else:
            if cur:
                out.append(cur)
            cur, cur_max = [m], hi
    if cur:
        out.append(cur)
    return out


# ── beams yield to wall ends ─────────────────────────────────────────────

def snap_beams_to_wall_ends(members):
    """
    A wall that ends inside a beam's strip keeps its length; the beam slides sideways so its axis
    passes through the wall's end (beam eccentricity is cheap, a shortened wall is not).  Covers a
    wall butting a beam face and a wall running through to the beam's far face.

    Only for a beam that spans between two wall ends (one at each of its ends).  A beam that is
    also held up by other members keeps its midline, and the wall is trimmed to it instead.

    Skipped (the beam keeps its midline and extend_trim handles the ends) when:
      - a wall already ends on the beam axis,
      - walls end on both sides of the axis, or at different lines,
      - the beam is part of a longer collinear run (the manual answer links such a wall end to
        the run with a short stub instead), so only isolated beams slide.
    Returns the number of beams moved.
    """
    beams = [m for m in members if m.kind == "beam"]
    walls = [m for m in members if m.kind == "wall"]
    moved = 0
    for b in beams:
        half = b.thickness / 2.0
        hits = []        # signed offsets (relative to the beam axis) of wall ends inside the strip
        at_s0 = at_s1 = False
        reach = max(b.thickness, SPAN_END_REACH_MM)
        for w in walls:
            if angle_diff(w.axis.angle_deg, b.axis.angle_deg) < 60.0:
                continue
            for end in (w.p1, w.p2):
                s = b.axis.s_of(end)
                if not (b.s0 - b.thickness <= s <= b.s1 + b.thickness):
                    continue
                d = b.axis.off_of(end) - b.axis.offset
                if abs(d) <= half + BUTT_FACE_TOL_MM:
                    hits.append(d)
                    at_s0 = at_s0 or abs(s - b.s0) <= reach
                    at_s1 = at_s1 or abs(s - b.s1) <= reach
        if not (at_s0 and at_s1) or min(abs(d) for d in hits) <= ON_AXIS_TOL_MM:
            continue
        if max(hits) * min(hits) < 0 or max(hits) - min(hits) > BUTT_FACE_TOL_MM:
            continue
        if len(_collinear_run(b, beams)) > 1:
            continue
        _set_offset(b, b.axis.offset + sum(hits) / len(hits))
        moved += 1
    return moved


def _collinear_run(b, beams):
    """b plus the beams chained to it along the same axis."""
    run, frontier = [b], [b]
    while frontier:
        cur = frontier.pop()
        for o in beams:
            if o in run or angle_diff(o.axis.angle_deg, cur.axis.angle_deg) > PARALLEL_TOL_DEG:
                continue
            if abs(cur.axis.off_of(o.centroid) - cur.axis.offset) > 1.0:
                continue
            lo, hi = sorted((o.s0, o.s1))
            if lo - cur.s1 <= 1.05 * o.thickness and cur.s0 - hi <= 1.05 * o.thickness:
                run.append(o)
                frontier.append(o)
    return run


# ── extend / trim ends onto neighbouring axes ────────────────────────────

def extend_trim(members, columns=None):
    """
    Slide each free end along its own axis to the exact intersection with a
    neighbouring axis (or onto a column footprint) when that intersection is
    within ~0.75 x thickness + pad of the end.  Direction never changes.
    Returns the number of ends moved.
    """
    applied = 0
    for _ in range(max(1, EXTEND_PASSES)):
        n = _extend_trim_pass(members, columns)
        applied += n
        if n == 0:
            break
    return applied


def _extend_trim_pass(members, columns):
    moves = []   # (member, which, new_s)
    for mi, m in enumerate(members):
        for which in (0, 1):
            end_s = m.s0 if which == 0 else m.s1
            best = None
            for oi, o in enumerate(members):
                if oi == mi:
                    continue
                X = intersect_axes(m.axis, o.axis)
                if X is None:
                    continue
                sx = m.axis.s_of(X)
                move = (sx - end_s) if which == 1 else (end_s - sx)
                reach = EXTEND_FACTOR * max(m.thickness, o.thickness) + EXTEND_PAD_MM
                if abs(move) > reach:
                    continue
                so = o.axis.s_of(X)
                slack = m.thickness / 2.0 + 1.0
                if not (o.s0 - slack <= so <= o.s1 + slack):
                    continue
                if best is None or abs(move) < abs(best[0]):
                    best = (move, sx)
            if columns:
                cs = _column_snap(m, which, end_s, columns)
                if cs is not None and (best is None or abs(cs - end_s) < abs(best[1] - end_s)):
                    best = (cs - end_s, cs)
            if best is not None and abs(best[1] - end_s) > 1e-6:
                moves.append((m, which, best[1]))

    applied = 0
    for m, which, sx in moves:
        new0, new1 = (sx, m.s1) if which == 0 else (m.s0, sx)
        if new1 - new0 < MIN_MEMBER_MM:
            continue
        m.s0, m.s1 = new0, new1
        applied += 1
    return applied


def _column_snap(m, which, end_s, columns):
    """Slide end onto a column only if the axis passes through the column footprint."""
    best = None
    for c in columns:
        cx, cy = c.centroid
        half_n = 0.5 * _extent_along(c, m.axis.n)
        if abs(m.axis.off_of((cx, cy)) - m.axis.offset) > half_n:
            continue       # axis misses the footprint
        sc = m.axis.s_of((cx, cy))
        reach = EXTEND_FACTOR * m.thickness + EXTEND_PAD_MM + 0.5 * _extent_along(c, m.axis.d)
        if abs(sc - end_s) > reach:
            continue
        # an end already reaching past the column centre would be trimmed to it: fine
        if best is None or abs(sc - end_s) < abs(best - end_s):
            best = sc
    return best


def _extent_along(col, direction):
    """Full width of a rectangular column's footprint projected on a unit vector."""
    r = math.radians(col.angle_deg)
    ex = (math.cos(r), math.sin(r))
    ey = (-math.sin(r), math.cos(r))
    return (abs(direction[0] * ex[0] + direction[1] * ex[1]) * col.width_mm +
            abs(direction[0] * ey[0] + direction[1] * ey[1]) * col.depth_mm)


# ── nodes / junction classification ──────────────────────────────────────

def build_nodes(members):
    """
    Cluster member ends and axis crossings into nodes.
    Returns list of dicts {"pt", "members": [idx...], "type": "end|L|T|X"}.
    """
    nodes = []

    def add(pt, mi, at_end):
        for nd in nodes:
            if math.hypot(nd["pt"][0] - pt[0], nd["pt"][1] - pt[1]) <= max(NODE_MERGE_MM, 2.0):
                if mi not in nd["members"]:
                    nd["members"].append(mi)
                nd["ends"][mi] = nd["ends"].get(mi, False) or at_end
                return
        nodes.append({"pt": pt, "members": [mi], "ends": {mi: at_end}})

    for i, m in enumerate(members):
        add(m.p1, i, True)
        add(m.p2, i, True)
    # interior contacts (a member end lands on another's span)
    for i, m in enumerate(members):
        for j, o in enumerate(members):
            if i == j:
                continue
            X = intersect_axes(m.axis, o.axis)
            if X is None:
                continue
            si, so = m.axis.s_of(X), o.axis.s_of(X)
            if m.s0 - 1.0 <= si <= m.s1 + 1.0 and o.s0 - 1.0 <= so <= o.s1 + 1.0:
                at_end_i = min(abs(si - m.s0), abs(si - m.s1)) <= 2.0
                at_end_j = min(abs(so - o.s0), abs(so - o.s1)) <= 2.0
                add(X, i, at_end_i)
                add(X, j, at_end_j)
    for nd in nodes:
        ends = [v for v in nd["ends"].values()]
        k = len(nd["members"])
        if k == 1:
            nd["type"] = "end"
        elif k == 2 and all(ends):
            nd["type"] = "L"
        elif k == 2:
            nd["type"] = "T"
        else:
            nd["type"] = "X" if not any(ends) or sum(1 for v in ends if not v) >= 2 else "T"
    return nodes


# ── slabs from the member graph ──────────────────────────────────────────

def _clean_ring(coords):
    """Drop repeated and collinear vertices; return list of (x, y) without closing point."""
    pts = [tuple(c[:2]) for c in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            scale = max(math.hypot(b[0] - a[0], b[1] - a[1]) * math.hypot(c[0] - b[0], c[1] - b[1]), 1e-9)
            if abs(cross) / scale < 1e-6 or (a == b) or (b == c):
                del pts[i]
                changed = True
                break
    return pts


def polygon_ccw_vertices(poly: Polygon):
    """All n vertices in CCW order with collinear points removed."""
    return _clean_ring(list(orient(poly, sign=1.0).exterior.coords))


def slab_faces(members, slab_polys, void_points=(), void_polys=(), log=lambda m: None):
    """
    Polygonize the member graph.

    members     -- wall/beam Members (axis segments are the graph edges)
    slab_polys  -- [{"vertices", "thickness_mm", "handle", ...}] original slab outlines; used to
                   seed thickness per face and as virtual edges only where no member supports them
    void_points -- [(x, y)] points (STAIRCASE/LIFT/SHAFT text) marking voids

    Returns (faces, warnings) where faces = [{"poly", "thickness_mm", "sources": [idx...]}].
    """
    warnings = []
    if not members or not slab_polys:
        return [], warnings

    usable = [m for m in members if m.length > 1.0]
    # 1 mm overshoot at both ends so exact-touch T/L nodes are really noded (dangles are ignored by polygonize)
    axes = []
    for m in usable:
        d = m.axis.d
        a, b = m.p1, m.p2
        axes.append(LineString([(a[0] - d[0], a[1] - d[1]), (b[0] + d[0], b[1] + d[1])]))
    support = unary_union([ls.buffer(m.thickness / 2.0 + SLAB_SUPPORT_PAD_MM, cap_style=2)
                           for ls, m in zip(axes, usable)])

    virtual = []
    slab_shapes = []
    for sp in slab_polys:
        poly = Polygon(sp["vertices"])
        if not poly.is_valid:
            poly = poly.buffer(0)
        slab_shapes.append(poly)
        ring = LineString(list(poly.exterior.coords))
        unsupported = ring.difference(support)
        if not unsupported.is_empty:
            virtual.append(unsupported)

    noded = unary_union(axes + virtual)
    faces = []
    for face in polygonize(noded):
        if face.area < 1.0e4:          # < 0.01 m2: sliver
            continue
        if void_polys:
            vu = unary_union(list(void_polys))
            cut = face.difference(vu)
            if not cut.is_empty:
                parts = [g for g in getattr(cut, 'geoms', [cut]) if g.geom_type == 'Polygon']
                if parts:
                    face = Polygon(max(parts, key=lambda g: g.area).exterior)   # notches removed; interior holes stay slab
        rep = face.representative_point()
        # thickness seed: slab outlines overlapping this face
        overlaps = []
        for idx, sh in enumerate(slab_shapes):
            if sh.is_empty:
                continue
            a = face.intersection(sh).area
            if a > 0:
                overlaps.append((a, idx))
        covered = sum(a for a, _ in overlaps) / face.area
        if any(face.buffer(0).contains(Point(p)) for p in void_points):
            log("Slab face containing STAIRCASE/LIFT/SHAFT text treated as a void.")
            continue
        if covered < SLAB_MIN_COVERAGE:
            continue                    # no slab under it: void / shaft
        overlaps.sort(reverse=True)
        thks = {slab_polys[i].get("thickness_mm", 0) for a, i in overlaps if a / face.area >= 0.2}
        if len(thks) > 1:
            warnings.append(
                f"Slab face near ({rep.x:.0f}, {rep.y:.0f}) covers slab outlines of different thickness "
                f"{sorted(thks)}: a supporting wall/beam is probably missing.")
        best = overlaps[0][1]
        faces.append({"poly": face, "thickness_mm": slab_polys[best].get("thickness_mm", 0),
                      "sources": [i for _, i in overlaps], "mixed": len(thks) > 1})
    return faces, warnings
