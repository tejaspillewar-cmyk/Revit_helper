"""
Stage 2: Revit Model Builder (pyRevit Script)

This script reads the validated JSON contract from Stage 1 and constructs
native Revit structural elements using the Revit API.

Deployment:
    1. Copy this file into your pyRevit extension folder.
    2. In Revit, click the pyRevit button to run it.
    3. Browse to the exported .json file when prompted.

Compatible with: Revit 2022+ via pyRevit (CPython3 or IronPython)

NOTE: This script must be run INSIDE Revit through pyRevit.
      It uses Revit API classes that are only available in that environment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Revit API Imports (available only inside Revit/pyRevit environment)
# --------------------------------------------------------------------------
try:
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    clr.AddReference("RevitServices")

    from Autodesk.Revit.DB import (
        BuiltInCategory,
        BuiltInParameter,
        CurveLoop,
        ElementId,
        FilteredElementCollector,
        Floor,
        Level,
        Line,
        StructuralType,
        Transaction,
        XYZ,
    )
    from Autodesk.Revit.DB.Structure import StructuralType
    from Autodesk.Revit.UI import TaskDialog

    # pyRevit document variables
    doc = __revit__.ActiveUIDocument.Document  # type: ignore
    uidoc = __revit__.ActiveUIDocument  # type: ignore

    REVIT_AVAILABLE = True

except ImportError:
    REVIT_AVAILABLE = False
    print("WARNING: Revit API not available. Running in standalone/test mode.")


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
MM_TO_FEET = 1.0 / 304.8  # Revit internal units are feet


def mm_to_feet(mm_value: float) -> float:
    """Convert millimetres to feet (Revit internal units)."""
    return mm_value * MM_TO_FEET


def xyz_from_mm(x: float, y: float, z: float) -> "XYZ":
    """Create a Revit XYZ point from millimetre coordinates."""
    return XYZ(mm_to_feet(x), mm_to_feet(y), mm_to_feet(z))


# --------------------------------------------------------------------------
# JSON Loader
# --------------------------------------------------------------------------

def load_json(filepath: str) -> dict:
    """Load and validate the structural JSON contract."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Basic validation
    if "levels" not in data:
        raise ValueError("Invalid JSON: missing 'levels' key")

    total = sum(
        len(lvl.get("beams", []))
        + len(lvl.get("columns", []))
        + len(lvl.get("slabs", []))
        + len(lvl.get("walls", []))
        for lvl in data["levels"]
    )
    print(f"Loaded {total} elements from {len(data['levels'])} levels")
    return data


# --------------------------------------------------------------------------
# Type Resolver
# --------------------------------------------------------------------------

class RevitTypeResolver:
    """
    Resolves Revit FamilySymbols for structural elements.
    If a required type doesn't exist, duplicates a base type
    and sets the new parameters.
    """

    def __init__(self, doc):
        self.doc = doc
        self._beam_types = {}
        self._column_types = {}
        self._floor_types = {}
        self._cache_types()

    def _cache_types(self):
        """Cache existing structural family types."""
        # Structural Framing (Beams)
        collector = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsElementType()
        )
        for sym in collector:
            name = sym.get_Parameter(
                BuiltInParameter.ALL_MODEL_TYPE_NAME
            ).AsString()
            self._beam_types[name] = sym

        # Structural Columns
        collector = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_StructuralColumns)
            .WhereElementIsElementType()
        )
        for sym in collector:
            name = sym.get_Parameter(
                BuiltInParameter.ALL_MODEL_TYPE_NAME
            ).AsString()
            self._column_types[name] = sym

        # Floor Types
        collector = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Floors)
            .WhereElementIsElementType()
        )
        for sym in collector:
            name = sym.get_Parameter(
                BuiltInParameter.ALL_MODEL_TYPE_NAME
            ).AsString()
            self._floor_types[name] = sym

    def get_beam_type(self, width_mm: float, depth_mm: float):
        """Get or create a beam family symbol for the given dimensions."""
        type_name = f"Concrete-Rectangular Beam {int(width_mm)}x{int(depth_mm)}"

        if type_name in self._beam_types:
            sym = self._beam_types[type_name]
            if not sym.IsActive:
                sym.Activate()
            return sym

        # Duplicate from first available beam type
        if self._beam_types:
            base = next(iter(self._beam_types.values()))
            new_type = base.Duplicate(type_name)

            # Set dimensions
            b_param = new_type.LookupParameter("b") or new_type.LookupParameter("Width")
            if b_param:
                b_param.Set(mm_to_feet(width_mm))

            h_param = new_type.LookupParameter("h") or new_type.LookupParameter("Depth")
            if h_param:
                h_param.Set(mm_to_feet(depth_mm))

            if not new_type.IsActive:
                new_type.Activate()

            self._beam_types[type_name] = new_type
            print(f"  Created beam type: {type_name}")
            return new_type

        raise RuntimeError("No beam family types available to duplicate from.")

    def get_column_type(
        self, width_mm: float, depth_mm: float, is_circular: bool = False,
        diameter_mm: float = None,
    ):
        """Get or create a column family symbol."""
        if is_circular and diameter_mm:
            type_name = f"Concrete-Round-Column D{int(diameter_mm)}"
        else:
            type_name = f"Concrete-Rectangular-Column {int(width_mm)}x{int(depth_mm)}"

        if type_name in self._column_types:
            sym = self._column_types[type_name]
            if not sym.IsActive:
                sym.Activate()
            return sym

        if self._column_types:
            base = next(iter(self._column_types.values()))
            new_type = base.Duplicate(type_name)

            if is_circular and diameter_mm:
                d_param = new_type.LookupParameter("Diameter")
                if d_param:
                    d_param.Set(mm_to_feet(diameter_mm))
            else:
                b_param = new_type.LookupParameter("b") or new_type.LookupParameter("Width")
                if b_param:
                    b_param.Set(mm_to_feet(width_mm))
                h_param = new_type.LookupParameter("h") or new_type.LookupParameter("Depth")
                if h_param:
                    h_param.Set(mm_to_feet(depth_mm))

            if not new_type.IsActive:
                new_type.Activate()

            self._column_types[type_name] = new_type
            print(f"  Created column type: {type_name}")
            return new_type

        raise RuntimeError("No column family types available to duplicate from.")

    def get_floor_type(self, thickness_mm: float):
        """Get or create a floor type for the given thickness."""
        type_name = f"Concrete Slab {int(thickness_mm)}mm"

        if type_name in self._floor_types:
            return self._floor_types[type_name]

        if self._floor_types:
            base = next(iter(self._floor_types.values()))
            new_type = base.Duplicate(type_name)
            self._floor_types[type_name] = new_type
            print(f"  Created floor type: {type_name}")
            return new_type

        raise RuntimeError("No floor family types available to duplicate from.")


