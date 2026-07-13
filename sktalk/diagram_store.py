"""In-memory diagram state: shapes, edges, and raw strokes."""

from dataclasses import dataclass
import itertools
import math

import config
import symbols


@dataclass
class Shape:
    id: str
    type: str  # "rectangle" | "line"
    bbox: tuple  # (x1, y1, x2, y2): corners for rectangle, endpoints for line
    label: str = ""
    centroid: tuple = (0, 0)
    created_t: float = 0.0  # time.monotonic() at creation, used for LABEL binding window
    icon_override: str = ""  # set by a composite gesture (e.g. line drawn inside -> "server")
    # True while this action/decision node is still waiting on its spoken
    # name (or open to a correction re-say) - closed by starting a new
    # stroke or an explicit key press, not just a timeout
    awaiting_name: bool = False


@dataclass
class Edge:
    from_id: str
    to_id: str
    # the connect stroke's shape (simplified to its major corners, snapped to
    # horizontal/vertical), used to route the connector along roughly the
    # same path the user actually drew; None for edges made via the
    # speech/'c' connect path, which has no drawn stroke to go on
    route_hint: tuple = None
    # an optional label drawn on the connector itself - used for a decision
    # node's outgoing branches ("yes"/"no"), typed on the keyboard the same
    # way an action/decision node's name is
    label: str = ""
    # reuses the exact attribute name a Shape uses while it waits on its typed
    # name, so the one typing_state flow in ui.py drives both without caring
    # whether its target is a shape or an edge
    awaiting_name: bool = False


class DiagramStore:
    """Holds shapes/edges plus raw (not-yet-recognized) strokes for phase 1 display."""

    def __init__(self):
        self.shapes: list[Shape] = []
        self.edges: list[Edge] = []
        self.raw_strokes: list[list[tuple]] = []
        self._id_counter = itertools.count(1)
        # shapes and edges in the order they were actually created, interleaved,
        # so undo can walk it back chronologically instead of always removing
        # edges before shapes regardless of which came later
        self._history: list = []

    def next_id(self):
        return f"s{next(self._id_counter)}"

    def add_raw_stroke(self, points):
        if points:
            self.raw_strokes.append(points)

    def add_shape(self, shape: Shape):
        self.shapes.append(shape)
        self._history.append(shape)

    def add_shape_from_stroke(self, points, shape_type, created_t):
        """Builds a beautified Shape from a recognized stroke and stores it."""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        if shape_type == "line":
            bbox = _fit_line_endpoints(points)
        else:
            bbox = (min(xs), min(ys), max(xs), max(ys))

        centroid = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        shape = Shape(
            id=self.next_id(),
            type=shape_type,
            bbox=bbox,
            centroid=centroid,
            created_t=created_t,
        )
        self.shapes.append(shape)
        self._history.append(shape)
        return shape

    def add_edge(self, edge: Edge):
        self.edges.append(edge)
        self._history.append(edge)
        return edge

    def remove_shape(self, shape):
        """Removes a shape and any edges attached to it, keeping undo history consistent."""
        if shape in self.shapes:
            self.shapes.remove(shape)
        dependent_edges = [e for e in self.edges if e.from_id == shape.id or e.to_id == shape.id]
        for edge in dependent_edges:
            self.edges.remove(edge)
        dropped = {id(shape)} | {id(e) for e in dependent_edges}
        self._history = [item for item in self._history if id(item) not in dropped]

    def most_recent_shape_within(self, window_s, now_t):
        """Latest-created shape still inside the LABEL binding window, or None."""
        candidates = [s for s in self.shapes if now_t - s.created_t <= window_s]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.created_t)

    def awaiting_name_shape(self, window_s, now_t):
        """The action/decision node still open for its (or a corrected) name,
        or None. window_s is just a safety cap in case it's never explicitly
        closed - the real gate is the awaiting_name flag."""
        candidates = [s for s in self.shapes
                      if s.awaiting_name and now_t - s.created_t <= window_s]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.created_t)

    def awaiting_name_edge(self):
        """The connector still open for its typed branch label (yes/no on a
        decision's outgoing arrow), or None. No time window - unlike a shape
        it isn't competing with speech labeling, so it just stays open until
        the user types something or presses Enter to skip."""
        return next((e for e in self.edges if e.awaiting_name), None)

    def close_awaiting_names(self):
        """Stops treating any shape's name or edge's label as still-open -
        called when a new stroke starts or the user explicitly confirms with
        a key press."""
        for shape in self.shapes:
            shape.awaiting_name = False
        for edge in self.edges:
            edge.awaiting_name = False

    def undo(self):
        """Removes whatever (shape or edge) was actually created most recently."""
        if not self._history:
            return None
        item = self._history.pop()
        if isinstance(item, Edge):
            if item in self.edges:
                self.edges.remove(item)
        elif item in self.shapes:
            self.shapes.remove(item)
        return item

    def clear(self):
        self.shapes.clear()
        self.edges.clear()
        self.raw_strokes.clear()
        self._history.clear()


