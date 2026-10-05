"""
Geometry engine: Shapely-based spatial operations for structural element
recognition — collinear merging, parallel-line detection, spatial indexing,
and text-to-geometry association.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import (
    LineString,
    MultiLineString,
    Point,
    Polygon,
    box,
)
from shapely.ops import linemerge, nearest_points, unary_union
from shapely.strtree import STRtree

from stage1_preflight.parsers.dxf_parser import (
    ParsedCADData,
    RawCircle,
    RawLine,
    RawPolyline,
    RawRectangle,
    RawText,
)
from stage1_preflight.utils.constants import (
    COLLINEAR_MERGE_TOL,
    CONTAINMENT_TOL,
    PARALLEL_LINE_TOL,
    POLYGON_CLOSE_TOL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output data classes
# ---------------------------------------------------------------------------

@dataclass
class ParallelLinePair:
    """Two parallel lines forming a structural member cross-section."""
    line_a: LineString
    line_b: LineString
    centerline: LineString
    separation: float  # Distance between lines in mm
    layer: str = ""


@dataclass
class ClosedRegion:
    """A closed polygon region (potential slab or column footprint)."""
    polygon: Polygon
    area: float
    centroid: tuple[float, float]
    layer: str = ""
    is_rectangular: bool = False
    width: float = 0.0
    height: float = 0.0


@dataclass
class TextAssociation:
    """A text entity spatially linked to a geometry."""
    text: RawText
    geom_index: int      # Index into the associated geometry list
    distance: float       # Distance from text to nearest geometry in mm


@dataclass
class GeometryResult:
    """Complete geometry analysis result."""
    parallel_pairs: list[ParallelLinePair] = field(default_factory=list)
    closed_regions: list[ClosedRegion] = field(default_factory=list)
    circles: list[RawCircle] = field(default_factory=list)
    merged_lines: list[LineString] = field(default_factory=list)
    text_associations: list[TextAssociation] = field(default_factory=list)
    raw_texts: list[RawText] = field(default_factory=list)


class GeometryEngine:
    """
    Processes raw CAD entities into structured geometric relationships.

    Steps:
    1. Convert raw lines to Shapely LineStrings.
    2. Merge collinear segments within tolerance.
    3. Detect parallel line pairs (beam/wall candidates).
    4. Extract closed polygons (slab/column candidates).
    5. Build spatial index for text-to-geometry association.
    """

    def __init__(
        self,
        merge_tol: float = COLLINEAR_MERGE_TOL,
        parallel_tol: float = PARALLEL_LINE_TOL,
        text_tol: float = CONTAINMENT_TOL,
        close_tol: float = POLYGON_CLOSE_TOL,
    ):
        self.merge_tol = merge_tol
        self.parallel_tol = parallel_tol
        self.text_tol = text_tol
        self.close_tol = close_tol

    def process(self, cad_data: ParsedCADData) -> GeometryResult:
        """Run the full geometry analysis pipeline."""
        result = GeometryResult()
        result.raw_texts = list(cad_data.texts)
        result.circles = list(cad_data.circles)

        # 1) Convert raw lines to Shapely LineStrings grouped by layer
        layer_lines: dict[str, list[LineString]] = {}
        for raw_line in cad_data.lines:
            ls = LineString([raw_line.start, raw_line.end])
            if ls.length < 1.0:
                continue
            layer_lines.setdefault(raw_line.layer, []).append(ls)

        # 2) Merge collinear lines per layer
        all_merged: list[LineString] = []
        for layer, lines in layer_lines.items():
            merged = self._merge_collinear(lines)
            all_merged.extend(merged)
        result.merged_lines = all_merged

        # 3) Detect parallel line pairs
        result.parallel_pairs = self._find_parallel_pairs(cad_data.lines)

        # 4) Extract closed regions from polylines and rectangles
        result.closed_regions = self._extract_closed_regions(
            cad_data.polylines, cad_data.rectangles
        )

        # 5) Associate texts with geometry using spatial index
        result.text_associations = self._associate_texts(
            cad_data.texts, result
        )

        logger.info(
            f"Geometry analysis: {len(result.parallel_pairs)} parallel pairs, "
            f"{len(result.closed_regions)} closed regions, "
            f"{len(result.text_associations)} text associations"
        )
        return result

    # -------------------------------------------------------------------
    # Step 2: Merge collinear segments
    # -------------------------------------------------------------------

    def _merge_collinear(self, lines: list[LineString]) -> list[LineString]:
        """
        Merge nearly-collinear line segments within tolerance.

        Groups lines by angle, then merges overlapping segments
        along the same axis.
        """
        if not lines:
            return []

        # Group by angle (quantised to 1°)
        angle_groups: dict[int, list[LineString]] = {}
        for ls in lines:
            coords = list(ls.coords)
            dx = coords[-1][0] - coords[0][0]
            dy = coords[-1][1] - coords[0][1]
            angle = int(round(np.degrees(np.arctan2(dy, dx)))) % 180
            angle_groups.setdefault(angle, []).append(ls)

        merged: list[LineString] = []
        for angle, group in angle_groups.items():
            # Within each angle group, cluster by perpendicular distance
            clusters = self._cluster_by_perpendicular(group)
            for cluster in clusters:
                try:
                    result = linemerge(MultiLineString(cluster))
                    if isinstance(result, LineString):
                        merged.append(result)
                    else:
                        merged.extend(result.geoms)
                except Exception:
                    merged.extend(cluster)

        return merged

    def _cluster_by_perpendicular(
        self, lines: list[LineString]
    ) -> list[list[LineString]]:
        """Cluster lines that are within merge_tol perpendicular distance."""
        if not lines:
            return []

        used = [False] * len(lines)
        clusters: list[list[LineString]] = []

        for i, line_a in enumerate(lines):
            if used[i]:
                continue
            cluster = [line_a]
            used[i] = True
            for j in range(i + 1, len(lines)):
                if used[j]:
                    continue
                if line_a.distance(lines[j]) <= self.merge_tol:
                    cluster.append(lines[j])
                    used[j] = True
            clusters.append(cluster)

        return clusters

    # -------------------------------------------------------------------
    # Step 3: Find parallel line pairs
    # -------------------------------------------------------------------

    def _find_parallel_pairs(
        self, raw_lines: list[RawLine]
    ) -> list[ParallelLinePair]:
        """
        Detect pairs of parallel lines that could form beam/wall edges.

        Two lines are parallel if their direction vectors are within
        tolerance, and they are separated by a reasonable structural width.
        """
        pairs: list[ParallelLinePair] = []
        lines_by_layer: dict[str, list[tuple[LineString, np.ndarray]]] = {}

        for raw in raw_lines:
            ls = LineString([raw.start, raw.end])
            if ls.length < 100.0:
                continue
            vec = np.array(raw.end) - np.array(raw.start)
            vec_norm = vec / np.linalg.norm(vec)
            lines_by_layer.setdefault(raw.layer, []).append((ls, vec_norm))

        for layer, line_data in lines_by_layer.items():
            used = set()
            for i, (ls_a, vec_a) in enumerate(line_data):
                if i in used:
                    continue
                for j in range(i + 1, len(line_data)):
                    if j in used:
                        continue
                    ls_b, vec_b = line_data[j]

                    # Check parallel: |cross product| ≈ 0
                    cross = abs(vec_a[0] * vec_b[1] - vec_a[1] * vec_b[0])
                    if cross > 0.05:  # ~3° tolerance
                        continue

                    # Check separation
                    dist = ls_a.distance(ls_b)
                    if dist < 50.0 or dist > 2000.0:
                        continue

                    # Check overlap (lines should overlap along their length)
                    proj_overlap = self._check_overlap(ls_a, ls_b)
                    if proj_overlap < 0.5:  # At least 50% overlap
                        continue

                    # Compute centerline
                    centerline = self._compute_centerline(ls_a, ls_b)
                    if centerline and centerline.length > 100.0:
                        pairs.append(ParallelLinePair(
                            line_a=ls_a,
                            line_b=ls_b,
                            centerline=centerline,
                            separation=dist,
                            layer=layer,
                        ))
                        used.add(i)
                        used.add(j)
                        break

        return pairs

    @staticmethod
    def _check_overlap(ls_a: LineString, ls_b: LineString) -> float:
        """Return fractional overlap of two nearly-parallel lines [0–1]."""
        try:
            buf_a = ls_a.buffer(50)
            intersection = buf_a.intersection(ls_b)
            if intersection.is_empty:
                return 0.0
            return intersection.length / min(ls_a.length, ls_b.length)
        except Exception:
            return 0.0

    @staticmethod
    def _compute_centerline(ls_a: LineString, ls_b: LineString) -> LineString | None:
        """Compute the centerline between two parallel line segments."""
        try:
            coords_a = np.array(ls_a.coords)
            coords_b = np.array(ls_b.coords)

            # Project endpoints of A onto B and vice versa
            start = (coords_a[0] + coords_b[0]) / 2.0
            end = (coords_a[-1] + coords_b[-1]) / 2.0

            return LineString([tuple(start), tuple(end)])
        except Exception:
            return None

    # -------------------------------------------------------------------
    # Step 4: Extract closed regions
    # -------------------------------------------------------------------

    def _extract_closed_regions(
        self,
        polylines: list[RawPolyline],
        rectangles: list[RawRectangle],
    ) -> list[ClosedRegion]:
        """Extract closed polygon regions from polylines and rectangles."""
        regions: list[ClosedRegion] = []

        # From closed polylines
        for pline in polylines:
            if not pline.is_closed or len(pline.vertices) < 3:
                continue
            try:
                poly = Polygon(pline.vertices)
                if not poly.is_valid:
                    poly = poly.buffer(0)  # Fix self-intersections
                if poly.is_empty or poly.area < 100.0:
                    continue

                centroid = poly.centroid
                bounds = poly.bounds
                w = bounds[2] - bounds[0]
                h = bounds[3] - bounds[1]

                is_rect = len(pline.vertices) == 4
                regions.append(ClosedRegion(
                    polygon=poly,
                    area=poly.area,
                    centroid=(centroid.x, centroid.y),
                    layer=pline.layer,
                    is_rectangular=is_rect,
                    width=w,
                    height=h,
                ))
            except Exception as e:
                logger.debug(f"Invalid polyline polygon: {e}")

        # From detected rectangles
        for rect in rectangles:
            try:
                poly = Polygon(rect.vertices)
                if not poly.is_valid or poly.area < 100.0:
                    continue
                regions.append(ClosedRegion(
                    polygon=poly,
                    area=poly.area,
                    centroid=rect.centroid,
                    layer=rect.layer,
                    is_rectangular=True,
                    width=rect.width,
                    height=rect.height,
                ))
            except Exception as e:
                logger.debug(f"Invalid rectangle polygon: {e}")

        return regions

    # -------------------------------------------------------------------
    # Step 5: Text association via spatial index
    # -------------------------------------------------------------------

    def _associate_texts(
        self,
        texts: list[RawText],
        geom_result: GeometryResult,
    ) -> list[TextAssociation]:
        """
        Associate text entities with nearby geometry using STRtree spatial index.

        For each text, find the nearest geometry (parallel pair centerline,
        closed region, or circle) and link them.
        """
        if not texts:
            return []

        # Build a list of geometries with their indices and types
        geom_list: list[tuple[int, str, object]] = []  # (index, type, shapely_geom)

        for i, pair in enumerate(geom_result.parallel_pairs):
            geom_list.append((i, "parallel", pair.centerline))

        for i, region in enumerate(geom_result.closed_regions):
            geom_list.append((i, "region", region.polygon))

        for i, circle in enumerate(geom_result.circles):
            pt = Point(circle.center)
            geom_list.append((i, "circle", pt.buffer(circle.radius)))

        if not geom_list:
            return []

        # Build spatial index
        shapely_geoms = [g[2] for g in geom_list]
        tree = STRtree(shapely_geoms)

        associations: list[TextAssociation] = []
        for text in texts:
            text_point = Point(text.position)
            # Find nearest geometry
            nearest_idx = tree.nearest(text_point)
            nearest_geom = shapely_geoms[nearest_idx]
            dist = text_point.distance(nearest_geom)

            if dist <= self.text_tol:
                orig_idx, geom_type, _ = geom_list[nearest_idx]
                associations.append(TextAssociation(
                    text=text,
                    geom_index=nearest_idx,
                    distance=dist,
                ))

        return associations
