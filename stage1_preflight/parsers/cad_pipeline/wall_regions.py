"""
Stage 2/3: wall regions, the simple-vs-composite gate and composite
decomposition into straight legs.

  * A polygon is a *simple* wall only if it fills its bounding rectangle
    (>= 0.9), has aspect >= 2 and a thickness inside this drawing's
    calibrated thickness set.  Anything else is composite and is never
    accepted as one wide wall.
  * Rectilinear composites (L/T/+/I/H/C/U ...) are cut into rectangles at
    reflex vertices (in both orientations; the better cut is kept) and
    collinear pieces are merged into maximal legs.
  * Everything else goes through a medial-axis skeleton (Voronoi of the
    densified boundary), spur pruning and chain fitting.

decompose_region() returns legs as dicts {"p1", "p2", "width"}.  Axis angles
are snapped and nodes are made exact later (members.py / graph.py).
"""
import math
import statistics

import shapely
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import voronoi_diagram

SIMPLE_FILL_MIN = 0.90
SIMPLE_ASPECT_MIN = 2.0
CLEAN_FILL_MIN = 0.95
CLEAN_ASPECT_MIN = 3.0
PLAUSIBLE_THICKNESS_MM = (50.0, 1200.0)
MIN_SLIVER_MM = 20.0


# ── thickness set ────────────────────────────────────────────────────────

def snap_thickness(t: float, tset) -> float:
    """Snap to the nearest calibrated thickness if close, else to the nearest 5 mm."""
    if tset:
        best = min(tset, key=lambda v: abs(v - t))
        if abs(best - t) <= max(10.0, 0.06 * t):
            return float(best)
    return float(round(t / 5.0) * 5)


def in_thickness_set(t: float, tset) -> bool:
    if not tset:
        return PLAUSIBLE_THICKNESS_MM[0] <= t <= PLAUSIBLE_THICKNESS_MM[1]
    return any(abs(v - t) <= max(10.0, 0.06 * t) for v in tset)


def rect_stats(poly: Polygon):
    """(fill, aspect, length, width, angle_deg) of the minimum rotated rectangle."""
    mrr = poly.minimum_rotated_rectangle
    if mrr.geom_type != "Polygon":
        return 0.0, 0.0, 0.0, 0.0, 0.0
    c = list(mrr.exterior.coords)
    e1 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e2 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    if l1 >= l2:
        length, width, e = l1, l2, e1
    else:
        length, width, e = l2, l1, e2
    if width <= 0:
        return 0.0, 0.0, length, width, 0.0
    return (poly.area / mrr.area if mrr.area else 0.0, length / width, length, width,
            math.degrees(math.atan2(e[1], e[0])) % 180.0)


def calibrate_thickness_set(polys, label_values=(), layer_values=()):
    """
    Wall thicknesses used in this drawing: short sides of clean rectangles
    (fill >= 0.95, aspect >= 3), plus "NNN THK" labels and layer numbers.
    """
    vals = []
    for p in polys:
        fill, aspect, length, width, _ = rect_stats(p)
        if fill >= CLEAN_FILL_MIN and aspect >= CLEAN_ASPECT_MIN and \
                PLAUSIBLE_THICKNESS_MM[0] <= width <= PLAUSIBLE_THICKNESS_MM[1]:
            vals.append(round(width / 5.0) * 5)
    vals += [v for v in label_values if PLAUSIBLE_THICKNESS_MM[0] <= v <= PLAUSIBLE_THICKNESS_MM[1]]
    vals += [v for v in layer_values if PLAUSIBLE_THICKNESS_MM[0] <= v <= PLAUSIBLE_THICKNESS_MM[1]]
    out = []
    for v in sorted(set(vals)):
        if not out or v - out[-1] > 10:
            out.append(float(v))
    return out


def is_simple_wall(poly: Polygon, tset) -> bool:
    fill, aspect, _, width, _ = rect_stats(poly)
    return fill >= SIMPLE_FILL_MIN and aspect >= SIMPLE_ASPECT_MIN and in_thickness_set(width, tset)


# ── rectilinear decomposition ────────────────────────────────────────────

