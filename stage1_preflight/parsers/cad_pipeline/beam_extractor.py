"""
Beam extraction.

Convention (ported from the concrete-qty-takeoff-from-dxf reference tool):
  - Beams are NOT centerlines. They're drawn as either:
      (a) a closed LWPOLYLINE rectangle outline, or
      (b) a pair of parallel LINEs (the two long edges of the beam strip).
  - Width & depth come from a nearby TEXT/MTEXT label like "B 300X1350/850"
    or "PB1 230x450" (name, width, depth[/depth2...] -- max depth taken),
    matched to the nearest beam footprint whose geometric width is within
    tolerance. Depth is a vertical dimension invisible in plan view, so it
    can only ever come from text (or, as a fallback, the layer name).
  - Each text label is consumed at most once (used_labels tracking) and only
    matched within MAX_BEAM_LABEL_DISTANCE_MM of the beam centroid to prevent
    distant labels from mis-matching.
  - Explicit p1/p2 centerline endpoints are stored on BeamEntry so that the
    connectivity pass and ETABS writer can use them directly rather than
    re-deriving them from centroid + angle + length (which is lossy after
    snapping moves one endpoint more than the other).
"""
import math
import re
from dataclasses import dataclass, field

from .geometry import distance, pair_parallel_segments, point_line_distance, check_lines_overlap
from .members import Axis, angle_diff, dominant_angles, member_from_points, norm_angle
from .dxf_helpers import (
    extract_closed_polygons, extract_lines, extract_text_entities,
    scale_polygon_dicts, scale_line_dicts, scale_text_dicts,
)

WIDTH_TOLERANCE_MM = 50
MIN_BEAM_LENGTH_MM = 500
MIN_BEAM_WIDTH_MM = 50
MAX_BEAM_WIDTH_MM = 1500
MAX_BEAM_LABEL_DISTANCE_MM = 5000
LABEL_WIDTH_TOL_MM = 2       # face gap must match the label width within this (real pairs are exact)
LABEL_ROTATION_TOL_DEG = 10  # face direction must match the label rotation (mod 180)


@dataclass
class VirtualBeam:
    p1: tuple[float, float]
    p2: tuple[float, float]
    calc_width: float
    calc_length: float
    is_rect: bool
    layer: str = ""


@dataclass
class BeamEntry:
    name: str
    layer: str
    width_mm: float
    depth_mm: float
    length_m: float
    centroid: tuple[float, float]
    angle_deg: float
    p1: tuple[float, float]
    p2: tuple[float, float]
    dimension_source: str  # "text_label" | "layer_name" | "unmatched"
    warnings: list[str] = field(default_factory=list)
    tag: str = ""           # label suffix metadata: INV, BRACKET, ...
    source_handles: list = field(default_factory=list)


_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[X*]\s*(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*)")
_TAG_WORDS = {"INV", "INV.", "INVERTED", "RC", "BRACKET", "CANT", "CANTILEVER", "LINTEL", "DROP", "TYP", "TYP."}


def parse_dim_text(text: str, name_prefix_pattern: str = r"[A-Z]*") -> tuple[str, float, float]:
    """
    Parses strings like 'B 300X1350/850', 'PB1 230x450' or 'INV. B2 300X750' -> (name, width, depth).
    If multiple depths are given (stepped/haunched beam, separated by '/'), the maximum is taken.
    Words such as INV / RC BRACKET are tags, not the name (see _beam_tag).
    """
    up = text.upper().strip()
    m = _DIM_RE.search(up)
    if not m:
        return "BEAM", 0.0, 0.0
    try:
        width = float(m.group(1))
        depths = [float(d) for d in re.split(r"\s*/\s*", m.group(2)) if d]
        depth = max(depths) if depths else 0.0
    except ValueError:
        return "BEAM", 0.0, 0.0
    words = [w for w in re.findall(r"[A-Z][A-Z0-9\-]*\.?", up[:m.start()]) if w not in _TAG_WORDS]
    return (words[-1] if words else "BEAM"), width, depth


