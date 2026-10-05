"""
Adapter that bridges the ETABSConnector robust CAD parsing pipeline 
with the REVIT_builder's expected Pydantic schema structure.
"""

from __future__ import annotations

import logging
from typing import Optional

from stage1_preflight.models.schemas import (
    BeamElement,
    ColumnElement,
    ElementType,
    Point3D,
    SlabElement,
    StructuralLevel,
    WallElement,
)
from stage1_preflight.parsers.cad_pipeline.pipeline import run_pipeline

logger = logging.getLogger(__name__)

def parse_with_robust_pipeline(
    filepath: str,
    layer_mapping: dict[str, Optional[ElementType]],
    floor_height: float,
    base_elevation: float,
    level_name: str,
) -> StructuralLevel:
    """
    Execute the robust cad_pipeline and convert its output to a StructuralLevel.
    """
    
    # 1. Convert our layer_mapping dict to the 'buckets' expected by ETABS pipeline
    buckets: dict[str, list[str]] = {
        "beams": [],
        "columns": [],
        "slabs": [],
        "str_walls": [],
        "ns_walls": [],
    }
    
    for layer, elem_type in layer_mapping.items():
        if elem_type == ElementType.BEAM:
            buckets["beams"].append(layer)
        elif elem_type == ElementType.COLUMN:
            buckets["columns"].append(layer)
        elif elem_type == ElementType.SLAB:
            buckets["slabs"].append(layer)
        elif elem_type == ElementType.WALL:
            buckets["str_walls"].append(layer)

    # 2. Run the robust pipeline
    logger.info("Running robust CAD pipeline (ETABS logic)...")
    
    def log_redirect(msg: str):
        logger.info(msg)
        
    result = run_pipeline(
        dxf_path=filepath, 
        buckets=buckets, 
        auto_connect=True, 
        log=log_redirect
    )
    
    level = StructuralLevel(
        level=level_name,
        base_elevation=base_elevation,
        floor_height=floor_height,
    )
    
    # 3. Translate parsed entities to our Pydantic schemas
    
    # --- Beams ---
    for b in result.get("beams", []):
        confidence = 1.0 if not b.warnings else 0.6
        depth = b.depth_mm if b.depth_mm > 0 else 600.0  # Default depth if unknown
        level.beams.append(BeamElement(
            id=b.name,
            width=b.width_mm,
            depth=depth,
            start=Point3D(x=b.p1[0], y=b.p1[1], z=base_elevation + floor_height),
            end=Point3D(x=b.p2[0], y=b.p2[1], z=base_elevation + floor_height),
            layer=b.layer,
            label_text=f"{b.name} ({b.dimension_source})",
            confidence=confidence,
            issues=b.warnings,
        ))
        
    # --- Columns ---
    for c in result.get("columns", []):
        confidence = 1.0 if not c.warnings else 0.6
        depth = c.depth_mm if c.depth_mm > 0 else c.width_mm
        diameter = getattr(c, "diameter_mm", 0.0)
        level.columns.append(ColumnElement(
            id=c.name,
            width=c.width_mm,
            depth=depth,
            centroid=Point3D(x=c.centroid[0], y=c.centroid[1], z=base_elevation),
            height=floor_height,
            is_circular=getattr(c, "is_circular", False),
            diameter=diameter if diameter > 0 else None,
            layer=c.layer,
            label_text=f"{c.name} ({c.dimension_source})",
            confidence=confidence,
            issues=c.warnings,
        ))

    # --- Slabs ---
    for s in result.get("slabs", []):
        confidence = 1.0 if not s.warnings else 0.6
        thickness = s.thickness_mm if s.thickness_mm > 0 else 150.0
        boundary = [
            Point3D(x=v[0], y=v[1], z=base_elevation + floor_height)
            for v in s.vertices
        ]
        level.slabs.append(SlabElement(
            id=s.name,
            thickness=thickness,
            boundary=boundary,
            layer=s.layer,
            label_text=f"{s.name} ({s.thickness_source})",
            confidence=confidence,
            issues=s.warnings,
        ))
        
    # --- Walls ---
    for w in result.get("walls", []):
        confidence = 1.0 if not w.warnings else 0.6
        thickness = w.thickness_mm if w.thickness_mm > 0 else 200.0
        level.walls.append(WallElement(
            id=w.name,
            thickness=thickness,
            start=Point3D(x=w.p1[0], y=w.p1[1], z=base_elevation),
            end=Point3D(x=w.p2[0], y=w.p2[1], z=base_elevation),
            height=floor_height,
            layer=w.layer,
            label_text=f"{w.name} ({w.thickness_source})",
            confidence=confidence,
            issues=w.warnings,
        ))
        
    return level