ALIGN_PX = 30  # centers within this many px on one axis count as "aligned"


_TERMINAL_CIRCLE_SIDES = 24  # polygon side count approximating the start/final circle, plenty smooth for clipping


def _shape_vertices(shape):
    """The shape's actual drawn boundary as a polygon: a diamond for decision
    nodes (matching the diamond ui.py/renderer.py actually draw), a circle
    (approximated as a many-sided polygon, reusing the same polygon-clipping
    code) for start/final nodes since those render much smaller than their
    bbox, the cloud silhouette for cloud icons (letterboxed inside the bbox,
    so the empty margin must not count as part of the shape - otherwise a
    connect stroke ending in the gap still "hits" the cloud and both ends
    resolve to it), the plain bbox corners otherwise. Clipping/trimming
    against the full bbox instead of the shape's real drawn boundary lands
    connector endpoints in the empty gaps outside it."""
    x1, y1, x2, y2 = shape.bbox
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    icon = symbols.icon_for(shape)
    if icon == "decision":
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        return [(cx, y1), (x2, cy), (cx, y2), (x1, cy)]
    if icon in ("start", "final"):
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        r = min(x2 - x1, y2 - y1) / 2 * config.TERMINAL_NODE_SCALE
        return [
            (cx + r * math.cos(2 * math.pi * i / _TERMINAL_CIRCLE_SIDES),
             cy + r * math.sin(2 * math.pi * i / _TERMINAL_CIRCLE_SIDES))
            for i in range(_TERMINAL_CIRCLE_SIDES)
        ]
    if icon == "cloud":
        return [(px + x1, py + y1) for px, py in symbols.cloud_contour(x2 - x1, y2 - y1)]
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def distance_to_shape(shape, point):
    """0 if point is inside the shape's real drawn boundary, else the
    distance to its nearest edge - the diamond/circle for decision/
    start/final nodes, not their (often much larger) bounding rectangle.
    Two shapes placed close together no longer get confused with each
    other just because a start/final circle's bbox margin overlaps a
    neighbor, since only the visible boundary counts."""
    vertices = _shape_vertices(shape)
    if _point_in_polygon(vertices, point):
        return 0.0
    return math.dist(point, _nearest_boundary_point(vertices, point))


def edge_route(from_shape, to_shape, route_hint=None, offset=0.0):
    """Boundary-to-boundary connector path between two shapes.

    If route_hint has a real corner in it (the drawn connect stroke,
    simplified), follow that shape - just clip its two ends onto the shape
    borders instead of floating in the middle of them. Otherwise (no stroke
    to go on, e.g. the speech/'c' connect path, or a stroke straight enough
    that it simplified down to just its two endpoints) fall back to an
    auto-computed straight line or right-angle elbow between the centroids.

    offset shifts the auto-computed line sideways (perpendicular to the
    centroid-to-centroid direction) before clipping to the shape borders, so
    multiple edges between the same pair of shapes fan out instead of
    drawing exactly on top of each other; only applies to the fallback
    routing, not a hand-drawn route_hint."""
    cx1, cy1 = from_shape.centroid
    cx2, cy2 = to_shape.centroid

    if route_hint and len(route_hint) >= 2:
        # follow the drawn stroke (straight or elbow) and clip its ends onto
        # each shape's real boundary - works for horizontal, vertical, and
        # diagonal two-point connectors, not just L-shaped ones
        return _trim_to_shapes(list(route_hint), from_shape, to_shape)

    if offset:
        dx, dy = cx2 - cx1, cy2 - cy1
        length = math.hypot(dx, dy) or 1e-6
        perp_x, perp_y = -dy / length * offset, dx / length * offset
        cx1, cy1 = cx1 + perp_x, cy1 + perp_y
        cx2, cy2 = cx2 + perp_x, cy2 + perp_y

    p1 = _clip_to_polygon(_shape_vertices(from_shape), cx1, cy1, cx2, cy2)
    p2 = _clip_to_polygon(_shape_vertices(to_shape), cx2, cy2, cx1, cy1)

    if abs(p1[1] - p2[1]) < ALIGN_PX:
        # close enough to level - snap dead horizontal instead of leaving a
        # slightly slanted line from the raw clip points
        avg_y = (p1[1] + p2[1]) / 2
        return [(p1[0], avg_y), (p2[0], avg_y)]
    if abs(p1[0] - p2[0]) < ALIGN_PX:
        avg_x = (p1[0] + p2[0]) / 2
        return [(avg_x, p1[1]), (avg_x, p2[1])]

    # a clean straight boundary-to-boundary line. Both endpoints are clipped
    # along the same centroid-to-centroid ray, so the arrow meets each shape's
    # border head-on and stops exactly on it. A right-angle elbow was tried
    # here and looked crooked: its final segment hit the border at an angle
    # inconsistent with the clip point, leaving the arrowhead poking into the
    # shape at an odd spot. The drawn-path routing still applies when the user
    # deliberately draws a bent connector (handled above via route_hint).
    return [p1, p2]