def _edge_frame(poly: Polygon):
    """Angle theta (deg, 0..90) such that >= 98% of the boundary length runs at theta or theta+90, else None."""
    coords = list(poly.exterior.coords)
    for r in poly.interiors:
        coords += list(r.coords)
    lens, angs = [], []
    ring = list(poly.exterior.coords)
    segs = list(zip(ring[:-1], ring[1:]))
    for r in poly.interiors:
        c = list(r.coords)
        segs += list(zip(c[:-1], c[1:]))
    for a, b in segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-6:
            continue
        lens.append(L)
        angs.append(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0)
    if not lens:
        return None
    # circular weighted mean around the dominant (longest) edge
    ref = angs[max(range(len(lens)), key=lambda i: lens[i])]
    tot = sum(lens)
    good, acc = 0.0, 0.0
    for L, a in zip(lens, angs):
        d = (a - ref + 45.0) % 90.0 - 45.0
        if abs(d) <= 1.5:
            good += L
            acc += L * d
    if good < 0.98 * tot:
        return None
    return (ref + acc / good) % 90.0


def _strip_rects(poly: Polygon):
    """Horizontal strips cut at every vertex y, merged vertically when the x-range matches."""
    ys = set()
    for ring in [poly.exterior] + list(poly.interiors):
        for x, y in ring.coords:
            ys.add(round(y, 3))
    ys = sorted(ys)
    minx, _, maxx, _ = poly.bounds
    rects = []
    open_ = {}
    for y0, y1 in zip(ys[:-1], ys[1:]):
        if y1 - y0 < 1e-3:
            continue
        piece = poly.intersection(box(minx - 1, y0, maxx + 1, y1))
        geoms = getattr(piece, "geoms", [piece])
        now = {}
        for g in geoms:
            if g.geom_type != "Polygon" or g.area < 1.0:
                continue
            gx0, gy0, gx1, gy1 = g.bounds
            if g.area < 0.98 * (gx1 - gx0) * (gy1 - gy0):
                continue                    # not a clean rectangle (sliver artefact): leave to the other cut
            key = (round(gx0, 1), round(gx1, 1))
            if key in open_ and abs(open_[key][3] - y0) < 1e-3:
                r = open_[key]
                r[3] = y1
                now[key] = r
            else:
                r = [gx0, y0, gx1, y1]
                rects.append(r)
                now[key] = r
        open_ = now
    return rects


def _rects_to_legs(rects):
    legs = []
    for x0, y0, x1, y1 in rects:
        w, h = x1 - x0, y1 - y0
        if min(w, h) < MIN_SLIVER_MM:
            continue
        if w >= h:
            yc = (y0 + y1) / 2.0
            legs.append({"p1": (x0, yc), "p2": (x1, yc), "width": h})
        else:
            xc = (x0 + x1) / 2.0
            legs.append({"p1": (xc, y0), "p2": (xc, y1), "width": w})
    return legs


def _rot_pt(p, ang):
    r = math.radians(ang)
    c, s = math.cos(r), math.sin(r)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _merge_collinear(legs, poly: Polygon):
    """Merge same-axis, same-thickness legs separated by <= 1.05 t where the polygon fills the gap."""
    def key(l):
        horiz = abs(l["p2"][1] - l["p1"][1]) < 1e-6
        off = l["p1"][1] if horiz else l["p1"][0]
        return horiz, off

    changed = True
    legs = [dict(l) for l in legs]
    while changed:
        changed = False
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                a, b = legs[i], legs[j]
                ha, oa = key(a)
                hb, ob = key(b)
                if ha != hb or abs(oa - ob) > 2.0 or abs(a["width"] - b["width"]) > 5.0:
                    continue
                ia = 0 if ha else 1
                a0, a1 = sorted((a["p1"][ia], a["p2"][ia]))
                b0, b1 = sorted((b["p1"][ia], b["p2"][ia]))
                gap = max(b0 - a1, a0 - b1)
                if gap > 1.05 * a["width"]:
                    continue
                if gap > 1.0:
                    gs, ge = (a1, b0) if b0 >= a1 else (b1, a0)
                    hw = a["width"] / 2.0
                    gap_rect = box(gs, oa - hw, ge, oa + hw) if ha else box(oa - hw, gs, oa + hw, ge)
                    # the gap must be solid wall across the full thickness, not bridged by a thin bar
                    if poly.buffer(1.0).intersection(gap_rect).area < 0.9 * gap_rect.area:
                        continue
                lo, hi = min(a0, b0), max(a1, b1)
                if ha:
                    legs[i] = {"p1": (lo, oa), "p2": (hi, oa), "width": max(a["width"], b["width"])}
                else:
                    legs[i] = {"p1": (oa, lo), "p2": (oa, hi), "width": max(a["width"], b["width"])}
                del legs[j]
                changed = True
                break
            if changed:
                break
    return legs