def _beam_tag(raw: str) -> str:
    """Suffix metadata such as 'INV.' or 'RC BRACKET' carried on the label."""
    up = raw.upper()
    tags = [t for t in ("INV", "BRACKET", "CANT", "LINTEL", "DROP") if t in up]
    return ",".join(tags)


def _extract_dim_texts(msp, scale: float = 1.0) -> list[dict]:
    """All TEXT/MTEXT entities that look like a 'WxD' dimension label."""
    texts = []
    raw_texts = extract_text_entities(msp)
    scale_text_dicts(raw_texts, scale)
    for label in raw_texts:
        raw = label["text"]
        if "X" in raw.upper() or "*" in raw:
            name, w, d = parse_dim_text(raw)
            if w > 0 and d > 0:
                texts.append({"raw": raw, "name": name, "width": w, "depth": d,
                              "position": label["position"], "rotation": label.get("rotation", 0.0),
                              "tag": _beam_tag(raw)})
    return texts


# ── face pairing ─────────────────────────────────────────────────────────

def _seg_angle(s):
    return norm_angle(math.degrees(math.atan2(s[1][1] - s[0][1], s[1][0] - s[0][0])))


def _pair_to_beam(sa, sb, layer, handles):
    """
    Axis from a pair of faces: angle = length-weighted mean of the two face angles (doubled-angle
    vectors), offset = mean of the two face offsets, interval = union of the faces' spans.
    """
    la, lb = distance(*sa), distance(*sb)
    vx = la * math.cos(math.radians(2 * _seg_angle(sa))) + lb * math.cos(math.radians(2 * _seg_angle(sb)))
    vy = la * math.sin(math.radians(2 * _seg_angle(sa))) + lb * math.sin(math.radians(2 * _seg_angle(sb)))
    ang = norm_angle(math.degrees(math.atan2(vy, vx)) / 2.0)
    ax = Axis(ang, 0.0, 0.0)
    offs = [ax.off_of(sa[0]), ax.off_of(sa[1]), ax.off_of(sb[0]), ax.off_of(sb[1])]
    oa, ob = (offs[0] + offs[1]) / 2.0, (offs[2] + offs[3]) / 2.0
    ax.offset = (oa + ob) / 2.0
    ax.thickness = abs(oa - ob)
    ss = [ax.s_of(p) for p in (*sa, *sb)]
    return VirtualBeam(ax.point(min(ss)), ax.point(max(ss)), ax.thickness, max(ss) - min(ss),
                       is_rect=False, layer=layer), handles


def _pair_faces_with_label(segs, used, label):
    """Best unused face pair matching a label's width and rotation (mod 180)."""
    w, rot = label["width"], label["rotation"]
    best, best_score = None, None
    cand = [i for i in range(len(segs)) if i not in used and
            angle_diff(_seg_angle(segs[i]), rot) <= LABEL_ROTATION_TOL_DEG]
    for ai in range(len(cand)):
        for bi in range(ai + 1, len(cand)):
            i, j = cand[ai], cand[bi]
            if angle_diff(_seg_angle(segs[i]), _seg_angle(segs[j])) > 2.0:
                continue
            gap = point_line_distance(segs[j][0], segs[i][0], segs[i][1])
            if abs(gap - w) > LABEL_WIDTH_TOL_MM:
                continue
            if check_lines_overlap(segs[i][0], segs[i][1], segs[j][0], segs[j][1]) < 0.5 * min(
                    distance(*segs[i]), distance(*segs[j])):
                continue
            vb, _ = _pair_to_beam(segs[i], segs[j], "", [])
            # attach by projection on the axis, not by nearest centroid
            ax, s0, s1 = Axis.from_points(vb.p1, vb.p2, vb.calc_width)
            sp = ax.s_of(label["position"])
            perp = abs(ax.off_of(label["position"]) - ax.offset)
            along = 0.0 if s0 <= sp <= s1 else min(abs(sp - s0), abs(sp - s1))
            if perp > MAX_BEAM_LABEL_DISTANCE_MM or along > MAX_BEAM_LABEL_DISTANCE_MM:
                continue
            score = perp + along + 20.0 * abs(gap - w)      # an exact-width pair beats a near-width one
            if best_score is None or score < best_score:
                best, best_score = (i, j), score
    return best


