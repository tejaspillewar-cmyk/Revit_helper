"""
Wall & slab extraction.

Convention (ported from the concrete-qty-takeoff-from-dxf reference tool,
extended for walls -- see _resolve_thickness_and_warnings below):
  - Walls/slabs are drawn as closed LWPOLYLINE footprint rectangles.
  - A nearby TEXT/MTEXT label containing "...NNN THK..." gives an explicit
    thickness for the polygon it sits inside (matched by point-in-polygon);
    this always wins when present, since it's the engineer's own annotation.
  - Slabs: thickness is a vertical dimension invisible in plan view, so
    without a text label it can only come from the layer name's trailing
    number (e.g. "STR-SLAB-REG150" -> 150).
  - Walls: thickness IS visible in plan view -- it's the short side of the
    wall's footprint rectangle. So without a text label, thickness is
    measured directly from the drawn geometry (via min_bounding_rectangle),
    not just assumed from the layer name. The layer name is only used as a
    plausibility cross-check, producing a warning on disagreement.
  - Bent/L/T walls drawn as ONE polygon: min_bounding_rectangle fits the
    polygon's *entire* extent, which for a bent shape wildly overstates its
    thickness (the fill ratio -- polygon area / bounding-box area -- is far
    below 1). Below BENT_WALL_FILL_RATIO_THRESHOLD, the polygon's own
    boundary edges are decomposed into straight legs via
    pair_parallel_segments -- the same parallel-edge-pairing technique
    beam_extractor.py uses for beams drawn as two parallel lines, just
    applied to a polygon's edges instead of independent LINE entities.
  - MULTIPLE polygons forming a wall system (T, L, cross junctions): grouped
    by vertex proximity, then medial axis extracts the combined centerline
    network with proper junction connectivity.
  - Explicit p1/p2 centerline endpoints are stored on WallEntry so that the
    connectivity pass and ETABS writer can use them directly rather than
    re-deriving them from centroid + angle + length (which is lossy after
    snapping moves one endpoint more than the other).
"""
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .geometry import (
    polygon_area_sqmm, sqmm_to_sqm, polygon_area_centroid, point_in_polygon, min_bounding_rectangle,
)
from .dxf_helpers import extract_closed_polygons, extract_hatch_polygons, extract_lines, extract_text_entities, scale_polygon_dicts, scale_text_dicts

THK_PATTERN = re.compile(r"(\d+)\s*(?:MM\s*)?THK", re.IGNORECASE)
WALL_THICKNESS_PLAUSIBLE_MM = (50, 1200)  # typical structural/partition wall range
BENT_WALL_FILL_RATIO_THRESHOLD = 0.85  # below this, decompose into straight legs
PLAUSIBLE_MIN_MM = 60.0  # legs thinner than this are slivers when a region has several legs
WALL_POLYGON_GROUP_GAP_MM = 50.0  # max gap between polygons to consider them one wall system


@dataclass
class SlabEntry:
    name: str
    layer: str
    thickness_mm: int
    thickness_source: str  # "text_label" | "layer_name" | "unknown"
    label_text: str
    area_sqm: float
    volume_cum: float
    centroid: tuple[float, float]
    vertices: list
    warnings: list[str] = field(default_factory=list)


@dataclass
class WallEntry:
    name: str
    layer: str
    is_structural: bool
    thickness_mm: int
    thickness_raw_mm: float  # unrounded value as measured/reported, before rounding to 5mm
    thickness_source: str  # "text_label" | "geometry" | "unknown"
    label_text: str
    area_sqm: float
    length_m: float
    angle_deg: float
    centroid: tuple[float, float]
    p1: tuple[float, float]
    p2: tuple[float, float]
    vertices: list
    warnings: list[str] = field(default_factory=list)
    group_id: str = ""   # legs of one composite shape (L/T/I...) share this id
    source_handles: list = field(default_factory=list)


# ── helpers ───────────────────────────────────────────────────────────────

def _endpoints_from_rect(center, angle_deg, length_mm):
    """Derive the two centerline endpoints from the MBR properties."""
    cx, cy = center
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * (length_mm / 2.0)
    dy = math.sin(rad) * (length_mm / 2.0)
    return (cx - dx, cy - dy), (cx + dx, cy + dy)


def _find_thk_labels(msp, scale: float = 1.0) -> list[dict]:
    """All TEXT/MTEXT entities anywhere in the drawing matching '...NNN THK...'."""
    labels = []
    raw_texts = extract_text_entities(msp)
    scale_text_dicts(raw_texts, scale)
    for label in raw_texts:
        match = THK_PATTERN.search(label["text"].upper())
        if match:
            labels.append({
                "text": label["text"],
                "thickness_mm": int(match.group(1)),
                "position": label["position"],
            })
    return labels