def _decompose_rectilinear(poly: Polygon, theta: float, tset):
    frame = rotate(poly, -theta, origin=(0, 0))
    cands = []
    for extra in (0.0, 90.0):
        p = rotate(frame, -extra, origin=(0, 0)) if extra else frame
        legs = _rects_to_legs(_strip_rects(p))
        legs = _merge_collinear(legs, p)
        # back to the base frame, then to the world frame
        back = []
        for l in legs:
            q1 = _rot_pt(l["p1"], extra + theta)
            q2 = _rot_pt(l["p2"], extra + theta)
            back.append({"p1": q1, "p2": q2, "width": l["width"]})
        bad = sum(1 for l in back if not in_thickness_set(l["width"], tset))
        cands.append((bad * 5 + len(back), back))
    cands.sort(key=lambda c: c[0])
    return cands[0][1]


# ── skeleton (oblique / non-rectilinear) ─────────────────────────────────

def _flatten(g):
    if hasattr(g, "geoms"):
        for sub in g.geoms:
            yield from _flatten(sub)
    else:
        yield g


def _densify(ring_coords, step):
    pts = []
    for a, b in zip(ring_coords[:-1], ring_coords[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(L / step)))
        for k in range(n):
            t = k / n
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return pts


def _decompose_skeleton(poly: Polygon, tset):
    t_est = max(2.0 * poly.area / max(poly.length, 1e-6), 20.0)
    step = max(t_est / 4.0, 20.0)
    while True:
        pts = _densify(list(poly.exterior.coords), step)
        for r in poly.interiors:
            pts += _densify(list(r.coords), step)
        if len(pts) <= 6000:
            break
        step *= 1.5
    vd = voronoi_diagram(MultiPoint(pts), edges=True)
    inner = poly.buffer(-0.02 * t_est)
    edges = []
    for g in _flatten(vd):
        cs = list(g.coords)
        for a, b in zip(cs[:-1], cs[1:]):
            if inner.contains(Point(a)) and inner.contains(Point(b)):
                ra, rb = poly.boundary.distance(Point(a)), poly.boundary.distance(Point(b))
                edges.append((a, b, ra, rb))
    if not edges:
        return []
    r_typ = statistics.median([min(e[2], e[3]) for e in edges])
    if tset:
        r_typ = min(r_typ, max(tset) / 2.0) if r_typ > max(tset) / 2.0 else r_typ
    edges = [e for e in edges if min(e[2], e[3]) >= 0.6 * r_typ]

    # graph
    def k(p):
        return (round(p[0], 1), round(p[1], 1))
    adj = {}
    rad = {}
    for a, b, ra, rb in edges:
        ka, kb = k(a), k(b)
        if ka == kb:
            continue
        adj.setdefault(ka, set()).add(kb)
        adj.setdefault(kb, set()).add(ka)
        rad[ka], rad[kb] = ra, rb

    def prune():
        changed = True
        while changed:
            changed = False
            for n in [n for n, nb in adj.items() if len(nb) == 1]:
                # walk the spur to the first junction
                path, cur, prev = [n], next(iter(adj[n])), n
                while len(adj.get(cur, ())) == 2:
                    path.append(cur)
                    nxt = [x for x in adj[cur] if x != prev][0]
                    prev, cur = cur, nxt
                length = sum(math.hypot(path[i][0] - path[i + 1][0], path[i][1] - path[i + 1][1])
                             for i in range(len(path) - 1)) + \
                    math.hypot(path[-1][0] - cur[0], path[-1][1] - cur[1])
                if len(adj.get(cur, ())) >= 3 and length < 1.0 * (2 * r_typ):
                    for p in path:
                        for q in list(adj.get(p, ())):
                            adj[q].discard(p)
                        adj.pop(p, None)
                    changed = True
    prune()

    # chains between junction/end nodes
    seen = set()
    chains = []
    special = [n for n, nb in adj.items() if len(nb) != 2]
    starts = special or list(adj)[:1]
    for s in starts:
        for nb in list(adj.get(s, ())):
            if (s, nb) in seen:
                continue
            chain, prev, cur = [s], s, nb
            seen.add((s, nb))
            while True:
                chain.append(cur)
                if len(adj[cur]) != 2 or cur == s:
                    break
                nxt = [x for x in adj[cur] if x != prev][0]
                seen.add((cur, nxt))
                prev, cur = cur, nxt
            seen.add((chain[-1], chain[-2]))
            chains.append(chain)

    legs = []
    for chain in chains:
        if len(chain) < 2:
            continue
        line = LineString(chain).simplify(0.2 * 2 * r_typ)
        cs = list(line.coords)
        for a, b in zip(cs[:-1], cs[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 0.5 * 2 * r_typ:
                continue
            # local widths of the chain points inside this segment
            seg = LineString([a, b])
            ws = [2.0 * rad[c] for c in chain if seg.distance(Point(c)) < 0.3 * 2 * r_typ]
            width = statistics.median(ws) if ws else 2 * r_typ
            legs.append({"p1": tuple(a), "p2": tuple(b), "width": width})
    # extend each leg's ends along its own axis to the polygon boundary
    out = []
    for l in legs:
        dx, dy = l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1]
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        far = 4.0 * r_typ
        ext = LineString([(l["p1"][0] - ux * far, l["p1"][1] - uy * far),
                          (l["p2"][0] + ux * far, l["p2"][1] + uy * far)]).intersection(poly)
        if not ext.is_empty:
            parts = [g for g in getattr(ext, "geoms", [ext]) if g.geom_type == "LineString"]
            mid = Point((l["p1"][0] + l["p2"][0]) / 2, (l["p1"][1] + l["p2"][1]) / 2)
            parts.sort(key=lambda g: g.distance(mid))
            if parts:
                c = list(parts[0].coords)
                l = {"p1": c[0], "p2": c[-1], "width": l["width"]}
        out.append(l)
    # junction fragments: a very short leg between two longer ones is just the node
    if len(out) >= 3:
        def _len(l):
            return math.hypot(l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1])
        out = [l for l in out if _len(l) >= 2.5 * l["width"] or
               sum(1 for o in out if o is not l and _len(o) >= 2.5 * o["width"]) < 2]
    return out


# ── entry point ──────────────────────────────────────────────────────────

def decompose_region(poly: Polygon, tset):
    """
    Decompose one wall region into straight legs [{"p1","p2","width"}].
    Returns ([], reason) if nothing usable could be found.
    """
    poly = shapely.set_precision(poly, 1.0)          # 1 mm grid: no hair-thin sliver strips
    poly = poly.simplify(0.5, preserve_topology=True)
    if poly.geom_type != "Polygon":
        parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
        if not parts:
            return [], "empty"
        poly = max(parts, key=lambda g: g.area)
    if poly.is_empty or poly.area < 1.0:
        return [], "empty"
    fill, aspect, length, width, angle = rect_stats(poly)
    if is_simple_wall(poly, tset):
        rad = math.radians(angle)
        c = poly.minimum_rotated_rectangle.centroid
        dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
        return [{"p1": (c.x - dx, c.y - dy), "p2": (c.x + dx, c.y + dy), "width": width}], "simple"
    theta = _edge_frame(poly)
    if theta is not None:
        legs = _decompose_rectilinear(poly, theta, tset)
        if legs:
            return legs, "rectilinear"
    legs = _decompose_skeleton(poly, tset)
    if legs:
        return legs, "skeleton"
    return [], "composite_unresolved"
