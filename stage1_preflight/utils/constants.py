"""
Constants and configuration for the pre-flight application.
"""

# ---------------------------------------------------------------------------
# Color scheme (RGBA, 0–255) for PyVista
# ---------------------------------------------------------------------------
COLORS = {
    "beam":   (0, 200, 220, 255),      # Cyan
    "column": (220, 50, 50, 255),       # Red
    "slab":   (255, 180, 40, 100),      # Translucent Amber
    "wall":   (80, 200, 80, 200),       # Green (semi-transparent)
    "default": (180, 180, 180, 255),    # Gray
    "flagged": (255, 100, 0, 255),      # Orange for flagged items
    "selected": (255, 255, 0, 255),     # Yellow highlight
}

# Normalised 0–1 colors for PyVista
COLORS_FLOAT = {
    k: tuple(c / 255.0 for c in v)
    for k, v in COLORS.items()
}

# ---------------------------------------------------------------------------
# Geometry & parsing tolerances (all in mm)
# ---------------------------------------------------------------------------
COLLINEAR_MERGE_TOL = 50.0       # Max distance to merge collinear lines
PARALLEL_LINE_TOL = 25.0         # Max offset to consider lines as parallel
CONTAINMENT_TOL = 100.0          # Distance for text-to-geometry association
POLYGON_CLOSE_TOL = 50.0         # Max gap to auto-close a polygon
MIN_BEAM_LENGTH = 200.0          # Minimum valid beam length
MIN_COLUMN_DIM = 100.0           # Minimum valid column dimension
MIN_SLAB_AREA = 10000.0          # Minimum slab area (100mm x 100mm)

# ---------------------------------------------------------------------------
# Confidence scoring weights
# ---------------------------------------------------------------------------
CONFIDENCE_LABEL_WEIGHT = 0.40   # Weight for label presence
CONFIDENCE_GEOM_WEIGHT = 0.35    # Weight for geometry quality
CONFIDENCE_LAYER_WEIGHT = 0.25   # Weight for layer name match

# Confidence thresholds
CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.70

# ---------------------------------------------------------------------------
# Default element parameters (mm)
# ---------------------------------------------------------------------------
DEFAULT_FLOOR_HEIGHT = 3200.0
DEFAULT_BEAM_WIDTH = 300.0
DEFAULT_BEAM_DEPTH = 450.0
DEFAULT_COLUMN_WIDTH = 600.0
DEFAULT_COLUMN_DEPTH = 600.0
DEFAULT_SLAB_THICKNESS = 150.0
DEFAULT_WALL_THICKNESS = 200.0

# ---------------------------------------------------------------------------
# CAD layer name patterns (case-insensitive regex)
# ---------------------------------------------------------------------------
BEAM_LAYER_PATTERNS = [
    r"(?i)beam",
    r"(?i)bm",
    r"(?i)s[-_]?beam",
    r"(?i)framing",
    r"(?i)str[-_]?beam",
]
COLUMN_LAYER_PATTERNS = [
    r"(?i)col(?:umn)?",
    r"(?i)s[-_]?col",
    r"(?i)str[-_]?col",
]
SLAB_LAYER_PATTERNS = [
    r"(?i)slab",
    r"(?i)floor",
    r"(?i)deck",
    r"(?i)s[-_]?slab",
]
WALL_LAYER_PATTERNS = [
    r"(?i)wall",
    r"(?i)shear",
    r"(?i)s[-_]?wall",
]

# ---------------------------------------------------------------------------
# Section label regex patterns
# ---------------------------------------------------------------------------
# Matches: "B 300x450", "300X600", "B300*450", "300×600", etc.
SECTION_REGEX = r"[BbCc]?\s*(\d{2,4})\s*[xX*×]\s*(\d{2,4})"
# Matches: "150 THK", "200THK", "150 mm thk", etc.
THICKNESS_REGEX = r"(\d{2,4})\s*(?:mm\s*)?[Tt][Hh][Kk]"
# Matches: "DIA 600", "Ø600", "D600", etc.
DIAMETER_REGEX = r"(?:[Dd][Ii][Aa]|[ØøDd])\s*(\d{2,4})"
