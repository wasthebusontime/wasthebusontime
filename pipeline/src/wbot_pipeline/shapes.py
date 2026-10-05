"""Route lines for the stop map: shapes.txt simplified for display.

Douglas-Peucker keeps a subset of the original points (none is moved), dropping those
within TOLERANCE_M of the line through their neighbours.
"""

import math

TOLERANCE_M = 4.0
EARTH_M = 6_371_000


def _xy(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """(lat, lon) -> local metres (equirectangular; fine at city scale)."""
    lat0 = math.radians(sum(p[0] for p in points) / len(points))
    return [(math.radians(lon) * math.cos(lat0) * EARTH_M, math.radians(lat) * EARTH_M) for lat, lon in points]


def _distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def simplify(points: list[tuple[float, float]], tolerance_m: float = TOLERANCE_M) -> list[tuple[float, float]]:
    if len(points) < 3:
        return list(points)
    xy = _xy(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, index = 0.0, None
        for k in range(i + 1, j):
            d = _distance(xy[k], xy[i], xy[j])
            if d > best:
                best, index = d, k
        if index is not None and best > tolerance_m:
            keep[index] = True
            stack += [(i, index), (index, j)]
    return [p for p, k in zip(points, keep) if k]