# --------------------------------------------------------------------------
# Level Resolver
# --------------------------------------------------------------------------

def get_or_create_level(doc, name: str, elevation_mm: float):
    """Get an existing level by name or create a new one."""
    collector = FilteredElementCollector(doc).OfClass(Level)
    for lvl in collector:
        if lvl.Name == name:
            return lvl

    # Create new level
    new_level = Level.Create(doc, mm_to_feet(elevation_mm))
    new_level.Name = name
    print(f"  Created level: {name} at {elevation_mm} mm")
    return new_level


# --------------------------------------------------------------------------
# Element Builders
# --------------------------------------------------------------------------

def build_beams(doc, beams: list, level, type_resolver: RevitTypeResolver) -> int:
    """Create beam instances from JSON data."""
    count = 0
    for beam_data in beams:
        try:
            width = beam_data["width"]
            depth = beam_data["depth"]
            start = beam_data["start"]
            end = beam_data["end"]

            symbol = type_resolver.get_beam_type(width, depth)

            # Create line from start to end
            start_pt = xyz_from_mm(start["x"], start["y"], start["z"])
            end_pt = xyz_from_mm(end["x"], end["y"], end["z"])
            line = Line.CreateBound(start_pt, end_pt)

            # Create beam instance
            instance = doc.Create.NewFamilyInstance(
                line, symbol, level, StructuralType.Beam
            )

            count += 1
            print(f"  Created beam: {beam_data.get('id', 'unknown')}")

        except Exception as e:
            print(f"  ERROR creating beam {beam_data.get('id', '?')}: {e}")

    return count


def build_columns(doc, columns: list, level, type_resolver: RevitTypeResolver) -> int:
    """Create column instances from JSON data."""
    count = 0
    for col_data in columns:
        try:
            width = col_data["width"]
            depth = col_data["depth"]
            centroid = col_data["centroid"]
            height = col_data.get("height", 3200)
            is_circular = col_data.get("is_circular", False)
            diameter = col_data.get("diameter")

            symbol = type_resolver.get_column_type(
                width, depth, is_circular, diameter
            )

            # Create column at centroid
            point = xyz_from_mm(centroid["x"], centroid["y"], centroid["z"])

            instance = doc.Create.NewFamilyInstance(
                point, symbol, level, StructuralType.Column
            )

            # Set top offset
            top_param = instance.get_Parameter(
                BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM
            )
            if top_param:
                top_param.Set(mm_to_feet(height))

            # Set base offset
            base_param = instance.get_Parameter(
                BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM
            )
            if base_param:
                base_param.Set(0.0)

            count += 1
            print(f"  Created column: {col_data.get('id', 'unknown')}")

        except Exception as e:
            print(f"  ERROR creating column {col_data.get('id', '?')}: {e}")

    return count