def _build_virtual_beams(msp, beam_layers, scale=1.0, ledger=None, dim_texts=None):
    """Returns [(VirtualBeam, handles, label_or_None)]."""
    from shapely.geometry import Polygon
    from . import ledger as L
    from .wall_regions import decompose_region, rect_stats

    if not beam_layers:
        return []
    open_polys = []
    polys = extract_closed_polygons(msp, beam_layers, ledger=ledger, open_out=open_polys)
    scale_polygon_dicts(polys, scale)
    lines = extract_lines(msp, beam_layers, ledger=ledger)
    scale_line_dicts(lines, scale)
    # open polylines are beam faces drawn as chains: each edge is a face segment
    scale_polygon_dicts(open_polys, scale)
    for op in open_polys:
        for a, b in zip(op["vertices"][:-1], op["vertices"][1:]):
            lines.append({"layer": op["layer"], "p1": a, "p2": b, "handle": op["handle"]})

    out = []

    # Closed polygons: any vertex count (no truncation to the first 4).
    for poly in polys:
        sh = Polygon(poly["vertices"])
        if not sh.is_valid:
            sh = sh.buffer(0)
        if sh.is_empty or sh.area < 1.0:
            if ledger:
                ledger.reject(poly["handle"], L.TOO_FEW_VERTICES, "degenerate beam polygon", poly["layer"], "beam")
            continue
        fill, aspect, length, width, _ = rect_stats(sh)
        legs, how = decompose_region(sh, [])
        if not legs or (how == "simple" and not (MIN_BEAM_WIDTH_MM <= width <= MAX_BEAM_WIDTH_MM)):
            if ledger:
                ledger.reject(poly["handle"], L.COMPOSITE_UNRESOLVED if not legs else L.THICKNESS_OUT_OF_SET,
                              f"beam polygon not usable (fill {fill:.2f}, width {width:.0f})", poly["layer"], "beam")
            continue
        for leg in legs:
            out.append((VirtualBeam(leg["p1"], leg["p2"], leg["width"],
                                    distance(leg["p1"], leg["p2"]), is_rect=True, layer=poly["layer"]),
                        [poly["handle"]], None))
        if ledger:
            ledger.consume(poly["handle"], layer=poly["layer"], kind="beam")

    # Lines: two long faces make one beam.
    long_idx = []
    for k, ln in enumerate(lines):
        if distance(ln["p1"], ln["p2"]) >= MIN_BEAM_LENGTH_MM:
            long_idx.append(k)
        elif ledger:
            ledger.reject(ln["handle"], L.TOO_SHORT, f"line shorter than {MIN_BEAM_LENGTH_MM}mm", ln["layer"], "beam")
    segs = [(lines[k]["p1"], lines[k]["p2"]) for k in long_idx]
    used = set()

    # 1) label-driven: labels give width and direction
    for label in (dim_texts or []):
        pair = _pair_faces_with_label(segs, used, label)
        if pair is None:
            continue
        i, j = pair
        used.update(pair)
        layer = lines[long_idx[i]]["layer"]
        vb, _ = _pair_to_beam(segs[i], segs[j], layer, [])
        out.append((vb, [lines[long_idx[i]]["handle"], lines[long_idx[j]]["handle"]], label))

    # 2) anything left: plain parallel-face pairing
    rest = [k for k in range(len(segs)) if k not in used]
    rest_segs = [segs[k] for k in rest]
    paired = set()
    for pair in pair_parallel_segments(rest_segs, MIN_BEAM_WIDTH_MM, MAX_BEAM_WIDTH_MM):
        a, b = pair["source_indices"]
        ia, ib = rest[a], rest[b]
        paired.update((ia, ib))
        layer = lines[long_idx[ia]]["layer"]
        vb, _ = _pair_to_beam(segs[ia], segs[ib], layer, [])
        out.append((vb, [lines[long_idx[ia]]["handle"], lines[long_idx[ib]]["handle"]], None))
    used |= paired

    if ledger:
        for k in range(len(segs)):
            ln = lines[long_idx[k]]
            if k in used:
                ledger.consume(ln["handle"], layer=ln["layer"], kind="beam")
            else:
                ledger.reject(ln["handle"], L.NOT_PAIRED, "no parallel face within beam width range",
                              ln["layer"], "beam")
    return out


