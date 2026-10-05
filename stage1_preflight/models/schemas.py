"""
Pydantic schemas for the Structural BIM Data Model.

These models define the JSON contract between Stage 1 (Pre-Flight)
and Stage 2 (Revit Builder). All dimensions are in millimetres.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ElementType(str, Enum):
    BEAM = "beam"
    COLUMN = "column"
    SLAB = "slab"
    WALL = "wall"


class ConfidenceLevel(str, Enum):
    HIGH = "high"        # >= 0.85
    MEDIUM = "medium"    # >= 0.70
    LOW = "low"          # < 0.70


# ---------------------------------------------------------------------------
# Geometry primitives (all values in mm)
# ---------------------------------------------------------------------------

class Point3D(BaseModel):
    """A 3D point in millimetres."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.z]

    @classmethod
    def from_list(cls, coords: list[float]) -> "Point3D":
        return cls(x=coords[0], y=coords[1], z=coords[2] if len(coords) > 2 else 0.0)


class BoundingBox2D(BaseModel):
    """Axis-aligned bounding box in the XY plane."""
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return abs(self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return abs(self.max_y - self.min_y)

    @property
    def centroid(self) -> Point3D:
        return Point3D(
            x=(self.min_x + self.max_x) / 2.0,
            y=(self.min_y + self.max_y) / 2.0,
            z=0.0,
        )


# ---------------------------------------------------------------------------
# Structural element schemas
# ---------------------------------------------------------------------------

class StructuralElementBase(BaseModel):
    """Base schema for all structural elements."""
    id: str = Field(..., description="Unique element identifier, e.g. B_01, C_05")
    element_type: ElementType
    layer: str = Field("", description="Source CAD layer name")
    label_text: str = Field("", description="Raw text label extracted from CAD")
    confidence: float = Field(
        1.0, ge=0.0, le=1.0,
        description="Recognition confidence score, 0.0–1.0",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="List of flagged issues for exception inspector",
    )
    user_verified: bool = Field(
        False,
        description="Set True once the user has manually validated this element",
    )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        if self.confidence >= 0.85:
            return ConfidenceLevel.HIGH
        elif self.confidence >= 0.70:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW


class BeamElement(StructuralElementBase):
    """A structural beam defined by centerline start/end and cross-section."""
    element_type: ElementType = ElementType.BEAM
    width: float = Field(..., gt=0, description="Beam width in mm")
    depth: float = Field(..., gt=0, description="Beam depth in mm")
    start: Point3D = Field(..., description="Centerline start point (mm)")
    end: Point3D = Field(..., description="Centerline end point (mm)")
    rotation: float = Field(0.0, description="Cross-section rotation in degrees")

    @property
    def length(self) -> float:
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        dz = self.end.z - self.start.z
        return (dx**2 + dy**2 + dz**2) ** 0.5


class ColumnElement(StructuralElementBase):
    """A structural column defined by centroid and dimensions."""
    element_type: ElementType = ElementType.COLUMN
    width: float = Field(..., gt=0, description="Column width (X) in mm")
    depth: float = Field(..., gt=0, description="Column depth (Y) in mm")
    centroid: Point3D = Field(..., description="Column centroid (mm)")
    height: float = Field(3200.0, gt=0, description="Column height in mm")
    is_circular: bool = Field(False, description="True if the column is circular")
    diameter: Optional[float] = Field(None, gt=0, description="Diameter if circular (mm)")

    @field_validator("diameter")
    @classmethod
    def check_diameter(cls, v, info):
        if info.data.get("is_circular") and v is None:
            raise ValueError("Circular columns must have a diameter")
        return v


class SlabElement(StructuralElementBase):
    """A structural slab defined by a boundary polygon and thickness."""
    element_type: ElementType = ElementType.SLAB
    thickness: float = Field(..., gt=0, description="Slab thickness in mm")
    boundary: list[Point3D] = Field(
        ..., min_length=3,
        description="Ordered boundary vertices (closed polygon, mm)",
    )

    @property
    def is_closed(self) -> bool:
        if len(self.boundary) < 3:
            return False
        first = self.boundary[0]
        last = self.boundary[-1]
        dist = ((first.x - last.x)**2 + (first.y - last.y)**2) ** 0.5
        return dist < 1.0  # 1 mm tolerance


class WallElement(StructuralElementBase):
    """A structural wall defined by centerline and thickness."""
    element_type: ElementType = ElementType.WALL
    thickness: float = Field(..., gt=0, description="Wall thickness in mm")
    start: Point3D = Field(..., description="Centerline start point (mm)")
    end: Point3D = Field(..., description="Centerline end point (mm)")
    height: float = Field(3200.0, gt=0, description="Wall height in mm")

    @property
    def length(self) -> float:
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        return (dx**2 + dy**2) ** 0.5


# ---------------------------------------------------------------------------
# Project-level container
# ---------------------------------------------------------------------------

class StructuralLevel(BaseModel):
    """All elements belonging to a single floor level."""
    level: str = Field(..., description="Level name, e.g. L01, L05")
    base_elevation: float = Field(
        0.0, description="Level base elevation in mm above origin",
    )
    floor_height: float = Field(
        3200.0, gt=0, description="Floor-to-floor height in mm",
    )
    beams: list[BeamElement] = Field(default_factory=list)
    columns: list[ColumnElement] = Field(default_factory=list)
    slabs: list[SlabElement] = Field(default_factory=list)
    walls: list[WallElement] = Field(default_factory=list)

    @property
    def total_elements(self) -> int:
        return len(self.beams) + len(self.columns) + len(self.slabs) + len(self.walls)

    @property
    def flagged_elements(self) -> list[StructuralElementBase]:
        """Return elements with confidence < 0.7 or non-empty issues."""
        flagged = []
        for elem in self.all_elements:
            if elem.confidence < 0.7 or elem.issues:
                flagged.append(elem)
        return flagged

    @property
    def all_elements(self) -> list[StructuralElementBase]:
        return self.beams + self.columns + self.slabs + self.walls


class StructuralProject(BaseModel):
    """Root schema for the entire structural project JSON export."""
    project_name: str = Field("Untitled Project", description="Project name")
    units: str = Field("mm", description="Units system (always mm internally)")
    source_file: str = Field("", description="Original CAD file path")
    levels: list[StructuralLevel] = Field(default_factory=list)

    def to_json_file(self, path: Path | str) -> None:
        """Serialize the validated project to a JSON file."""
        path = Path(path)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_json_file(cls, path: Path | str) -> "StructuralProject":
        """Deserialize a project from a JSON file."""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @property
    def total_elements(self) -> int:
        return sum(level.total_elements for level in self.levels)