def build_slabs(doc, slabs: list, level, type_resolver: RevitTypeResolver) -> int:
    """Create floor/slab instances from JSON boundary data."""
    count = 0
    for slab_data in slabs:
        try:
            thickness = slab_data["thickness"]
            boundary = slab_data["boundary"]

            floor_type = type_resolver.get_floor_type(thickness)

            # Build CurveLoop from boundary points
            curve_loop = CurveLoop()
            pts = [
                xyz_from_mm(pt["x"], pt["y"], pt["z"])
                for pt in boundary
            ]
            # Close the polygon
            for i in range(len(pts)):
                j = (i + 1) % len(pts)
                line = Line.CreateBound(pts[i], pts[j])
                curve_loop.Append(line)

            # Create floor
            from System.Collections.Generic import List
            curve_loops = List[CurveLoop]()
            curve_loops.Add(curve_loop)

            floor = Floor.Create(
                doc,
                curve_loops,
                floor_type.Id,
                level.Id,
            )

            count += 1
            print(f"  Created slab: {slab_data.get('id', 'unknown')}")

        except Exception as e:
            print(f"  ERROR creating slab {slab_data.get('id', '?')}: {e}")

    return count


def build_walls(doc, walls: list, level, type_resolver: RevitTypeResolver) -> int:
    """Create wall instances from JSON data. Uses Revit Wall.Create."""
    count = 0
    for wall_data in walls:
        try:
            from Autodesk.Revit.DB import Wall, WallType

            thickness = wall_data["thickness"]
            start = wall_data["start"]
            end = wall_data["end"]
            height = wall_data.get("height", 3200)

            start_pt = xyz_from_mm(start["x"], start["y"], start["z"])
            end_pt = xyz_from_mm(end["x"], end["y"], end["z"])
            line = Line.CreateBound(start_pt, end_pt)

            # Find or create wall type
            wall_types = (
                FilteredElementCollector(doc)
                .OfClass(WallType)
                .ToElements()
            )
            wall_type = next(iter(wall_types), None)

            if wall_type:
                wall = Wall.Create(
                    doc,
                    line,
                    wall_type.Id,
                    level.Id,
                    mm_to_feet(height),
                    0.0,    # offset
                    False,  # flip
                    True,   # structural
                )
                count += 1
                print(f"  Created wall: {wall_data.get('id', 'unknown')}")

        except Exception as e:
            print(f"  ERROR creating wall {wall_data.get('id', '?')}: {e}")

    return count


# --------------------------------------------------------------------------
# Main Entry Point
# --------------------------------------------------------------------------

def main():
    """Main entry point for the Revit builder script."""
    if not REVIT_AVAILABLE:
        print("This script must be run inside Revit via pyRevit.")
        print("Usage: Place this script in your pyRevit extension and run from Revit.")

        # Test mode: validate JSON loading
        if len(sys.argv) > 1:
            data = load_json(sys.argv[1])
            print(f"\nJSON validation passed: {json.dumps(data, indent=2)[:500]}...")
        return

    # Prompt user to select JSON file
    from Autodesk.Revit.UI import UIApplication
    from Microsoft.Win32 import OpenFileDialog

    dialog = OpenFileDialog()
    dialog.Filter = "JSON Files (*.json)|*.json|All Files (*.*)|*.*"
    dialog.Title = "Import Structural JSON"

    if not dialog.ShowDialog():
        return

    filepath = dialog.FileName
    data = load_json(filepath)

    # Process each level
    type_resolver = RevitTypeResolver(doc)

    total_beams = 0
    total_columns = 0
    total_slabs = 0
    total_walls = 0

    with Transaction(doc, "Import Structural JSON") as t:
        t.Start()

        for level_data in data["levels"]:
            level_name = level_data.get("level", "L01")
            base_elev = level_data.get("base_elevation", 0.0)
            floor_height = level_data.get("floor_height", 3200.0)

            print(f"\nProcessing level: {level_name} (elev: {base_elev} mm)")

            level = get_or_create_level(doc, level_name, base_elev)

            # Build elements
            total_beams += build_beams(
                doc, level_data.get("beams", []), level, type_resolver
            )
            total_columns += build_columns(
                doc, level_data.get("columns", []), level, type_resolver
            )
            total_slabs += build_slabs(
                doc, level_data.get("slabs", []), level, type_resolver
            )
            total_walls += build_walls(
                doc, level_data.get("walls", []), level, type_resolver
            )

        t.Commit()

    # Summary
    summary = (
        f"Import Complete!\n\n"
        f"Beams: {total_beams}\n"
        f"Columns: {total_columns}\n"
        f"Slabs: {total_slabs}\n"
        f"Walls: {total_walls}\n"
        f"Total: {total_beams + total_columns + total_slabs + total_walls}"
    )
    print(summary)

    if REVIT_AVAILABLE:
        TaskDialog.Show("Structural JSON Import", summary)


if __name__ == "__main__":
    main()