def orthogonal_route(points):
    """Turns a raw hand-drawn connector stroke into a clean path that still
    follows the general shape the user drew.

    - Nearly horizontal/vertical: one straight segment.
    - Nearly straight diagonal: one straight segment (no forced elbow).
    - Clear L-bend: one right-angle elbow (draw.io-style).

    Following every simplified segment of the raw stroke kept the connector
    faithful but let a sloppy, wide hand movement render as a tangled
    multi-bend path. A diagonal stroke was also being forced into an L even
    when the user clearly drew straight across - that only happens now when
    the stroke visibly detours off the straight chord between its endpoints."""
    simplified = _rdp(points, config.ROUTE_SIMPLIFY_EPSILON)
    start, end = simplified[0], simplified[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]

    # nearly a straight run along one axis - hand it back as just the two
    # endpoints so edge_route draws (and axis-snaps) a single clean line
    # rather than inventing a needless corner
    if abs(dx) < ALIGN_PX or abs(dy) < ALIGN_PX:
        return [start, end]

    # nearly straight diagonal - the stroke stays close to the chord between
    # its endpoints, so keep one segment instead of forcing an elbow
    max_dev = max(_point_line_dist(p, start, end) for p in points)
    if max_dev <= config.ROUTE_SIMPLIFY_EPSILON:
        return [start, end]

    # deliberate L-bend: stroke clearly detours off the straight chord
    nxt = simplified[1]
    horizontal_first = abs(nxt[0] - start[0]) >= abs(nxt[1] - start[1])
    bend = (end[0], start[1]) if horizontal_first else (start[0], end[1])
    return [start, bend, end]


def _rdp(points, epsilon):
    if len(points) < 3:
        return list(points)
    start, end = points[0], points[-1]
    max_dist, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _point_line_dist(points[i], start, end)
        if d > max_dist:
            max_dist, index = d, i
    if max_dist > epsilon:
        left = _rdp(points[: index + 1], epsilon)
        right = _rdp(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]


