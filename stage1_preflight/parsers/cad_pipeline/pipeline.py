"""
Headless DXF -> parsed-model pipeline (shared by the Qt ParseWorker and tests).

    load -> scale -> extract (slabs, walls, beams, columns; ledger tracks every entity)
         -> connect (axis intersections) -> dedupe -> validate
"""
from .dxf_helpers import load_dxf, detect_dxf_scale_to_mm
from .wall_slab_extractor import extract_slabs, extract_walls, find_void_points
from .beam_extractor import extract_beams
from .column_extractor import extract_columns
from .connectivity import snap_connections
from .deduplication import deduplicate_parsed
from .ledger import Ledger
from .validation import validate
from .geometry import polygon_area_sqmm


def _compute_origin_offset(parsed: dict) -> tuple[float, float]:
    """If geometry centroid is far from origin (> 100 m), return offset to translate near (0,0)."""
    coords = []
    for slab in parsed.get("slabs", []):
        coords.extend(slab.vertices)
    for wall in parsed.get("walls", []):
        coords.extend([wall.p1, wall.p2])
    for beam in parsed.get("beams", []):
        coords.extend([beam.p1, beam.p2])
    for col in parsed.get("columns", []):
        coords.append(col.centroid)
    if not coords:
        return (0.0, 0.0)
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    if abs(cx) > 100_000 or abs(cy) > 100_000:
        return (round(cx, 1), round(cy, 1))
    return (0.0, 0.0)


def run_pipeline(dxf_path, buckets, auto_connect=True, log=lambda m: None) -> dict:
    log("Loading DXF geometry...")
    doc, msp = load_dxf(dxf_path)

    # Detect DXF drawing units and compute scale factor to mm.
    scale, scale_source = detect_dxf_scale_to_mm(doc, msp)
    log(f"DXF units: {scale_source} (scale factor: {scale}×)")

    ledger = Ledger()
    regions = []

    log("Extracting slabs...")
    void_polys = []
    slabs = extract_slabs(msp, buckets.get("slabs", []), scale=scale, ledger=ledger, voids_out=void_polys)

    log("Extracting walls...")
    walls = extract_walls(msp, buckets.get("str_walls", []), buckets.get("ns_walls", []),
                          scale=scale, ledger=ledger, regions_out=regions)

    log("Extracting beams...")
    beams = extract_beams(msp, buckets.get("beams", []), scale=scale, ledger=ledger)

    log("Extracting columns...")
    columns = extract_columns(msp, buckets.get("columns", []), scale=scale, ledger=ledger)

    log(
        f"Extracted: {len(slabs)} slabs, {len(walls)} walls, {len(beams)} beams, {len(columns)} columns."
    )
    original_slab_area = sum(polygon_area_sqmm(sl.vertices) for sl in slabs if sl.vertices)
    result = {"slabs": slabs, "walls": walls, "beams": beams, "columns": columns,
              "void_points": find_void_points(msp, scale), "void_polys": void_polys}

    if auto_connect:
        log("Connecting geometry (axis intersections, no endpoint averaging)...")
        result = snap_connections(result, log=log)

    log("Checking for duplicates...")
    result = deduplicate_parsed(result, log=log, ledger=ledger)

    log("Validating (coverage, orientation, topology, ledger)...")
    report = validate(result, regions=regions, ledger=ledger, original_slab_area_sqmm=original_slab_area)
    for line in report.lines():
        log("  " + line)
    result["report"] = report
    result["regions"] = regions

    origin_offset = _compute_origin_offset(result)
    if origin_offset != (0.0, 0.0):
        log(
            f"Large coordinate offset detected: ({origin_offset[0]:.0f}, {origin_offset[1]:.0f})mm. "
            f"Geometry will be translated near ETABS origin when drawing."
        )
    result["origin_offset"] = origin_offset
    result["scale"] = scale
    result["scale_source"] = scale_source
    return result
