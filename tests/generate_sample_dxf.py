"""
Generate a sample DXF framing plan for testing the Pre-Flight application.

Creates a realistic structural layout with:
- 6 columns (rectangular + circular)
- 8 beams connecting them
- 2 slab regions
- 2 shear walls
- Text labels for sections and thicknesses

All dimensions in millimetres.
"""

import ezdxf
from ezdxf.units import MM


def create_sample_dxf(filepath: str = "sample_framing.dxf"):
    """Generate a sample structural DXF file."""
    doc = ezdxf.new("R2010")
    doc.units = MM
    doc.header["$INSUNITS"] = 4  # Millimetres

    msp = doc.modelspace()

    # Create layers
    doc.layers.add("S-COLUMN", color=1)       # Red
    doc.layers.add("S-BEAM", color=4)          # Cyan
    doc.layers.add("S-SLAB", color=40)         # Orange
    doc.layers.add("S-WALL", color=3)          # Green
    doc.layers.add("S-TEXT", color=7)           # White

    # ===================================================================
    # COLUMNS: 600x600 rectangular grid + one circular
    # ===================================================================
    column_positions = [
        (0, 0), (5000, 0), (10000, 0),
        (0, 6000), (5000, 6000), (10000, 6000),
    ]

    for i, (cx, cy) in enumerate(column_positions):
        w, h = 600, 600
        half_w, half_h = w / 2, h / 2

        # Draw column as closed rectangle
        msp.add_lwpolyline(
            [
                (cx - half_w, cy - half_h),
                (cx + half_w, cy - half_h),
                (cx + half_w, cy + half_h),
                (cx - half_w, cy + half_h),
            ],
            close=True,
            dxfattribs={"layer": "S-COLUMN"},
        )

        # Label
        msp.add_text(
            f"C{i+1} 600x600",
            dxfattribs={
                "layer": "S-TEXT",
                "height": 150,
                "insert": (cx - 200, cy - 100),
            },
        )

    # Add a circular column
    msp.add_circle(
        center=(15000, 3000),
        radius=300,
        dxfattribs={"layer": "S-COLUMN"},
    )
    msp.add_text(
        "C7 DIA 600",
        dxfattribs={
            "layer": "S-TEXT",
            "height": 150,
            "insert": (14700, 2800),
        },
    )

    # ===================================================================
    # BEAMS: Connecting columns with parallel lines (edge representation)
    # ===================================================================
    beam_data = [
        # (start, end, width_mm, label)
        ((0, 0), (5000, 0), 300, "B1 300x450"),
        ((5000, 0), (10000, 0), 300, "B2 300x450"),
        ((0, 6000), (5000, 6000), 300, "B3 300x600"),
        ((5000, 6000), (10000, 6000), 300, "B4 300x600"),
        ((0, 0), (0, 6000), 300, "B5 300x450"),
        ((5000, 0), (5000, 6000), 300, "B6 300x450"),
        ((10000, 0), (10000, 6000), 300, "B7 300x450"),
        ((0, 3000), (10000, 3000), 300, "B8 300x450"),
    ]

    for (sx, sy), (ex, ey), width, label in beam_data:
        half_w = width / 2

        # Determine perpendicular offset
        dx = ex - sx
        dy = ey - sy
        length = (dx**2 + dy**2) ** 0.5
        if length < 1:
            continue
        nx, ny = -dy / length, dx / length

        # Two parallel lines
        msp.add_line(
            (sx + nx * half_w, sy + ny * half_w),
            (ex + nx * half_w, ey + ny * half_w),
            dxfattribs={"layer": "S-BEAM"},
        )
        msp.add_line(
            (sx - nx * half_w, sy - ny * half_w),
            (ex - nx * half_w, ey - ny * half_w),
            dxfattribs={"layer": "S-BEAM"},
        )

        # Label at midpoint
        mid_x = (sx + ex) / 2
        mid_y = (sy + ey) / 2
        msp.add_text(
            label,
            dxfattribs={
                "layer": "S-TEXT",
                "height": 120,
                "insert": (mid_x + 50, mid_y + 50),
            },
        )

    # ===================================================================
    # SLABS: Large closed polygons
    # ===================================================================
    # Left bay slab
    msp.add_lwpolyline(
        [
            (300, 300),
            (4700, 300),
            (4700, 2700),
            (300, 2700),
        ],
        close=True,
        dxfattribs={"layer": "S-SLAB"},
    )
    msp.add_text(
        "150 THK",
        dxfattribs={
            "layer": "S-TEXT",
            "height": 200,
            "insert": (2000, 1400),
        },
    )

    # Right bay slab
    msp.add_lwpolyline(
        [
            (5300, 300),
            (9700, 300),
            (9700, 5700),
            (5300, 5700),
        ],
        close=True,
        dxfattribs={"layer": "S-SLAB"},
    )
    msp.add_text(
        "200 THK",
        dxfattribs={
            "layer": "S-TEXT",
            "height": 200,
            "insert": (7000, 3000),
        },
    )

    # ===================================================================
    # WALLS: Parallel lines with larger separation
    # ===================================================================
    # Shear wall 1 (left edge, vertical)
    wall_thickness = 200
    half_t = wall_thickness / 2
    msp.add_line(
        (-half_t, 1000), (-half_t, 5000),
        dxfattribs={"layer": "S-WALL"},
    )
    msp.add_line(
        (half_t, 1000), (half_t, 5000),
        dxfattribs={"layer": "S-WALL"},
    )
    msp.add_text(
        "SW1 200 THK",
        dxfattribs={
            "layer": "S-TEXT",
            "height": 100,
            "insert": (-300, 3000),
        },
    )

    # Shear wall 2 (right edge, vertical)
    msp.add_line(
        (10000 - half_t, 1000), (10000 - half_t, 5000),
        dxfattribs={"layer": "S-WALL"},
    )
    msp.add_line(
        (10000 + half_t, 1000), (10000 + half_t, 5000),
        dxfattribs={"layer": "S-WALL"},
    )
    msp.add_text(
        "SW2 200 THK",
        dxfattribs={
            "layer": "S-TEXT",
            "height": 100,
            "insert": (10200, 3000),
        },
    )

    # ===================================================================
    # A beam with MISSING label (to test low-confidence flagging)
    # ===================================================================
    msp.add_line(
        (0, 4500 + 150), (5000, 4500 + 150),
        dxfattribs={"layer": "S-BEAM"},
    )
    msp.add_line(
        (0, 4500 - 150), (5000, 4500 - 150),
        dxfattribs={"layer": "S-BEAM"},
    )
    # No text label — this should be flagged by the exception inspector

    # Save
    doc.saveas(filepath)
    print(f"Sample DXF saved to: {filepath}")
    print(f"  Columns: 7 (6 rect + 1 circular)")
    print(f"  Beams: 9 (8 labeled + 1 unlabeled)")
    print(f"  Slabs: 2")
    print(f"  Walls: 2")


if __name__ == "__main__":
    create_sample_dxf()