def extract_beams(msp, beam_layers: list[str], scale: float = 1.0, ledger=None) -> list[BeamEntry]:
    dim_texts = _extract_dim_texts(msp, scale)
    built = _build_virtual_beams(msp, beam_layers, scale, ledger, dim_texts)
    all_segs = [(vb.p1, vb.p2) for vb, _, _ in built]
    dominant = dominant_angles(all_segs)

    used_labels: set[int] = set()
    entries = []
    for vb, handles, direct_label in built:
        m = member_from_points("beam", vb.p1, vb.p2, vb.calc_width, snap_angles=dominant)
        p1, p2 = m.p1, m.p2
        cx, cy = m.centroid
        length_m = m.length / 1000.0
        angle = m.angle_deg

        best_idx = -1
        if direct_label is not None:
            best_idx = next((i for i, t in enumerate(dim_texts) if t is direct_label), -1)
            used_labels.add(best_idx)
        else:
            best_dist = float("inf")
            for idx, txt in enumerate(dim_texts):
                if idx in used_labels:
                    continue
                if abs(txt["width"] - vb.calc_width) >= WIDTH_TOLERANCE_MM:
                    continue
                if angle_diff(txt["rotation"], angle) > LABEL_ROTATION_TOL_DEG and txt["rotation"] != 0.0:
                    continue
                # attach by projection on the axis, then by distance
                sp = m.axis.s_of(txt["position"])
                perp = abs(m.axis.off_of(txt["position"]) - m.axis.offset)
                along = 0.0 if m.s0 <= sp <= m.s1 else min(abs(sp - m.s0), abs(sp - m.s1))
                d = perp + along
                if d < best_dist and perp <= MAX_BEAM_LABEL_DISTANCE_MM and along <= MAX_BEAM_LABEL_DISTANCE_MM:
                    best_dist, best_idx = d, idx
            if best_idx != -1:
                used_labels.add(best_idx)

        if best_idx != -1:
            t = dim_texts[best_idx]
            entries.append(BeamEntry(
                name=t["name"], layer=vb.layer, width_mm=t["width"], depth_mm=t["depth"],
                length_m=round(length_m, 3), centroid=(round(cx, 1), round(cy, 1)),
                angle_deg=round(angle, 2), p1=p1, p2=p2, dimension_source="text_label",
                tag=t["tag"], source_handles=handles,
            ))
        else:
            name, w, d = parse_dim_text(vb.layer)
            warnings = []
            source = "layer_name"
            if w <= 0 or d <= 0:
                w, d = vb.calc_width, 0.0
                source = "unmatched"
                warnings.append(
                    f"Beam on layer '{vb.layer}' near ({cx:.0f}, {cy:.0f}): no matching WxD text label found; "
                    f"depth is unknown."
                )
            entries.append(BeamEntry(
                name=name if w > 0 else "BEAM", layer=vb.layer, width_mm=w, depth_mm=d,
                length_m=round(length_m, 3), centroid=(round(cx, 1), round(cy, 1)),
                angle_deg=round(angle, 2), p1=p1, p2=p2,
                dimension_source=source, warnings=warnings, source_handles=handles,
            ))
    return entries