def _match_thickness(vertices, layer, thk_labels, used_labels) -> tuple[int, str, str, list[str]]:
    """Returns (thickness_mm, source, label_text, warnings) for one polygon."""
    warnings = []
    layer_thk = None
    numbers = re.findall(r"\d+", layer)
    if numbers:
        layer_thk = int(numbers[-1])

    for idx, label in enumerate(thk_labels):
        if idx in used_labels:
            continue
        lx, ly = label["position"]
        if point_in_polygon(lx, ly, vertices):
            used_labels.add(idx)
            thickness_mm = label["thickness_mm"]
            if layer_thk is not None and thickness_mm != layer_thk:
                warnings.append(
                    f"Layer '{layer}' implies {layer_thk}mm but text label says "
                    f"{thickness_mm}mm ('{label['text']}'); using the text label."
                )
            return thickness_mm, "text_label", label["text"], warnings

    if layer_thk is not None:
        return layer_thk, "layer_name", f"(from layer: {layer})", warnings

    warnings.append(f"Polygon on layer '{layer}': no THK text label and no thickness in the layer name.")
    return 0, "unknown", "UNKNOWN", warnings


# ── slab extraction ───────────────────────────────────────────────────────

CUTOUT_LAYER_PATTERN = re.compile(r"CUTOUT|OPENING|VOID|SHAFT", re.IGNORECASE)


def _cutout_voids(msp, layers, scale, ledger=None):
    """
    Cutout/opening layers hold slab holes drawn as closed polylines, open chains and plain LINEs.
    Polygonize all of their segments and return the enclosed regions as shapely polygons.
    """
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    if not layers:
        return []
    lay = set(layers)
    segs, handles = [], []
    for e in msp:
        if e.dxf.get("layer", "") not in lay:
            continue
        t = e.dxftype()
        h = e.dxf.get("handle", "")
        pts = None
        if t == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif t == "LWPOLYLINE":
            pts = list(e.get_points(format="xy"))
            if e.closed and pts:
                pts.append(pts[0])
        if pts and len(pts) >= 2:
            pts = [(x * scale, y * scale) for x, y in pts]
            segs.append(LineString(pts))
            handles.append(h)
            if ledger:
                ledger.see(h, e.dxf.get("layer", ""), "cutout")
    if not segs:
        return []
    voids = [f for f in polygonize(unary_union(segs)) if f.area > 5.0e4]     # > 0.05 m2
    for h in handles:
        if ledger:
            ledger.consume(h, member="void", kind="cutout")
    return voids


def extract_slabs(msp, slab_layers: list[str], scale: float = 1.0, ledger=None, voids_out=None) -> list[SlabEntry]:
    if not slab_layers:
        return []
    cut_layers = [l for l in slab_layers if CUTOUT_LAYER_PATTERN.search(l)]
    slab_layers = [l for l in slab_layers if l not in cut_layers]
    voids = _cutout_voids(msp, cut_layers, scale, ledger)
    if voids_out is not None:
        voids_out.extend(voids)
    if not slab_layers:
        return []
    polys = extract_closed_polygons(msp, slab_layers, ledger=ledger)
    scale_polygon_dicts(polys, scale)
    thk_labels = _find_thk_labels(msp, scale)
    used_labels: set[int] = set()

    entries = []
    counters: dict[int, int] = {}
    for poly in polys:
        vertices = poly["vertices"]
        layer = poly["layer"]
        thickness_mm, source, label_text, warnings = _match_thickness(vertices, layer, thk_labels, used_labels)

        area_sqm = sqmm_to_sqm(polygon_area_sqmm(vertices))
        volume_cum = area_sqm * (thickness_mm / 1000.0)
        cx, cy = polygon_area_centroid(vertices)

        counters[thickness_mm] = counters.get(thickness_mm, 0) + 1
        name = f"S{counters[thickness_mm]}-{thickness_mm}"

        entries.append(SlabEntry(
            name=name, layer=layer, thickness_mm=thickness_mm, thickness_source=source,
            label_text=label_text, area_sqm=round(area_sqm, 3), volume_cum=round(volume_cum, 4),
            centroid=(round(cx, 1), round(cy, 1)), vertices=vertices, warnings=warnings,
        ))
        if ledger:
            ledger.consume(poly["handle"], member=name, layer=layer, kind="slab")
    return entries


VOID_TEXT_PATTERN = re.compile(r"STAIR|LIFT|ELEVATOR|SHAFT|VOID|DUCT", re.IGNORECASE)


def find_void_points(msp, scale: float = 1.0) -> list[tuple[float, float]]:
    """Positions of STAIRCASE / LIFT / SHAFT / VOID text: a slab face containing one is a void."""
    texts = extract_text_entities(msp)
    scale_text_dicts(texts, scale)
    return [t["position"] for t in texts if VOID_TEXT_PATTERN.search(t["text"])]


