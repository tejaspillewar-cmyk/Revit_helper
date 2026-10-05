"""
Element Recognizer: Identifies structural elements (beams, columns, slabs, walls)
from the geometric analysis results using layer matching, regex label parsing,
and geometric heuristics. Assigns confidence scores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point

from stage1_preflight.models.schemas import (
    BeamElement,
    ColumnElement,
    ElementType,
    Point3D,
    SlabElement,
    StructuralLevel,
    WallElement,
)
from stage1_preflight.parsers.dxf_parser import ParsedCADData, RawText
from stage1_preflight.parsers.geometry_engine import (
    ClosedRegion,
    GeometryResult,
    ParallelLinePair,
    TextAssociation,
)
from stage1_preflight.utils.constants import (
    CONFIDENCE_GEOM_WEIGHT,
    CONFIDENCE_LABEL_WEIGHT,
    CONFIDENCE_LAYER_WEIGHT,
    DEFAULT_BEAM_DEPTH,
    DEFAULT_BEAM_WIDTH,
    DEFAULT_COLUMN_DEPTH,
    DEFAULT_COLUMN_WIDTH,
    DEFAULT_FLOOR_HEIGHT,
    DEFAULT_SLAB_THICKNESS,
    DEFAULT_WALL_THICKNESS,
    DIAMETER_REGEX,
    MIN_BEAM_LENGTH,
    MIN_COLUMN_DIM,
    MIN_SLAB_AREA,
    SECTION_REGEX,
    THICKNESS_REGEX,
)

logger = logging.getLogger(__name__)


def _parse_section_label(text: str) -> tuple[float | None, float | None]:
    """
    Extract width and depth from a section label.
    Returns (width_mm, depth_mm) or (None, None).
    E.g. "B 300x450" → (300.0, 450.0)
    """
    m = re.search(SECTION_REGEX, text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _parse_thickness(text: str) -> float | None:
    """
    Extract thickness from text like "150 THK".
    Returns thickness in mm or None.
    """
    m = re.search(THICKNESS_REGEX, text)
    if m:
        return float(m.group(1))
    return None


def _parse_diameter(text: str) -> float | None:
    """
    Extract diameter from text like "DIA 600" or "Ø600".
    Returns diameter in mm or None.
    """
    m = re.search(DIAMETER_REGEX, text)
    if m:
        return float(m.group(1))
    return None


class ElementRecognizer:
    """
    Converts geometry analysis results into typed structural elements
    with confidence scores.

    Processing order:
    1. Columns — from closed rectangles and circles on column layers
    2. Beams — from parallel line pairs on beam layers
    3. Slabs — from large closed polygons on slab layers
    4. Walls — from parallel line pairs on wall layers (wider separation)
    """

    def __init__(
        self,
        layer_mapping: dict[str, ElementType | None],
        floor_height: float = DEFAULT_FLOOR_HEIGHT,
        base_elevation: float = 0.0,
        level_name: str = "L01",
    ):
        self.layer_mapping = layer_mapping
        self.floor_height = floor_height
        self.base_elevation = base_elevation
        self.level_name = level_name
        self._counters: dict[str, int] = {"B": 0, "C": 0, "S": 0, "W": 0}

    def _next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}_{self._counters[prefix]:02d}"

    def recognize(
        self,
        cad_data: ParsedCADData,
        geom_result: GeometryResult,
    ) -> StructuralLevel:
        """
        Run the full recognition pipeline and return a StructuralLevel.
        """
        level = StructuralLevel(
            level=self.level_name,
            base_elevation=self.base_elevation,
            floor_height=self.floor_height,
        )

        # Build text lookup by proximity to each geometry
        text_map = self._build_text_map(geom_result)

        # 1) Columns from rectangles and circles
        level.columns = self._recognize_columns(
            geom_result.closed_regions,
            geom_result.circles,
            text_map,
            cad_data,
        )

        # Track regions used as columns
        column_regions = set()
        for col in level.columns:
            column_regions.add(id(col))

        # 2) Beams from parallel line pairs
        level.beams = self._recognize_beams(
            geom_result.parallel_pairs, text_map, cad_data
        )

        # 3) Slabs from large closed polygons
        level.slabs = self._recognize_slabs(
            geom_result.closed_regions, text_map, cad_data
        )

        # 4) Walls from remaining parallel pairs
        level.walls = self._recognize_walls(
            geom_result.parallel_pairs, text_map, cad_data, level.beams
        )

        logger.info(
            f"Recognized: {len(level.beams)} beams, {len(level.columns)} columns, "
            f"{len(level.slabs)} slabs, {len(level.walls)} walls"
        )
        return level

    # -------------------------------------------------------------------
    # Text mapping
    # -------------------------------------------------------------------

    def _build_text_map(
        self, geom_result: GeometryResult
    ) -> dict[int, list[RawText]]:
        """Build a map from geometry index → associated texts."""
        text_map: dict[int, list[RawText]] = {}
        for assoc in geom_result.text_associations:
            text_map.setdefault(assoc.geom_index, []).append(assoc.text)
        return text_map

    # -------------------------------------------------------------------
    # Column recognition
    # -------------------------------------------------------------------

    def _recognize_columns(
        self,
        regions: list[ClosedRegion],
        circles: list,
        text_map: dict[int, list[RawText]],
        cad_data: ParsedCADData,
    ) -> list[ColumnElement]:
        columns: list[ColumnElement] = []

        # From rectangular regions
        for i, region in enumerate(regions):
            if not region.is_rectangular:
                continue
            if region.area > 2_000_000:  # Too large to be a column (>~1.4m side)
                continue
            if region.width < MIN_COLUMN_DIM or region.height < MIN_COLUMN_DIM:
                continue

            # Check layer
            layer_match = self.layer_mapping.get(region.layer) == ElementType.COLUMN

            # Parse labels
            width, depth = None, None
            label_text = ""
            texts = text_map.get(i, [])
            for t in texts:
                w, d = _parse_section_label(t.content)
                if w and d:
                    width, depth = w, d
                    label_text = t.content
                    break

            # Confidence scoring
            conf = 0.0
            issues: list[str] = []

            if layer_match:
                conf += CONFIDENCE_LAYER_WEIGHT
            elif self.layer_mapping.get(region.layer) == ElementType.BEAM:
                continue  # Skip — it's on a beam layer

            if width and depth:
                conf += CONFIDENCE_LABEL_WEIGHT
            else:
                width = round(region.width)
                depth = round(region.height)
                issues.append("Dimensions inferred from geometry, no label found")
                conf += CONFIDENCE_LABEL_WEIGHT * 0.3

            # Geometry confidence (is it roughly square-ish for columns?)
            aspect = max(region.width, region.height) / max(
                min(region.width, region.height), 1.0
            )
            if aspect < 3.0:
                conf += CONFIDENCE_GEOM_WEIGHT
            else:
                conf += CONFIDENCE_GEOM_WEIGHT * 0.5
                issues.append(f"Aspect ratio {aspect:.1f} unusual for column")

            if not layer_match:
                issues.append(f"Layer '{region.layer}' not in column patterns")

            columns.append(ColumnElement(
                id=self._next_id("C"),
                width=width or DEFAULT_COLUMN_WIDTH,
                depth=depth or DEFAULT_COLUMN_DEPTH,
                centroid=Point3D(
                    x=region.centroid[0],
                    y=region.centroid[1],
                    z=self.base_elevation,
                ),
                height=self.floor_height,
                layer=region.layer,
                label_text=label_text,
                confidence=min(conf, 1.0),
                issues=issues,
            ))

        # From circles
        for i, circle in enumerate(circles):
            if circle.radius < MIN_COLUMN_DIM / 2:
                continue
            if circle.radius > 1000:  # Too large
                continue

            layer_match = self.layer_mapping.get(circle.layer) == ElementType.COLUMN
            conf = 0.0
            issues = []
            diameter = circle.radius * 2

            # Check for diameter label
            label_text = ""
            texts = text_map.get(len(regions) + i, [])  # Offset index
            for t in texts:
                d = _parse_diameter(t.content)
                if d:
                    diameter = d
                    label_text = t.content
                    break

            if layer_match:
                conf += CONFIDENCE_LAYER_WEIGHT
            else:
                issues.append(f"Layer '{circle.layer}' not in column patterns")

            if label_text:
                conf += CONFIDENCE_LABEL_WEIGHT
            else:
                issues.append("No diameter label found")
                conf += CONFIDENCE_LABEL_WEIGHT * 0.3

            conf += CONFIDENCE_GEOM_WEIGHT  # Circles are strong column indicators

            columns.append(ColumnElement(
                id=self._next_id("C"),
                width=diameter,
                depth=diameter,
                centroid=Point3D(
                    x=circle.center[0],
                    y=circle.center[1],
                    z=self.base_elevation,
                ),
                height=self.floor_height,
                is_circular=True,
                diameter=diameter,
                layer=circle.layer,
                label_text=label_text,
                confidence=min(conf, 1.0),
                issues=issues,
            ))

        return columns

    # -------------------------------------------------------------------
    # Beam recognition
    # -------------------------------------------------------------------

    def _recognize_beams(
        self,
        parallel_pairs: list[ParallelLinePair],
        text_map: dict[int, list[RawText]],
        cad_data: ParsedCADData,
    ) -> list[BeamElement]:
        beams: list[BeamElement] = []

        for i, pair in enumerate(parallel_pairs):
            layer = pair.layer

            # Only process beam layers (or unlabeled layers)
            is_beam_layer = self.layer_mapping.get(layer) == ElementType.BEAM
            is_wall_layer = self.layer_mapping.get(layer) == ElementType.WALL

            if is_wall_layer and not is_beam_layer:
                continue  # Will be processed as wall

            if pair.centerline.length < MIN_BEAM_LENGTH:
                continue

            # Parse labels
            width, depth = None, None
            label_text = ""
            texts = text_map.get(i, [])
            for t in texts:
                w, d = _parse_section_label(t.content)
                if w and d:
                    width, depth = w, d
                    label_text = t.content
                    break

            # Confidence scoring
            conf = 0.0
            issues: list[str] = []

            if is_beam_layer:
                conf += CONFIDENCE_LAYER_WEIGHT
            else:
                issues.append(f"Layer '{layer}' not in beam patterns")

            if width and depth:
                conf += CONFIDENCE_LABEL_WEIGHT
            else:
                # Infer width from separation
                width = round(pair.separation)
                issues.append("Width inferred from line separation")
                if not depth:
                    depth = DEFAULT_BEAM_DEPTH
                    issues.append("Depth set to default — no label found")
                    conf += CONFIDENCE_LABEL_WEIGHT * 0.2
                else:
                    conf += CONFIDENCE_LABEL_WEIGHT * 0.5

            # Geometry quality
            if pair.centerline.length > MIN_BEAM_LENGTH:
                conf += CONFIDENCE_GEOM_WEIGHT
            else:
                conf += CONFIDENCE_GEOM_WEIGHT * 0.5
                issues.append(f"Short beam: {pair.centerline.length:.0f} mm")

            coords = list(pair.centerline.coords)
            start = coords[0]
            end = coords[-1]

            beams.append(BeamElement(
                id=self._next_id("B"),
                width=width or DEFAULT_BEAM_WIDTH,
                depth=depth or DEFAULT_BEAM_DEPTH,
                start=Point3D(
                    x=start[0], y=start[1],
                    z=self.base_elevation + self.floor_height,
                ),
                end=Point3D(
                    x=end[0], y=end[1],
                    z=self.base_elevation + self.floor_height,
                ),
                layer=layer,
                label_text=label_text,
                confidence=min(conf, 1.0),
                issues=issues,
            ))

        return beams

    # -------------------------------------------------------------------
    # Slab recognition
    # -------------------------------------------------------------------

    def _recognize_slabs(
        self,
        regions: list[ClosedRegion],
        text_map: dict[int, list[RawText]],
        cad_data: ParsedCADData,
    ) -> list[SlabElement]:
        slabs: list[SlabElement] = []

        for i, region in enumerate(regions):
            if region.area < MIN_SLAB_AREA:
                continue
            # Slabs are typically large polygons
            if region.is_rectangular and region.area < 2_000_000:
                continue  # Probably a column, already handled

            layer_match = self.layer_mapping.get(region.layer) == ElementType.SLAB

            # Parse thickness
            thickness = None
            label_text = ""
            texts = text_map.get(i, [])
            for t in texts:
                thk = _parse_thickness(t.content)
                if thk:
                    thickness = thk
                    label_text = t.content
                    break

            # Confidence scoring
            conf = 0.0
            issues: list[str] = []

            if layer_match:
                conf += CONFIDENCE_LAYER_WEIGHT
            else:
                issues.append(f"Layer '{region.layer}' not in slab patterns")

            if thickness:
                conf += CONFIDENCE_LABEL_WEIGHT
            else:
                thickness = DEFAULT_SLAB_THICKNESS
                issues.append("Thickness set to default — no THK label found")
                conf += CONFIDENCE_LABEL_WEIGHT * 0.2

            # Large area = high geometry confidence for slabs
            if region.area > 1_000_000:  # > 1m²
                conf += CONFIDENCE_GEOM_WEIGHT
            else:
                conf += CONFIDENCE_GEOM_WEIGHT * 0.6
                issues.append(f"Small slab area: {region.area / 1e6:.2f} m²")

            # Check if polygon is closed
            boundary_coords = list(region.polygon.exterior.coords)
            boundary = [
                Point3D(x=pt[0], y=pt[1], z=self.base_elevation + self.floor_height)
                for pt in boundary_coords[:-1]  # Exclude duplicate closing vertex
            ]

            if len(boundary) < 3:
                continue

            slabs.append(SlabElement(
                id=self._next_id("S"),
                thickness=thickness,
                boundary=boundary,
                layer=region.layer,
                label_text=label_text,
                confidence=min(conf, 1.0),
                issues=issues,
            ))

        return slabs

    # -------------------------------------------------------------------
    # Wall recognition
    # -------------------------------------------------------------------

    def _recognize_walls(
        self,
        parallel_pairs: list[ParallelLinePair],
        text_map: dict[int, list[RawText]],
        cad_data: ParsedCADData,
        existing_beams: list[BeamElement],
    ) -> list[WallElement]:
        """Recognize walls from parallel line pairs on wall layers."""
        walls: list[WallElement] = []

        # Track beam centerlines to avoid duplication
        beam_lines = set()
        for beam in existing_beams:
            key = (
                round(beam.start.x), round(beam.start.y),
                round(beam.end.x), round(beam.end.y),
            )
            beam_lines.add(key)

        for i, pair in enumerate(parallel_pairs):
            layer = pair.layer
            is_wall_layer = self.layer_mapping.get(layer) == ElementType.WALL

            if not is_wall_layer:
                continue

            # Check not already classified as beam
            coords = list(pair.centerline.coords)
            start = coords[0]
            end = coords[-1]
            key = (round(start[0]), round(start[1]), round(end[0]), round(end[1]))
            if key in beam_lines:
                continue

            # Walls typically have larger separation (thickness)
            if pair.separation < 100.0:
                continue

            # Parse labels
            thickness = None
            label_text = ""
            texts = text_map.get(i, [])
            for t in texts:
                thk = _parse_thickness(t.content)
                if thk:
                    thickness = thk
                    label_text = t.content
                    break
                # Also try section format
                w, d = _parse_section_label(t.content)
                if w:
                    thickness = w
                    label_text = t.content
                    break

            conf = 0.0
            issues: list[str] = []

            if is_wall_layer:
                conf += CONFIDENCE_LAYER_WEIGHT

            if thickness:
                conf += CONFIDENCE_LABEL_WEIGHT
            else:
                thickness = round(pair.separation)
                issues.append("Thickness inferred from line separation")
                conf += CONFIDENCE_LABEL_WEIGHT * 0.4

            conf += CONFIDENCE_GEOM_WEIGHT

            walls.append(WallElement(
                id=self._next_id("W"),
                thickness=thickness or DEFAULT_WALL_THICKNESS,
                start=Point3D(x=start[0], y=start[1], z=self.base_elevation),
                end=Point3D(x=end[0], y=end[1], z=self.base_elevation),
                height=self.floor_height,
                layer=layer,
                label_text=label_text,
                confidence=min(conf, 1.0),
                issues=issues,
            ))

        return walls
