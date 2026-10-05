"""
DXF/DWG file parser using ezdxf.

Reads CAD geometry, normalizes coordinates to millimetres, and
extracts raw entities (lines, polylines, circles, text) grouped by layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import ezdxf
import numpy as np
from ezdxf.entities import (
    Circle,
    DXFGraphic,
    Insert,
    Line,
    LWPolyline,
    MText,
    Polyline,
    Text,
)
from ezdxf.units import decode as decode_units

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes for raw extracted geometry
# ---------------------------------------------------------------------------

@dataclass
class RawLine:
    """A line segment in mm."""
    start: tuple[float, float]
    end: tuple[float, float]
    layer: str = ""


@dataclass
class RawPolyline:
    """A polyline (open or closed) in mm."""
    vertices: list[tuple[float, float]]
    is_closed: bool = False
    layer: str = ""


@dataclass
class RawCircle:
    """A circle in mm."""
    center: tuple[float, float]
    radius: float = 0.0
    layer: str = ""


@dataclass
class RawText:
    """A text entity with position in mm."""
    content: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    height: float = 0.0
    layer: str = ""


@dataclass
class RawRectangle:
    """An axis-aligned or rotated rectangle extracted from polyline/lines."""
    vertices: list[tuple[float, float]]
    centroid: tuple[float, float] = (0.0, 0.0)
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    layer: str = ""


@dataclass
class ParsedCADData:
    """Container for all raw entities extracted from a CAD file."""
    lines: list[RawLine] = field(default_factory=list)
    polylines: list[RawPolyline] = field(default_factory=list)
    circles: list[RawCircle] = field(default_factory=list)
    texts: list[RawText] = field(default_factory=list)
    rectangles: list[RawRectangle] = field(default_factory=list)
    source_file: str = ""
    unit_scale: float = 1.0  # Multiplier to convert native units → mm
    layers: list[str] = field(default_factory=list)

    @property
    def total_entities(self) -> int:
        return (
            len(self.lines)
            + len(self.polylines)
            + len(self.circles)
            + len(self.texts)
            + len(self.rectangles)
        )


# ---------------------------------------------------------------------------
# Unit conversion factors → millimetres
# ---------------------------------------------------------------------------
UNIT_TO_MM = {
    0: 1.0,       # Unitless → assume mm
    1: 25.4,      # Inches
    2: 304.8,     # Feet
    3: 1609344.0, # Miles
    4: 1.0,       # Millimetres
    5: 10.0,      # Centimetres
    6: 1000.0,    # Metres
    7: 1e6,       # Kilometres
    8: 0.0254,    # Microinches
    9: 0.001,     # Mils
    10: 914.4,    # Yards
    11: 1e-7,     # Angstroms
    12: 1e-6,     # Nanometres
    13: 0.001,    # Microns
    14: 100.0,    # Decimetres
    15: 10000.0,  # Decametres
    16: 100000.0, # Hectometres
    17: 1e9,      # Gigametres
    18: 1.496e14, # Astronomical units
    19: 9.461e18, # Light years
    20: 3.086e19, # Parsecs
}


class DXFParser:
    """
    Parses a DXF file and extracts raw geometry data, normalised to mm.

    Usage:
        parser = DXFParser("path/to/framing.dxf")
        data = parser.parse()
    """

    def __init__(self, filepath: str | Path, force_unit: Optional[int] = None):
        self.filepath = Path(filepath)
        self.force_unit = force_unit
        self._doc: Optional[ezdxf.document.Drawing] = None
        self._scale: float = 1.0

    def parse(self) -> ParsedCADData:
        """
        Parse the DXF file and return all extracted entities.

        Returns:
            ParsedCADData with all lines, polylines, circles, texts,
            and rectangles normalised to millimetres.
        """
        logger.info(f"Parsing DXF: {self.filepath}")

        if not self.filepath.exists():
            raise FileNotFoundError(f"DXF file not found: {self.filepath}")

        try:
            self._doc = ezdxf.readfile(str(self.filepath))
        except Exception as e:
            # Try recovering
            logger.warning(f"Standard read failed, trying recover: {e}")
            self._doc = ezdxf.recover.readfile(str(self.filepath))[0]

        self._scale = self._detect_units()
        data = ParsedCADData(
            source_file=str(self.filepath),
            unit_scale=self._scale,
        )

        # Collect all layer names
        data.layers = [layer.dxf.name for layer in self._doc.layers]

        # Process modelspace entities
        msp = self._doc.modelspace()
        for entity in msp:
            self._process_entity(entity, data)

        # Also process block references (INSERT entities expand their content)
        logger.info(
            f"Parsed {data.total_entities} entities from {len(data.layers)} layers"
        )
        return data

    def _detect_units(self) -> float:
        """Detect CAD units and return scale factor to mm."""
        if self.force_unit is not None:
            scale = UNIT_TO_MM.get(self.force_unit, 1.0)
            logger.info(f"Forced unit code {self.force_unit}, scale = {scale}")
            return scale

        try:
            unit_code = self._doc.header.get("$INSUNITS", 0)
        except Exception:
            unit_code = 0

        scale = UNIT_TO_MM.get(unit_code, 1.0)
        logger.info(f"Detected unit code {unit_code}, scale = {scale}")
        return scale

    def _scale_point(self, x: float, y: float) -> tuple[float, float]:
        """Apply scale factor to a 2D point."""
        return (x * self._scale, y * self._scale)

    def _process_entity(self, entity: DXFGraphic, data: ParsedCADData) -> None:
        """Dispatch entity processing based on DXF type."""
        dxftype = entity.dxftype()
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""

        if dxftype == "LINE":
            self._process_line(entity, layer, data)
        elif dxftype == "LWPOLYLINE":
            self._process_lwpolyline(entity, layer, data)
        elif dxftype == "POLYLINE":
            self._process_polyline(entity, layer, data)
        elif dxftype == "CIRCLE":
            self._process_circle(entity, layer, data)
        elif dxftype == "TEXT":
            self._process_text(entity, layer, data)
        elif dxftype == "MTEXT":
            self._process_mtext(entity, layer, data)
        elif dxftype == "INSERT":
            self._process_insert(entity, layer, data)

    def _process_line(self, entity: Line, layer: str, data: ParsedCADData) -> None:
        start = self._scale_point(entity.dxf.start.x, entity.dxf.start.y)
        end = self._scale_point(entity.dxf.end.x, entity.dxf.end.y)
        data.lines.append(RawLine(start=start, end=end, layer=layer))

    def _process_lwpolyline(
        self, entity: LWPolyline, layer: str, data: ParsedCADData
    ) -> None:
        points = [(p[0], p[1]) for p in entity.get_points(format="xy")]
        vertices = [self._scale_point(x, y) for x, y in points]
        is_closed = entity.closed

        if len(vertices) < 2:
            return

        pline = RawPolyline(vertices=vertices, is_closed=is_closed, layer=layer)
        data.polylines.append(pline)

        # Try to detect rectangles from closed 4-vertex polylines
        if is_closed and len(vertices) == 4:
            rect = self._try_rectangle(vertices, layer)
            if rect:
                data.rectangles.append(rect)

    def _process_polyline(
        self, entity: Polyline, layer: str, data: ParsedCADData
    ) -> None:
        vertices = []
        for vertex in entity.vertices:
            x, y = vertex.dxf.location.x, vertex.dxf.location.y
            vertices.append(self._scale_point(x, y))

        if len(vertices) < 2:
            return

        is_closed = entity.is_closed
        pline = RawPolyline(vertices=vertices, is_closed=is_closed, layer=layer)
        data.polylines.append(pline)

        if is_closed and len(vertices) == 4:
            rect = self._try_rectangle(vertices, layer)
            if rect:
                data.rectangles.append(rect)

    def _process_circle(
        self, entity: Circle, layer: str, data: ParsedCADData
    ) -> None:
        center = self._scale_point(entity.dxf.center.x, entity.dxf.center.y)
        radius = entity.dxf.radius * self._scale
        data.circles.append(RawCircle(center=center, radius=radius, layer=layer))

    def _process_text(self, entity: Text, layer: str, data: ParsedCADData) -> None:
        content = entity.dxf.text.strip()
        if not content:
            return
        insert = entity.dxf.insert
        pos = self._scale_point(insert.x, insert.y)
        height = entity.dxf.height * self._scale
        data.texts.append(RawText(content=content, position=pos, height=height, layer=layer))

    def _process_mtext(self, entity: MText, layer: str, data: ParsedCADData) -> None:
        content = entity.plain_text().strip()
        if not content:
            return
        insert = entity.dxf.insert
        pos = self._scale_point(insert.x, insert.y)
        height = entity.dxf.char_height * self._scale
        data.texts.append(RawText(content=content, position=pos, height=height, layer=layer))

    def _process_insert(
        self, entity: Insert, layer: str, data: ParsedCADData
    ) -> None:
        """Explode block references and process their child entities."""
        try:
            for child in entity.virtual_entities():
                self._process_entity(child, data)
        except Exception as e:
            logger.debug(f"Could not explode INSERT on layer '{layer}': {e}")

    @staticmethod
    def _try_rectangle(
        vertices: list[tuple[float, float]], layer: str
    ) -> Optional[RawRectangle]:
        """
        Attempt to classify 4 vertices as a rectangle.

        Checks that opposite sides are equal length and all angles ≈ 90°.
        Returns RawRectangle or None.
        """
        if len(vertices) != 4:
            return None

        pts = np.array(vertices, dtype=np.float64)
        # Compute edge vectors
        edges = np.diff(np.vstack([pts, pts[0:1]]), axis=0)
        lengths = np.linalg.norm(edges, axis=1)

        if np.any(lengths < 1.0):  # Degenerate
            return None

        # Check opposite sides equal (with 5% tolerance)
        if not (
            np.isclose(lengths[0], lengths[2], rtol=0.05)
            and np.isclose(lengths[1], lengths[3], rtol=0.05)
        ):
            return None

        # Check right angles via dot product
        for i in range(4):
            e1 = edges[i]
            e2 = edges[(i + 1) % 4]
            dot = np.dot(e1, e2)
            if abs(dot) > 0.05 * lengths[i] * lengths[(i + 1) % 4]:
                return None

        # Compute properties
        centroid = pts.mean(axis=0)
        w = float(lengths[0])
        h = float(lengths[1])
        # Rotation = angle of first edge from X-axis
        angle = float(np.degrees(np.arctan2(edges[0][1], edges[0][0])))

        # Canonical: width >= height
        if w < h:
            w, h = h, w
            angle += 90.0

        return RawRectangle(
            vertices=vertices,
            centroid=(float(centroid[0]), float(centroid[1])),
            width=w,
            height=h,
            rotation=angle % 360.0,
            layer=layer,
        )
