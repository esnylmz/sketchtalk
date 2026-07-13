"""$1 Unistroke Recognizer (Wobbrock et al. 2007), 2 templates: rectangle, line."""

import math

NUM_RESAMPLE_POINTS = 64
SQUARE_SIZE = 250.0
ANGLE_RANGE = math.radians(45.0)
ANGLE_PRECISION = math.radians(2.0)
PHI = 0.5 * (-1.0 + math.sqrt(5.0))
HALF_DIAGONAL = 0.5 * math.hypot(SQUARE_SIZE, SQUARE_SIZE)


def _path_length(points):
    return sum(math.dist(points[i - 1], points[i]) for i in range(1, len(points)))


def _resample(points, n=NUM_RESAMPLE_POINTS):
    interval = _path_length(points) / (n - 1)
    if interval <= 1e-9:
        return [points[0]] * n

    pts = list(points)
    new_points = [pts[0]]
    d = 0.0
    i = 1
    while i < len(pts):
        p1, p2 = pts[i - 1], pts[i]
        seg = math.dist(p1, p2)
        if d + seg >= interval:
            t = (interval - d) / seg if seg > 1e-9 else 0.0
            q = (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
            new_points.append(q)
            pts.insert(i, q)
            d = 0.0
        else:
            d += seg
        i += 1

    while len(new_points) < n:
        new_points.append(pts[-1])
    return new_points[:n]


def _centroid(points):
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _indicative_angle(points):
    cx, cy = _centroid(points)
    return math.atan2(points[0][1] - cy, points[0][0] - cx)


def _rotate_by(points, angle):
    cx, cy = _centroid(points)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append((dx * cos_a - dy * sin_a + cx, dx * sin_a + dy * cos_a + cy))
    return out


def _scale_to_square(points, size=SQUARE_SIZE):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w = max(max(xs) - min(xs), 1e-9)
    h = max(max(ys) - min(ys), 1e-9)
    return [(x * size / w, y * size / h) for x, y in points]


def _translate_to_origin(points):
    cx, cy = _centroid(points)
    return [(x - cx, y - cy) for x, y in points]


def _normalize(points):
    pts = _resample(points)
    pts = _rotate_by(pts, -_indicative_angle(pts))
    pts = _scale_to_square(pts)
    pts = _translate_to_origin(pts)
    return pts


def _path_distance(a, b):
    return sum(math.dist(p1, p2) for p1, p2 in zip(a, b)) / len(a)


def _distance_at_best_angle(points, template, a=-ANGLE_RANGE, b=ANGLE_RANGE, threshold=ANGLE_PRECISION):
    x1 = PHI * a + (1 - PHI) * b
    f1 = _path_distance(_rotate_by(points, x1), template)
    x2 = (1 - PHI) * a + PHI * b
    f2 = _path_distance(_rotate_by(points, x2), template)
    while abs(b - a) > threshold:
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = PHI * a + (1 - PHI) * b
            f1 = _path_distance(_rotate_by(points, x1), template)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = (1 - PHI) * a + PHI * b
            f2 = _path_distance(_rotate_by(points, x2), template)
    return min(f1, f2)


# raw templates before normalization, unit-ish scale is irrelevant (scale_to_square handles it)
_RECT_RAW = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]  # closed loop, traced clockwise
_LINE_RAW = [(0, 0), (1, 1)]  # open diagonal stroke

TEMPLATES = {
    "rectangle": _normalize(_RECT_RAW),
    "line": _normalize(_LINE_RAW),
}

# closed vs open strokes get mixed up by raw template scoring alone (a rectangle
# corner drawn in the air rarely closes perfectly, and $1 will happily call it a
# line), so gate on start/end distance first like a normal closed-shape check
CLOSURE_RATIO_THRESHOLD = 0.35  # start-end dist / bbox diagonal, below this = closed
CLOSURE_ENDPOINT_SAMPLES = 3  # average this many points at each end, single-frame jitter (worse near frame corners) can make an intentionally closed loop look open


def _closure_endpoint(points):
    n = min(CLOSURE_ENDPOINT_SAMPLES, len(points))
    return sum(p[0] for p in points[:n]) / n, sum(p[1] for p in points[:n]) / n


def recognize(points):
    """Returns (shape_type, score in [0,1])."""
    if len(points) < 2:
        return None, 0.0

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) or 1e-6
    start = _closure_endpoint(points)
    end = _closure_endpoint(list(reversed(points)))
    closure_ratio = math.dist(start, end) / diagonal

    shape_type = "rectangle" if closure_ratio <= CLOSURE_RATIO_THRESHOLD else "line"

    candidate = _normalize(points)
    dist = _distance_at_best_angle(candidate, TEMPLATES[shape_type])
    score = 1.0 - dist / HALF_DIAGONAL
    return shape_type, score