# ── wall extraction ───────────────────────────────────────────────────────

def _resolve_thickness_and_warnings(geom_thickness, layer, text_match_vertices, thk_labels, used_labels):
    """
    Resolves a final thickness for a wall leg given its measured geometric
    thickness, preferring a matched TEXT label (point-in-polygon against
    text_match_vertices) over the drawn geometry.  The layer-name number is
    only a hint and never produces a warning: walls vary, geometry wins.

    Returns (thickness_mm, thickness_raw_mm, source, label_text, warnings).
    """
    warnings = []
    lo, hi = WALL_THICKNESS_PLAUSIBLE_MM
    if not (lo <= geom_thickness <= hi):
        warnings.append(
            f"Wall on layer '{layer}': measured geometric thickness {geom_thickness:.0f}mm is outside the "
            f"typical wall range ({lo}-{hi}mm); verify this one manually."
        )

    for idx, label in enumerate(thk_labels):
        if idx in used_labels:
            continue
        lx, ly = label["position"]
        if point_in_polygon(lx, ly, text_match_vertices):
            used_labels.add(idx)
            thickness_mm = label["thickness_mm"]
            if abs(thickness_mm - geom_thickness) > 10:
                warnings.append(
                    f"Wall on layer '{layer}': text label says {thickness_mm}mm but the drawn geometry "
                    f"measures {geom_thickness:.0f}mm; using the text label."
                )
            return thickness_mm, float(thickness_mm), "text_label", label["text"], warnings

    # No text label inside this polygon: trust what's actually drawn.
    thickness_mm = round(geom_thickness / 5.0) * 5  # nearest 5mm -- standard construction increment
    label_text = f"(measured from drawing: {geom_thickness:.1f}mm)"
    return thickness_mm, geom_thickness, "geometry", label_text, warnings


def _polygon_segments(polys):
    segs = []
    for p in polys:
        v = p["vertices"]
        for k in range(len(v)):
            segs.append((v[k], v[(k + 1) % len(v)]))
    return segs


def _label_values(thk_labels):
    return [l["thickness_mm"] for l in thk_labels]