def _point_line_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _nearest_boundary_point(vertices, point):
    """Closest point on the polygon's edge to `point`, whether point is
    inside or outside it - works the same way for a rectangle's 4 corners
    or a decision node's diamond vertices."""
    px, py = point
    best, best_dist = None, None
    n = len(vertices)
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            cand = (ax, ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            cand = (ax + t * dx, ay + t * dy)
        d = math.hypot(px - cand[0], py - cand[1])
        if best is None or d < best_dist:
            best, best_dist = cand, d
    return best


def _point_in_polygon(vertices, point):
    """Ray-casting point-in-polygon test - works for the rectangle or
    diamond vertex lists returned by _shape_vertices."""
    px, py = point
    inside = False
    n = len(vertices)
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        if (ay > py) != (by > py):
            x_at_y = ax + (py - ay) * (bx - ax) / (by - ay)
            if px < x_at_y:
                inside = not inside
    return inside


def _center_on_axis(a, b, centroid):
    """If segment a-b is purely vertical or horizontal (always true here -
    orthogonal_route guarantees every segment is axis-aligned), shift it
    sideways onto the shape's centroid on the perpendicular axis. Without
    this, the segment touching a shape sits wherever the hand happened to
    drift to, so the connector enters off-center and at an angle instead of
    landing dead-center like a real diagramming tool (drawio-style)."""
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if dx < 1 and dy >= 1:
        cx = centroid[0]
        return (cx, a[1]), (cx, b[1])
    if dy < 1 and dx >= 1:
        cy = centroid[1]
        return (a[0], cy), (b[0], cy)
    return a, b


def _trim_to_shapes(path, from_shape, to_shape):
    """Cuts the drawn path down to the segment that actually runs between
    the two shapes: drops any leading points still inside/on from_shape and
    any trailing points inside/on to_shape, replacing each trimmed end with
    the exact point the stroke crossed the shape's real boundary at (its
    diamond edge for a decision node, not the bounding rectangle). The
    segment touching each shape is also centered on that shape first (see
    _center_on_axis), so the crossing point lands dead-center rather than
    off to whichever side the raw stroke happened to drift toward."""
    path = list(path)
    path[0], path[1] = _center_on_axis(path[0], path[1], from_shape.centroid)
    path[-2], path[-1] = _center_on_axis(path[-2], path[-1], to_shape.centroid)

    from_vertices = _shape_vertices(from_shape)
    to_vertices = _shape_vertices(to_shape)

    start = 0
    while start < len(path) - 1 and _point_in_polygon(from_vertices, path[start]):
        start += 1
    if start > 0:
        # path[start-1] is the last point still inside/on the shape, ray
        # toward path[start] (first point past the border) finds the exit
        p1 = _boundary_touch_point(from_vertices, path[start - 1], path[start])
        path = [p1] + path[start:]
    else:
        # path[0] is already outside/on the border - just snap it in place
        p1 = _boundary_touch_point(from_vertices, path[0], path[1])
        path = [p1] + path[1:]

    # find the FIRST time the stroke actually reaches the target shape -
    # everything drawn after that (e.g. the hand drifting further while
    # releasing the pinch, possibly even back out an unrelated side) is
    # release noise, not a second intentional approach
    first_inside = None
    for i in range(1, len(path)):
        if _point_in_polygon(to_vertices, path[i]):
            first_inside = i
            break

    if first_inside is not None:
        p2 = _boundary_touch_point(to_vertices, path[first_inside], path[first_inside - 1])
        path = path[:first_inside] + [p2]
    else:
        # stroke never registered as inside (e.g. margin let a near-miss
        # count as the connect target) - just snap the raw endpoint
        p2 = _boundary_touch_point(to_vertices, path[-1], path[-2])
        path = path[:-1] + [p2]

    return path


def _boundary_touch_point(vertices, anchor_pt, aim_pt):
    """Where the ray from anchor_pt toward aim_pt actually crosses the
    polygon's border - this is the true entry/exit angle of the drawn
    stroke, not just whichever edge happens to be geometrically nearest to
    anchor_pt."""
    hit = _clip_to_polygon(vertices, anchor_pt[0], anchor_pt[1], aim_pt[0], aim_pt[1])
    if hit == (anchor_pt[0], anchor_pt[1]):
        return _nearest_boundary_point(vertices, anchor_pt)
    return hit


def _clip_to_polygon(vertices, cx, cy, ox, oy):
    """Point where the ray from (cx,cy) toward (ox,oy) exits the polygon -
    generic edge-list version so a rectangle's 4 corners and a decision
    node's diamond vertices are clipped against with the same math."""
    dx, dy = ox - cx, oy - cy
    if dx == 0 and dy == 0:
        return (cx, cy)

    best_t = None
    n = len(vertices)
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-9:
            continue
        t = ((ax - cx) * ey - (ay - cy) * ex) / denom
        u = ((ax - cx) * dy - (ay - cy) * dx) / denom
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
            if best_t is None or t < best_t:
                best_t = t

    if best_t is None:
        return (cx, cy)
    return (cx + best_t * dx, cy + best_t * dy)


def _fit_line_endpoints(points):
    """Least-squares straight line through the stroke, endpoints from the
    extreme projections onto it. Using just the raw first/last point is too
    sensitive to hand jitter right at pinch-start/release."""
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n

    sxx = sum((p[0] - cx) ** 2 for p in points)
    syy = sum((p[1] - cy) ** 2 for p in points)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in points)

    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    dx, dy = math.cos(angle), math.sin(angle)

    projections = [(p[0] - cx) * dx + (p[1] - cy) * dy for p in points]
    t_min, t_max = min(projections), max(projections)

    p1 = (cx + t_min * dx, cy + t_min * dy)
    p2 = (cx + t_max * dx, cy + t_max * dy)
    p1, p2 = _snap_to_axis(p1, p2)
    return (p1[0], p1[1], p2[0], p2[1])


AXIS_SNAP_DEG = 8  # snap to perfectly horizontal/vertical if within this many degrees


def _snap_to_axis(p1, p2):
    """If the fitted line is close to horizontal or vertical, make it exactly
    that instead of slightly crooked; a genuinely diagonal stroke is left as-is."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    angle = math.degrees(math.atan2(dy, dx)) % 180
    if angle > 90:
        angle -= 180

    if abs(angle) <= AXIS_SNAP_DEG:
        avg_y = (p1[1] + p2[1]) / 2
        return (p1[0], avg_y), (p2[0], avg_y)
    if abs(abs(angle) - 90) <= AXIS_SNAP_DEG:
        avg_x = (p1[0] + p2[0]) / 2
        return (avg_x, p1[1]), (avg_x, p2[1])
    return p1, p2
