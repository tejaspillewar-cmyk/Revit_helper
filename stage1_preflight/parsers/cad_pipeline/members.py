"""
Axis + interval member model.

A structural member is stored as an infinite axis line (angle, perpendicular
offset, thickness) plus an interval [s0, s1] of positions *along* that axis.
p1/p2 are derived, never stored, so a member cannot rotate: connectivity may
only slide an end along the axis (change s0/s1) or shift a whole cluster
sideways (change offset).

Frame: d = (cos a, sin a) is the axis direction, n = (-sin a, cos a) the
normal.  A point is  P = s*d + offset*n,  so  s = P.d  and  offset = P.n.
The angle is kept in [0, 180).
"""
import math
from dataclasses import dataclass, field

PARALLEL_TOL_DEG = 0.5
DOMINANT_SNAP_DEG = 2.5


def norm_angle(a: float) -> float:
    """Fold to [0, 180)."""
    a = a % 180.0
    return 0.0 if abs(a - 180.0) < 1e-9 else a


def angle_diff(a: float, b: float) -> float:
    """Smallest difference between two undirected angles (0..90)."""
    d = abs(norm_angle(a) - norm_angle(b))
    return min(d, 180.0 - d)


@dataclass
class Axis:
    angle_deg: float
    offset: float
    thickness: float

    @property
    def d(self):
        r = math.radians(self.angle_deg)
        return (math.cos(r), math.sin(r))

    @property
    def n(self):
        r = math.radians(self.angle_deg)
        return (-math.sin(r), math.cos(r))

    def s_of(self, pt) -> float:
        d = self.d
        return pt[0] * d[0] + pt[1] * d[1]

    def off_of(self, pt) -> float:
        n = self.n
        return pt[0] * n[0] + pt[1] * n[1]

    def point(self, s: float):
        d, n = self.d, self.n
        return (s * d[0] + self.offset * n[0], s * d[1] + self.offset * n[1])

    @classmethod
    def from_points(cls, p1, p2, thickness: float):
        """Axis through p1->p2 and the (s0, s1) interval of those points on it."""
        ang = norm_angle(math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])))
        ax = cls(ang, 0.0, thickness)
        ax.offset = (ax.off_of(p1) + ax.off_of(p2)) / 2.0
        return ax, ax.s_of(p1), ax.s_of(p2)


def intersect_axes(a: Axis, b: Axis):
    """Exact intersection point of two axes, or None if (near) parallel."""
    if angle_diff(a.angle_deg, b.angle_deg) < PARALLEL_TOL_DEG:
        return None
    da, na = a.d, a.n
    db, nb = b.d, b.n
    # solve  s*da + a.off*na = t*db + b.off*nb  for s (position on a)
    # dot with nb: s*(da.nb) + a.off*(na.nb) = b.off
    den = da[0] * nb[0] + da[1] * nb[1]
    if abs(den) < 1e-9:
        return None
    nn = na[0] * nb[0] + na[1] * nb[1]
    s = (b.offset - a.offset * nn) / den
    return a.point(s)


@dataclass
class Member:
    """kind: 'wall' | 'beam'.  s0 <= s1 always."""
    kind: str
    axis: Axis
    s0: float
    s1: float
    ref: object = None            # the WallEntry / BeamEntry this came from
    group_id: str = ""            # pieces of one composite shape share this
    source_ids: list = field(default_factory=list)   # DXF handles consumed
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.s0 > self.s1:
            self.s0, self.s1 = self.s1, self.s0

    @property
    def p1(self):
        return self.axis.point(self.s0)

    @property
    def p2(self):
        return self.axis.point(self.s1)

    @property
    def length(self) -> float:
        return self.s1 - self.s0

    @property
    def angle_deg(self) -> float:
        return self.axis.angle_deg

    @property
    def thickness(self) -> float:
        return self.axis.thickness

    @property
    def centroid(self):
        return self.axis.point((self.s0 + self.s1) / 2.0)

    def rect_corners(self):
        """Footprint rectangle (flat caps) as 4 corners."""
        d, n = self.axis.d, self.axis.n
        h = self.axis.thickness / 2.0
        a, b = self.p1, self.p2
        return [(a[0] - n[0] * h, a[1] - n[1] * h), (b[0] - n[0] * h, b[1] - n[1] * h),
                (b[0] + n[0] * h, b[1] + n[1] * h), (a[0] + n[0] * h, a[1] + n[1] * h)]


def member_from_points(kind, p1, p2, thickness, ref=None, group_id="", source_ids=None, snap_angles=None):
    """Build a Member from two endpoints, optionally snapping its angle to a dominant one."""
    ax, s0, s1 = Axis.from_points(p1, p2, thickness)
    if snap_angles:
        ax, s0, s1 = snap_axis(ax, s0, s1, snap_angles, p1, p2)
    return Member(kind, ax, s0, s1, ref=ref, group_id=group_id, source_ids=list(source_ids or []))


def snap_axis(ax: Axis, s0: float, s1: float, dominant, p1, p2):
    """Rotate the axis (about the interval midpoint) onto the nearest dominant angle within tolerance."""
    best = None
    for da in dominant:
        dd = angle_diff(ax.angle_deg, da)
        if dd <= DOMINANT_SNAP_DEG and (best is None or dd < best[0]):
            best = (dd, da)
    if best is None or best[0] < 1e-9:
        return ax, s0, s1
    mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    length = s1 - s0
    new = Axis(norm_angle(best[1]), 0.0, ax.thickness)
    new.offset = new.off_of(mid)
    sm = new.s_of(mid)
    return new, sm - length / 2.0, sm + length / 2.0


# ── orientation frame ────────────────────────────────────────────────────

def dominant_angles(segments, bin_deg: float = 0.5, min_share: float = 0.04):
    """
    Length-weighted angle histogram (mod 180) over (p1, p2) segments.
    Returns the list of peak angles (degrees), strongest first.
    """
    if not segments:
        return []
    nb = int(round(180.0 / bin_deg))
    hist = [0.0] * nb
    vec = [[0.0, 0.0] for _ in range(nb)]     # length-weighted (cos 2a, sin 2a) per bin
    total = 0.0
    for p1, p2 in segments:
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if L < 1e-6:
            continue
        a = norm_angle(math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])))
        b = int(a / bin_deg) % nb
        hist[b] += L
        vec[b][0] += L * math.cos(math.radians(2 * a))
        vec[b][1] += L * math.sin(math.radians(2 * a))
        total += L
    if total <= 0:
        return []
    # smooth over +/- DOMINANT_SNAP_DEG so one peak is one direction
    w = max(1, int(round(DOMINANT_SNAP_DEG / bin_deg)))
    smooth = [sum(hist[(i + k) % nb] for k in range(-w, w + 1)) for i in range(nb)]
    peaks = []
    for i in range(nb):
        v = smooth[i]
        if v < min_share * total:
            continue
        if all(v >= smooth[(i + k) % nb] for k in range(-w, w + 1)):
            # exact weighted mean angle inside the window (doubled-angle vectors; wrap-safe)
            cx = sum(vec[(i + k) % nb][0] for k in range(-w, w + 1))
            cy = sum(vec[(i + k) % nb][1] for k in range(-w, w + 1))
            ang = math.degrees(math.atan2(cy, cx)) / 2.0 if (cx or cy) else (i + 0.5) * bin_deg
            peaks.append((v, norm_angle(ang)))
    peaks.sort(reverse=True)
    out = []
    for _, a in peaks:
        if all(angle_diff(a, b) > DOMINANT_SNAP_DEG for b in out):
            out.append(a)
    return out
