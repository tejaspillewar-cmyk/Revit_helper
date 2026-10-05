"""
3D Viewport: PyVista-based 3D visualization engine.

Procedurally extrudes 2D structural elements into 3D geometry and renders
them in a VTK canvas with color-coding and raycasting support.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pyvista as pv

from stage1_preflight.models.schemas import (
    BeamElement,
    ColumnElement,
    ElementType,
    SlabElement,
    StructuralLevel,
    WallElement,
)
from stage1_preflight.utils.constants import COLORS_FLOAT

logger = logging.getLogger(__name__)


class StructuralViewport:
    """
    Generates and manages 3D meshes from structural elements.

    Each element is extruded from its 2D representation:
    - Columns: extruded upward from base elevation
    - Beams: extruded downward from floor datum
    - Slabs: extruded by thickness at floor level
    - Walls: extruded upward from base elevation
    """

    def __init__(self):
        self._meshes: dict[str, pv.PolyData] = {}     # id → mesh
        self._actors: dict[str, object] = {}           # id → VTK actor
        self._element_map: dict[str, object] = {}      # id → element schema
        self._mesh_to_id: dict[int, str] = {}          # mesh hash → element id

    @property
    def meshes(self) -> dict[str, pv.PolyData]:
        return self._meshes

    @property
    def element_map(self) -> dict[str, object]:
        return self._element_map

    def build_meshes(self, level: StructuralLevel) -> list[tuple[str, pv.PolyData, tuple]]:
        """
        Build 3D meshes from a StructuralLevel.

        Returns a list of (element_id, mesh, color_rgba) tuples for
        adding to a plotter.
        """
        self._meshes.clear()
        self._element_map.clear()
        self._mesh_to_id.clear()

        result: list[tuple[str, pv.PolyData, tuple]] = []

        for beam in level.beams:
            mesh = self._extrude_beam(beam, level)
            if mesh:
                self._meshes[beam.id] = mesh
                self._element_map[beam.id] = beam
                self._mesh_to_id[id(mesh)] = beam.id
                color = COLORS_FLOAT["flagged"] if beam.confidence < 0.7 else COLORS_FLOAT["beam"]
                result.append((beam.id, mesh, color))

        for col in level.columns:
            mesh = self._extrude_column(col, level)
            if mesh:
                self._meshes[col.id] = mesh
                self._element_map[col.id] = col
                self._mesh_to_id[id(mesh)] = col.id
                color = COLORS_FLOAT["flagged"] if col.confidence < 0.7 else COLORS_FLOAT["column"]
                result.append((col.id, mesh, color))

        for slab in level.slabs:
            mesh = self._extrude_slab(slab, level)
            if mesh:
                self._meshes[slab.id] = mesh
                self._element_map[slab.id] = slab
                self._mesh_to_id[id(mesh)] = slab.id
                color = COLORS_FLOAT["flagged"] if slab.confidence < 0.7 else COLORS_FLOAT["slab"]
                result.append((slab.id, mesh, color))

        for wall in level.walls:
            mesh = self._extrude_wall(wall, level)
            if mesh:
                self._meshes[wall.id] = mesh
                self._element_map[wall.id] = wall
                self._mesh_to_id[id(mesh)] = wall.id
                color = COLORS_FLOAT["flagged"] if wall.confidence < 0.7 else COLORS_FLOAT["wall"]
                result.append((wall.id, mesh, color))

        logger.info(f"Built {len(result)} 3D meshes")
        return result

    def get_element_by_mesh(self, mesh: pv.PolyData) -> Optional[object]:
        """Look up the structural element associated with a mesh."""
        elem_id = self._mesh_to_id.get(id(mesh))
        if elem_id:
            return self._element_map.get(elem_id)
        return None

    def get_element_id_by_mesh(self, mesh: pv.PolyData) -> Optional[str]:
        """Look up the element ID associated with a mesh."""
        return self._mesh_to_id.get(id(mesh))

    # -------------------------------------------------------------------
    # Extrusion methods
    # -------------------------------------------------------------------

    @staticmethod
    def _extrude_beam(beam: BeamElement, level: StructuralLevel) -> Optional[pv.PolyData]:
        """
        Extrude a beam as a rectangular box along its centerline.
        Beams hang downward from the floor datum.
        """
        try:
            start = np.array([beam.start.x, beam.start.y, beam.start.z])
            end = np.array([beam.end.x, beam.end.y, beam.end.z])
            direction = end - start
            length = np.linalg.norm(direction)

            if length < 1.0:
                return None

            dir_norm = direction / length

            # Perpendicular vector in XY plane
            perp = np.array([-dir_norm[1], dir_norm[0], 0.0])
            half_w = beam.width / 2.0

            # Beam hangs downward from its Z position
            z_top = beam.start.z
            z_bot = z_top - beam.depth

            # Build 8 corner points of the box
            corners = []
            for along in [start, end]:
                for side in [-1, 1]:
                    for vert in [z_bot, z_top]:
                        pt = along + side * half_w * perp
                        pt[2] = vert
                        corners.append(pt)

            corners = np.array(corners)

            # Create box mesh from corners
            mesh = pv.PolyData(corners)
            box_mesh = mesh.delaunay_3d().extract_surface()
            return box_mesh

        except Exception as e:
            logger.warning(f"Failed to extrude beam {beam.id}: {e}")
            return None

    @staticmethod
    def _extrude_column(
        col: ColumnElement, level: StructuralLevel
    ) -> Optional[pv.PolyData]:
        """
        Extrude a column upward from base elevation.
        Rectangular or circular cross-section.
        """
        try:
            cx, cy = col.centroid.x, col.centroid.y
            z_base = col.centroid.z
            z_top = z_base + col.height

            if col.is_circular and col.diameter:
                # Cylinder
                radius = col.diameter / 2.0
                center = (cx, cy, (z_base + z_top) / 2.0)
                mesh = pv.Cylinder(
                    center=center,
                    direction=(0, 0, 1),
                    radius=radius,
                    height=col.height,
                    resolution=24,
                    capping=True,
                )
            else:
                # Rectangular box
                half_w = col.width / 2.0
                half_d = col.depth / 2.0
                mesh = pv.Box(
                    bounds=(
                        cx - half_w, cx + half_w,
                        cy - half_d, cy + half_d,
                        z_base, z_top,
                    )
                )

            return mesh

        except Exception as e:
            logger.warning(f"Failed to extrude column {col.id}: {e}")
            return None

    @staticmethod
    def _extrude_slab(
        slab: SlabElement, level: StructuralLevel
    ) -> Optional[pv.PolyData]:
        """
        Extrude a slab polygon downward by its thickness.
        """
        try:
            # Create polygon face from boundary
            pts = np.array([
                [p.x, p.y, p.z] for p in slab.boundary
            ])

            if len(pts) < 3:
                return None

            # Close polygon if needed
            if not np.allclose(pts[0], pts[-1]):
                pts = np.vstack([pts, pts[0:1]])

            n = len(pts) - 1  # Exclude closing duplicate

            # Top face points
            top_pts = pts[:n].copy()
            # Bottom face points
            bot_pts = top_pts.copy()
            bot_pts[:, 2] -= slab.thickness

            all_pts = np.vstack([top_pts, bot_pts])

            # Build faces
            faces = []

            # Top face
            top_face = [n] + list(range(n))
            faces.extend(top_face)

            # Bottom face (reversed winding)
            bot_face = [n] + list(range(2 * n - 1, n - 1, -1))
            faces.extend(bot_face)

            # Side faces
            for i in range(n):
                j = (i + 1) % n
                quad = [4, i, j, j + n, i + n]
                faces.extend(quad)

            mesh = pv.PolyData(all_pts, faces=faces)
            return mesh

        except Exception as e:
            logger.warning(f"Failed to extrude slab {slab.id}: {e}")
            return None

    @staticmethod
    def _extrude_wall(
        wall: WallElement, level: StructuralLevel
    ) -> Optional[pv.PolyData]:
        """
        Extrude a wall along its centerline with thickness.
        """
        try:
            start = np.array([wall.start.x, wall.start.y, wall.start.z])
            end = np.array([wall.end.x, wall.end.y, wall.end.z])
            direction = end - start
            length = np.linalg.norm(direction[:2])

            if length < 1.0:
                return None

            dir_norm = direction[:2] / length
            perp = np.array([-dir_norm[1], dir_norm[0]])
            half_t = wall.thickness / 2.0

            z_base = wall.start.z
            z_top = z_base + wall.height

            # 4 bottom corners, 4 top corners
            corners = []
            for along in [start[:2], end[:2]]:
                for side in [-1, 1]:
                    offset = along + side * half_t * perp
                    corners.append([offset[0], offset[1], z_base])
                    corners.append([offset[0], offset[1], z_top])

            corners = np.array(corners)
            mesh = pv.PolyData(corners)
            box_mesh = mesh.delaunay_3d().extract_surface()
            return box_mesh

        except Exception as e:
            logger.warning(f"Failed to extrude wall {wall.id}: {e}")
            return None

    # -------------------------------------------------------------------
    # Selection helpers
    # -------------------------------------------------------------------

    def find_element_at_point(
        self, point: np.ndarray, tolerance: float = 100.0
    ) -> Optional[str]:
        """
        Find the element ID closest to a 3D point.
        Used for raycasting results.
        """
        best_id = None
        best_dist = float("inf")

        for elem_id, mesh in self._meshes.items():
            try:
                closest_pt = mesh.find_closest_point(point)
                pt = mesh.points[closest_pt]
                dist = np.linalg.norm(pt - point)
                if dist < best_dist and dist < tolerance:
                    best_dist = dist
                    best_id = elem_id
            except Exception:
                continue

        return best_id