def extract_walls(msp, str_wall_layers: list[str], ns_wall_layers: list[str],
                  scale: float = 1.0, ledger=None, regions_out=None) -> list[WallEntry]:
    """
    Walls via regions -> legs -> members.

      1. per layer group, union touching polygons into wall regions;
      2. composite gate: a region is simple only if it fills its bounding
         rectangle, is elongated and has a thickness in this drawing's set;
         everything else is decomposed into straight legs (wall_regions.py);
      3. each leg becomes an axis + interval Member with its angle snapped to
         the drawing's dominant directions; ends are slid to exact axis
         intersections (graph.extend_trim) -- never averaged.

    ledger      -- optional Ledger; every polygon is consumed or rejected.
    regions_out -- optional list; receives (layer, shapely polygon) per region
                   for the coverage check.
    """
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    from .wall_regions import calibrate_thickness_set, decompose_region, snap_thickness, in_thickness_set
    from .members import dominant_angles, member_from_points
    from .graph import extend_trim
    from . import ledger as L

    all_layers = list(str_wall_layers) + list(ns_wall_layers)
    if not all_layers:
        return []
    open_polys = []
    polys = extract_closed_polygons(msp, all_layers, ledger=ledger, open_out=open_polys)
    hatches = extract_hatch_polygons(msp, all_layers)     # filled walls: outer boundary of each HATCH
    for h in hatches:
        if ledger:
            ledger.see(h["handle"], h["layer"], "hatch")
    polys = polys + hatches
    scale_polygon_dicts(polys, scale)
    if ledger:
        for op in open_polys:
            ledger.see(op["handle"], op["layer"], "polygon")
            ledger.reject(op["handle"], L.OPEN_POLYLINE,
                          "open outline (wall geometry comes from closed outlines / HATCH)", op["layer"], "wall")
    thk_labels = _find_thk_labels(msp, scale)
    used_labels: set[int] = set()
    str_set = set(str_wall_layers)

    shapes = []
    for p in polys:
        sh = Polygon(p["vertices"])
        if not sh.is_valid:
            sh = sh.buffer(0)
        if sh.is_empty or sh.area < 1.0:
            if ledger:
                ledger.reject(p["handle"], L.TOO_FEW_VERTICES, "degenerate wall polygon", p["layer"], "wall")
            continue
        shapes.append((p, sh))

    layer_numbers = []
    for name in all_layers:
        mm = re.search(r"(\d+)\s*$", name)
        if mm:
            layer_numbers.append(int(mm.group(1)))
    # THK labels count for calibration only when they sit inside a wall polygon (slab labels must not leak in)
    wall_label_values = [lb["thickness_mm"] for lb in thk_labels
                         if any(sh.contains(Point(lb["position"])) for _, sh in shapes)]
    tset = calibrate_thickness_set([sh for _, sh in shapes], wall_label_values, layer_numbers)
    dominant = dominant_angles(_polygon_segments(polys))

    entries = []
    str_count = 1
    ns_counters: dict[int, int] = {}
    suffixes = "abcdefghijklmnopqrstuvwxyz"

    for is_structural in (True, False):
        group_shapes = [(p, sh) for p, sh in shapes if (p["layer"] in str_set) == is_structural]
        if not group_shapes:
            continue
        gap = WALL_POLYGON_GROUP_GAP_MM / 2.0
        merged = unary_union([sh.buffer(gap, join_style=2) for _, sh in group_shapes]).buffer(-gap, join_style=2)
        comps = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon" and g.area > 1.0]

        for comp in comps:
            srcs = [p for p, sh in group_shapes if comp.buffer(1.0).contains(sh.representative_point())]
            layer = srcs[0]["layer"] if srcs else all_layers[0]
            handles = [p["handle"] for p in srcs]
            if regions_out is not None:
                regions_out.append((layer, comp))

            legs, how = decompose_region(comp, tset)
            if not legs:
                for p in srcs:
                    if ledger:
                        ledger.reject(p["handle"], L.COMPOSITE_UNRESOLVED,
                                      f"could not decompose wall region near ({comp.centroid.x:.0f}, {comp.centroid.y:.0f})",
                                      layer, "wall")
                continue

            if len(legs) > 1:
                # stubs shorter than one thickness and sliver-thin legs are junction noise, not members
                def _ln(l):
                    return math.hypot(l["p2"][0] - l["p1"][0], l["p2"][1] - l["p1"][1])
                kept = [l for l in legs if _ln(l) >= l["width"] and l["width"] >= PLAUSIBLE_MIN_MM]
                if kept:
                    legs = kept
            members = [member_from_points("wall", l["p1"], l["p2"], l["width"], snap_angles=dominant)
                       for l in legs]
            if len(members) > 1:
                extend_trim(members)

            comp_vertices = list(comp.exterior.coords)[:-1]
            inside = [i for i, lb in enumerate(thk_labels)
                      if i not in used_labels and point_in_polygon(lb["position"][0], lb["position"][1], comp_vertices)]

            base_name = None
            if is_structural:
                base_name = f"SW-{str_count}"
                str_count += 1
            multi = len(members) > 1

            for idx, m in enumerate(members):
                width = m.thickness
                cand_idx = [i for i in inside if i not in used_labels and
                            (not multi or abs(thk_labels[i]["thickness_mm"] - width) <= 10)]
                cand = [thk_labels[i] for i in cand_idx]
                thickness_mm, thickness_raw_mm, source, label_text, warnings = _resolve_thickness_and_warnings(
                    width, layer, comp_vertices, cand, set())
                if source == "text_label":
                    for i in cand_idx:
                        if thk_labels[i]["text"] == label_text:
                            used_labels.add(i)
                            break
                else:
                    thickness_mm = int(snap_thickness(width, tset))

                suffix = (suffixes[idx] if idx < len(suffixes) else str(idx)) if multi else ""
                if base_name is None:
                    ns_counters[thickness_mm] = ns_counters.get(thickness_mm, 0) + 1
                    name = f"NSW{thickness_mm}-{ns_counters[thickness_mm]}{suffix}"
                    group = f"NSW-{comp.centroid.x:.0f}_{comp.centroid.y:.0f}" if multi else ""
                else:
                    name = base_name + suffix
                    group = base_name if multi else ""

                length_m = m.length / 1000.0
                verts = comp_vertices if (not multi and how == "simple") else []
                area = (sqmm_to_sqm(polygon_area_sqmm(verts)) if verts
                        else length_m * thickness_mm / 1000.0)
                c = m.centroid
                entries.append(WallEntry(
                    name=name, layer=layer, is_structural=is_structural, thickness_mm=thickness_mm,
                    thickness_raw_mm=round(thickness_raw_mm, 1), thickness_source=source, label_text=label_text,
                    area_sqm=round(area, 3), length_m=round(length_m, 3), angle_deg=round(m.angle_deg, 2),
                    centroid=(round(c[0], 1), round(c[1], 1)), p1=m.p1, p2=m.p2,
                    vertices=verts, warnings=warnings, group_id=group, source_handles=handles,
                ))
            if ledger:
                for p in srcs:
                    ledger.consume(p["handle"], member=base_name or "", layer=layer, kind="wall")
    return entries
