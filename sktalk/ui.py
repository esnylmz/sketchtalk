"""OpenCV main loop: webcam feed, fingertip overlay, pinch-to-draw, shapes,
speech commands, and deictic fusion (delete/label)."""

import math
import time
import winsound

import cv2
import numpy as np
from better_profanity import profanity

import command_parser
import config
import fusion
import renderer
import shape_recognizer
import symbols
from hand_tracker import HandTracker
from stroke_buffer import StrokeBuffer
from diagram_store import DiagramStore, Edge, edge_route, orthogonal_route, distance_to_shape
from session_logger import SessionLogger
from speech_engine import SpeechEngine

FINGER_COLOR = (0, 255, 0)
PINCH_COLOR = (0, 0, 255)
TRAIL_COLOR = (255, 200, 0)
SHAPE_COLOR = (0, 200, 0)
EDGE_COLOR = (200, 200, 0)
LABEL_COLOR = (255, 255, 255)
HIGHLIGHT_COLOR = (0, 255, 255)
CLARIFY_COLOR = (255, 0, 255)
DELETE_FLASH_COLOR = (0, 0, 255)

HIGHLIGHT_DURATION_S = 1.5
DELETE_FLASH_DURATION_S = 0.5

# mis-hearings of "stop" that command_parser.py deliberately doesn't treat
# as pause on their own (they overlap with a real word/meaning elsewhere) -
# only counted here, gated on the stop-pose gesture actually showing
_GESTURE_GATED_STOP_PHRASES = {"wow", "that stuff", "its now"}
EXPORT_FLASH_DURATION_S = 1.5


def _to_px(norm_x, norm_y, w, h):
    # landmarks come from the already-flipped frame, coords already match it
    return int(norm_x * w), int(norm_y * h)


def _beep(freq=880, duration_ms=80):
    winsound.Beep(freq, duration_ms)


def _draw_actor(frame, bbox, color, thickness):
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx = x1 + w // 2
    head_r = max(int(min(w, h) * 0.18), 6)
    head_cy = y1 + head_r + 2
    cv2.circle(frame, (cx, head_cy), head_r, color, thickness)
    body_top = head_cy + head_r
    body_bottom = y1 + int(h * 0.68)
    cv2.line(frame, (cx, body_top), (cx, body_bottom), color, thickness)
    arm_y = body_top + int((body_bottom - body_top) * 0.3)
    cv2.line(frame, (x1 + int(w * 0.15), arm_y), (x2 - int(w * 0.15), arm_y), color, thickness)
    cv2.line(frame, (cx, body_bottom), (x1 + int(w * 0.2), y2), color, thickness)
    cv2.line(frame, (cx, body_bottom), (x2 - int(w * 0.2), y2), color, thickness)


def _draw_database(frame, bbox, color, thickness):
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx = x1 + w // 2
    ry = max(int(h * 0.12), 4)
    cv2.ellipse(frame, (cx, y1 + ry), (max(w // 2, 1), ry), 0, 0, 360, color, thickness)
    cv2.line(frame, (x1, y1 + ry), (x1, y2 - ry), color, thickness)
    cv2.line(frame, (x2, y1 + ry), (x2, y2 - ry), color, thickness)
    cv2.ellipse(frame, (cx, y2 - ry), (max(w // 2, 1), ry), 0, 0, 180, color, thickness)


def _draw_server(frame, bbox, color, thickness):
    x1, y1, x2, y2 = bbox
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
    h = y2 - y1
    for i in (1, 2):
        ly = y1 + h * i // 3
        cv2.line(frame, (x1, ly), (x2, ly), color, max(thickness - 1, 1))


def _draw_action(frame, bbox, color, thickness):
    """UML activity diagram action node: a rectangle with rounded corners."""
    x1, y1, x2, y2 = bbox
    r = max(min(x2 - x1, y2 - y1) // 5, 8)
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    cv2.line(frame, (x1 + r, y1), (x2 - r, y1), color, thickness)
    cv2.line(frame, (x1 + r, y2), (x2 - r, y2), color, thickness)
    cv2.line(frame, (x1, y1 + r), (x1, y2 - r), color, thickness)
    cv2.line(frame, (x2, y1 + r), (x2, y2 - r), color, thickness)
    cv2.ellipse(frame, (x1 + r, y1 + r), (r, r), 180, 0, 90, color, thickness)
    cv2.ellipse(frame, (x2 - r, y1 + r), (r, r), 270, 0, 90, color, thickness)
    cv2.ellipse(frame, (x2 - r, y2 - r), (r, r), 0, 0, 90, color, thickness)
    cv2.ellipse(frame, (x1 + r, y2 - r), (r, r), 90, 0, 90, color, thickness)


def _draw_decision(frame, bbox, color, thickness):
    """Standard flowchart decision node: a diamond inscribed in the bbox."""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    pts = np.array([[cx, y1], [x2, cy], [cx, y2], [x1, cy]], dtype=np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=thickness)



def _draw_start(frame, bbox, color, thickness):
    """UML activity diagram initial node: a solid filled circle."""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    r = int(min(x2 - x1, y2 - y1) / 2 * config.TERMINAL_NODE_SCALE)
    cv2.circle(frame, (cx, cy), r, color, -1)


def _draw_final(frame, bbox, color, thickness):
    """UML activity diagram final node: a ring with a filled circle inside."""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    r = int(min(x2 - x1, y2 - y1) / 2 * config.TERMINAL_NODE_SCALE)
    cv2.circle(frame, (cx, cy), r, color, thickness)
    cv2.circle(frame, (cx, cy), max(r - thickness - 4, 3), color, -1)


def _draw_cloud(frame, bbox, color, thickness):
    x1, y1, x2, y2 = bbox
    contour = symbols.cloud_contour(x2 - x1, y2 - y1)
    pts = np.array([[px + x1, py + y1] for px, py in contour], dtype=np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=thickness)


def _draw_shape(frame, shape, highlighted):
    icon = symbols.icon_for(shape)
    color = HIGHLIGHT_COLOR if highlighted else SHAPE_COLOR
    thickness = 3 if highlighted else 2

    if icon == "line":
        p1 = (int(shape.bbox[0]), int(shape.bbox[1]))
        p2 = (int(shape.bbox[2]), int(shape.bbox[3]))
        cv2.line(frame, p1, p2, color, thickness)
        label_pos = (min(p1[0], p2[0]), max(p1[1], p2[1]) + 18)
    else:
        x1, x2 = sorted((int(shape.bbox[0]), int(shape.bbox[2])))
        y1, y2 = sorted((int(shape.bbox[1]), int(shape.bbox[3])))
        bbox = (x1, y1, x2, y2)
        if icon == "actor":
            _draw_actor(frame, bbox, color, thickness)
        elif icon == "database":
            _draw_database(frame, bbox, color, thickness)
        elif icon == "server":
            _draw_server(frame, bbox, color, thickness)
        elif icon == "cloud":
            _draw_cloud(frame, bbox, color, thickness)
        elif icon == "action":
            _draw_action(frame, bbox, color, thickness)
        elif icon == "decision":
            _draw_decision(frame, bbox, color, thickness)
        elif icon == "start":
            _draw_start(frame, bbox, color, thickness)
        elif icon == "final":
            _draw_final(frame, bbox, color, thickness)
        else:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        if icon in ("action", "decision"):
            # action/decision text sits inside the shape, like a real UML
            # activity diagram, instead of floating below it
            text_w, text_h = cv2.getTextSize(shape.label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0]
            label_pos = (x1 + max((x2 - x1 - text_w) // 2, 4), y1 + (y2 - y1 + text_h) // 2)
        elif icon in ("start", "final"):
            # the visible circle is drawn smaller than the drawn bbox (see
            # config.TERMINAL_NODE_SCALE) - anchor the label just under the circle
            # itself, not the much larger original bbox, or it reads as
            # floating far below the shape
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            r = int(min(x2 - x1, y2 - y1) / 2 * config.TERMINAL_NODE_SCALE)
            text_w = cv2.getTextSize(shape.label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0][0]
            label_pos = (cx - text_w // 2, cy + r + 18)
        else:
            label_pos = (x1, y2 + 18)

    if shape.label:
        cv2.putText(frame, shape.label, label_pos, cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, LABEL_COLOR, 1, cv2.LINE_AA)


ARROW_HEAD_PX = 16  # fixed on-screen arrowhead length, so it looks the same on a short or long connector instead of scaling with the segment


def _edge_label_anchor(points):
    """Point halfway along the connector by arc length - the natural spot to
    drop a branch label so it sits on the arrow rather than at one end."""
    seg_lens = [math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
                for i in range(len(points) - 1)]
    half = sum(seg_lens) / 2
    acc = 0.0
    for i, seg in enumerate(seg_lens):
        if seg and acc + seg >= half:
            t = (half - acc) / seg
            return (int(points[i][0] + (points[i + 1][0] - points[i][0]) * t),
                    int(points[i][1] + (points[i + 1][1] - points[i][1]) * t))
        acc += seg
    return points[len(points) // 2]


def _draw_edge(frame, edge, store, offset=0.0):
    from_shape = next((s for s in store.shapes if s.id == edge.from_id), None)
    to_shape = next((s for s in store.shapes if s.id == edge.to_id), None)
    if from_shape is None or to_shape is None:
        return
    route = edge_route(from_shape, to_shape, edge.route_hint, offset=offset)
    points = [tuple(int(v) for v in p) for p in route]
    for i in range(len(points) - 2):
        cv2.line(frame, points[i], points[i + 1], EDGE_COLOR, 2)
    # arrowhead as a fixed pixel length rather than a fraction of the last
    # segment, so a long straight connector doesn't get a giant arrowhead
    seg_len = math.hypot(points[-1][0] - points[-2][0], points[-1][1] - points[-2][1])
    tip_frac = min(0.4, ARROW_HEAD_PX / seg_len) if seg_len > 1 else 0.4
    cv2.arrowedLine(frame, points[-2], points[-1], EDGE_COLOR, 2, tipLength=tip_frac)

    if edge.label:
        ax, ay = _edge_label_anchor(points)
        text_w = cv2.getTextSize(edge.label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
        # nudge off the line so the arrow stays readable underneath it
        cv2.putText(frame, edge.label, (ax - text_w // 2, ay - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, LABEL_COLOR, 1, cv2.LINE_AA)


def _draw_edges(frame, store):
    """Draws every edge, fanning out edges that share the same pair of
    shapes (regardless of direction) so they don't sit exactly on top of
    each other."""
    groups = {}
    for edge in store.edges:
        key = tuple(sorted((edge.from_id, edge.to_id)))
        groups.setdefault(key, []).append(edge)

    for edge in store.edges:
        key = tuple(sorted((edge.from_id, edge.to_id)))
        siblings = groups[key]
        count = len(siblings)
        index = siblings.index(edge)
        offset = (index - (count - 1) / 2) * config.EDGE_PARALLEL_OFFSET_PX
        _draw_edge(frame, edge, store, offset=offset)


def _draw_clarification(frame, candidates):
    for i, shape in enumerate(candidates):
        cx, cy = (int(v) for v in shape.centroid)
        cv2.circle(frame, (cx, cy), 22, CLARIFY_COLOR, 3)
        cv2.putText(frame, str(i + 1), (cx - 8, cy + 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, CLARIFY_COLOR, 2, cv2.LINE_AA)
    cv2.putText(frame, "which one? press 1/2", (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, CLARIFY_COLOR, 2, cv2.LINE_AA)


def _draw_delete_flash(frame, bbox):
    x1, y1, x2, y2 = (int(v) for v in bbox)
    cv2.rectangle(frame, (x1, y1), (x2, y2), DELETE_FLASH_COLOR, 3)
    cv2.line(frame, (x1, y1), (x2, y2), DELETE_FLASH_COLOR, 3)
    cv2.line(frame, (x1, y2), (x2, y1), DELETE_FLASH_COLOR, 3)


def _draw_center_card(frame, lines, prompt):
    """Centered translucent card with a list of lines and a highlighted
    prompt at the bottom - shared layout for the startup intro and the
    pause card, both of which wait for Enter rather than a timer. Each line
    is either a plain string (normal body text) or a ("highlight", text)
    tuple rendered bigger and in the accent color, for the two commands
    worth calling out on sight (delete, stop)."""
    row_h = 34
    h, w = frame.shape[:2]
    box_w = 680
    box_h = row_h * len(lines) + 70
    x1, y1 = (w - box_w) // 2, (h - box_h) // 2
    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x1 + box_w, y1 + box_h), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
    y = y1 + row_h
    for line in lines:
        kind, text = line if isinstance(line, tuple) else ("body", line)
        scale = 0.7 if kind == "highlight" else 0.55
        color = HIGHLIGHT_COLOR if kind == "highlight" else (255, 255, 255)
        thickness = 2 if kind == "highlight" else 1
        text_w = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0]
        cv2.putText(frame, text, (x1 + (box_w - text_w) // 2, y), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, thickness, cv2.LINE_AA)
        y += row_h

    y += 12
    text_w = cv2.getTextSize(prompt, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0][0]
    cv2.putText(frame, prompt, (x1 + (box_w - text_w) // 2, y), cv2.FONT_HERSHEY_SIMPLEX,
                0.6, HIGHLIGHT_COLOR, 2, cv2.LINE_AA)


def _draw_startup_hint(frame):
    """Centered intro card summarizing the actual draw -> recognize -> label
    flow, shown until the user presses Enter - a new user isn't left
    guessing what a beep or a shape snapping into a box means, and dismisses
    it deliberately instead of it vanishing on a timer mid-read. Each named
    action (delete, connect, stop, export) gets its own highlighted line
    followed by how to do it, instead of being buried in a paragraph."""
    _draw_center_card(frame, [
        "Pinch thumb + index, trace a closed loop (circle or box)",
        "It snaps into a clean box - listen for the beep, watch it turn green",
        "Say what it is to label it: database, cloud, server, user, action, decision...",
        ("highlight", "\"START\" / \"FINAL\""),
        "Say either to drop a UML initial / final node (no name needed)",
        ("highlight", "\"DELETE THIS\""),
        "Point at a shape (no pinch needed) and say it to remove it",
        ("highlight", "\"CONNECT\""),
        "Pinch on one icon, drag the line into another -> arrow",
        "An arrow out of a decision: type its yes/no, or Enter to skip",
        ("highlight", "\"STOP\""),
        "Open palm toward the camera + say it together to pause anytime",
        ("highlight", "\"EXPORT\""),
        "Press e or say it anytime to save the diagram as SVG for your records",
        "z undo   q quit",
    ], "Press Enter to start")


def _draw_pause_card(frame):
    """Shown while paused (hold an open palm + say "stop") - speech and
    drawing are frozen until Enter is pressed, e.g. to take a question
    mid-presentation without quitting the app."""
    _draw_center_card(frame, [
        "Paused - speech and drawing are frozen",
        "Hold an open palm toward the camera and say \"stop\" to pause anytime",
    ], "Press Enter to resume")


_HELP_BADGE_COLOR = (200, 200, 200)
# fixed corner rect for the clickable help badge - frame size never changes
# at runtime (config.FRAME_W/H), so this can be computed once
HELP_BADGE_RECT = (config.FRAME_W - 70, 10, config.FRAME_W - 10, 40)


def _help_badge_hit(x, y):
    x1, y1, x2, y2 = HELP_BADGE_RECT
    return x1 <= x <= x2 and y1 <= y <= y2


def _draw_help_badge(frame):
    """Small always-on corner badge, clickable with the mouse, instead of a
    permanent legend eating screen space."""
    x1, y1, x2, y2 = HELP_BADGE_RECT
    cv2.rectangle(frame, (x1, y1), (x2, y2), _HELP_BADGE_COLOR, 1)
    cv2.putText(frame, "? help", (x1 + 4, y2 - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, _HELP_BADGE_COLOR, 1, cv2.LINE_AA)


_HELP_SECTIONS = [
    ("Draw & Label", [
        "Pinch + trace a closed loop -> snaps into a box (beep)",
        "Say: database / cloud / server / user / action / decision / ...",
        "Line drawn inside a box -> turns it into a server icon",
        "Say \"start\" / \"final\" for a UML initial/final node (no name needed)",
    ]),
    ("Delete", [
        "Point at a shape (no pinch needed) + say \"delete this\"",
    ]),
    ("Connect", [
        "Pinch on one icon, drag the line into another -> arrow",
        "Arrow out of a decision: type its yes/no label, or Enter to skip",
    ]),
    ("Pause", [
        "Open palm toward the camera + say \"stop\" -> freezes everything",
        "Press Enter to resume where you left off",
    ]),
    ("Keys", [
        "z undo   e export   q quit",
        "enter / esc  confirm / cancel a typed name",
    ]),
]

_HELP_ROW_H = {"title": 30, "header": 26, "body": 22}


def _draw_help_panel(frame):
    """Full legend, shown only while help is toggled on (click the badge) -
    grouped into labeled sections with a header rule, instead of one flat
    list of lines, so it reads like a reference card rather than a dump."""
    rows = [("title", "Help  (click the badge to close)")]
    for title, body_lines in _HELP_SECTIONS:
        rows.append(("header", title))
        for line in body_lines:
            rows.append(("body", line))

    top_pad, bottom_pad = 16, 14
    box_h = top_pad + bottom_pad + sum(_HELP_ROW_H[kind] for kind, _ in rows)
    box_w = 460

    h, w = frame.shape[:2]
    x1, y1 = w - box_w - 10, 10
    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x1 + box_w, y1 + box_h), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
    cv2.rectangle(frame, (x1, y1), (x1 + box_w, y1 + box_h), (100, 100, 100), 1)

    y = y1 + top_pad
    for kind, text in rows:
        y += _HELP_ROW_H[kind]
        if kind == "title":
            cv2.putText(frame, text, (x1 + 14, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, HIGHLIGHT_COLOR, 1, cv2.LINE_AA)
            cv2.line(frame, (x1 + 12, y + 8), (x1 + box_w - 12, y + 8), (100, 100, 100), 1)
        elif kind == "header":
            cv2.putText(frame, text, (x1 + 14, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, HIGHLIGHT_COLOR, 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, text, (x1 + 28, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1, cv2.LINE_AA)


_DEFAULT_STATUS_TEXT = ("pinch to draw, q to quit, e export, z undo, "
                         "enter/esc to confirm/cancel typed name, click ? for help")


def _status_text(store, typing_state, now_t):
    """Status-bar text, chosen from what the user is expected to do next
    rather than always showing the full command reference - a first-time
    user gets pointed at the one relevant action, an experienced one still
    has the full reminder once nothing more specific applies."""
    if typing_state["active"]:
        return None  # the "type name: _" prompt below already covers this
    if not store.shapes:
        return "pinch thumb and index, trace a closed loop - it snaps into a box"
    recent_unlabeled = next(
        (s for s in store.shapes
         if not s.label and not s.icon_override and not s.awaiting_name
         and now_t - s.created_t <= config.NEW_SHAPE_HINT_WINDOW_S),
        None,
    )
    if recent_unlabeled is not None:
        return "say what it is (e.g. \"database\", \"cloud\") or draw a line inside it for a server icon"
    return _DEFAULT_STATUS_TEXT


def _rectangle_containing(store, point, margin=0):
    """Rectangle whose real drawn boundary (within margin) contains this
    point, or None. Uses the shape's actual rendered polygon - the diamond
    for a decision node, the small circle for start/final, the box
    otherwise - so detection matches what the user actually sees and aims
    at, and a start/final node's much larger invisible bbox can't steal a
    point that's really closest to a neighbouring shape. Picks whichever
    candidate's boundary is closest to the point rather than the most
    recently drawn one, since two shapes placed close together can both end
    up "containing" the same point once a margin is added."""
    best, best_dist = None, None
    for shape in store.shapes:
        if shape.type != "rectangle":
            continue
        dist = distance_to_shape(shape, point)
        if dist > margin:
            continue
        if best is None or dist < best_dist:
            best, best_dist = shape, dist
    return best


# how far in from a connect stroke's end (as a fraction of its points) to keep
# scanning for the shape it targets - covers the endpoint drifting off the
# shape while the pinch releases over a few debounced frames, without reaching
# so far in that it grabs a shape the stroke merely passed near mid-route
_CONNECT_SCAN_FRAC = 0.35


def _connect_endpoint_shape(store, points, from_start):
    """Which shape a connect stroke is anchored to at one end. The raw first/
    last point often lands just off the shape - the pinch takes a few frames
    to register as released, and the hand drifts into the empty gap meanwhile
    (sometimes all the way back toward the other shape), so points keep getting
    recorded past the moment it actually touched. Instead of trusting that one
    drifted endpoint, look across the scan window near this end and take the
    shape at the point of *closest approach* - the deepest the stroke actually
    reached toward a shape - rather than the first point that happens to be
    near one (which, on an overshooting drift, can be the wrong shape). The
    window is capped so a stroke routed close past a third shape mid-route
    can't be picked as an anchor."""
    n = len(points)
    scan = max(1, int(n * _CONNECT_SCAN_FRAC))
    indices = range(scan) if from_start else range(n - 1, n - 1 - scan, -1)
    best, best_dist = None, None
    for i in indices:
        for shape in store.shapes:
            if shape.type != "rectangle":
                continue
            dist = distance_to_shape(shape, points[i])
            if dist > config.CONNECT_ENDPOINT_MARGIN:
                continue
            if best is None or dist < best_dist:
                best, best_dist = shape, dist
    return best


def _finish_stroke(finished, store, logger, now_t):
    if not finished:
        return

    # gesture-based delete (scribble over a shape) is disabled for now, it
    # conflicted with drawing new shapes near existing ones; delete by voice
    # ("delete the database" / point + "delete this") or 'z' undo instead
    shape_type, score = shape_recognizer.recognize(finished)

    if shape_type == "line":
        # a line drawn from one shape to a different shape is a draw.io-style
        # connect gesture: skip the raw wobbly stroke and draw a clean edge
        # between them instead, no voice needed
        start_shape = _connect_endpoint_shape(store, finished, from_start=True)
        end_shape = _connect_endpoint_shape(store, finished, from_start=False)
        if start_shape is not None and end_shape is not None and start_shape.id != end_shape.id:
            edge = Edge(
                from_id=start_shape.id, to_id=end_shape.id,
                route_hint=tuple(orthogonal_route(finished)),
            )
            # an arrow leaving a decision node is a branch and usually needs a
            # condition on it ("yes"/"no") - open the same keyboard typing
            # prompt an action/decision node uses, so it can be typed (or
            # skipped with Enter) right after drawing it
            if symbols.icon_for(start_shape) == "decision":
                edge.awaiting_name = True
            store.add_edge(edge)
            logger.log("connect_gesture", from_id=start_shape.id, to_id=end_shape.id)
            _beep(900, 100)
            return

        # a line drawn inside a single existing rectangle is a different
        # composite gesture ("server" icon) rather than a new shape - speech
        # alone isn't reliable enough offline to carry every icon choice
        xs = [p[0] for p in finished]
        ys = [p[1] for p in finished]
        centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
        host = _rectangle_containing(store, centroid)
        # only apply to a still-plain rectangle - once it has a label/icon
        # (e.g. "user" -> actor), an accidental line crossing it shouldn't
        # silently flip it to server
        if host is not None and not host.label and not host.icon_override:
            host.icon_override = "server"
            if not host.label:
                host.label = "server"
            logger.log("icon_override", shape_id=host.id, icon="server")
            _beep(1400, 60)
            return

    new_shape = store.add_shape_from_stroke(finished, shape_type, now_t)
    print(f"DEBUG shape_created: type={shape_type} score={score:.3f} id={new_shape.id} created_t={now_t}")
    logger.log("shape_created", shape_type=shape_type, score=round(score, 3))
    _beep(1200, 60)


def _handle_intent(intent, store, fusion_engine, logger, reference_t):
    if intent.kind == "LABEL":
        # bind against when the utterance started, not when Vosk finished
        # finalizing it (finalization lag can itself exceed the bind window)
        if intent.payload in config.NODE_TYPE_KEYWORDS:
            # "action"/"decision" pick the icon, not the visible label - the
            # actual name is typed on the keyboard (see typing_state in
            # run()), not spoken, since free-form ASR is unreliable for it
            target = store.most_recent_shape_within(config.LABEL_BIND_WINDOW_S, reference_t)
            if target is None:
                # the word itself may have been misheard the first time (so
                # no icon_override/awaiting_name was ever set to retry off
                # of) - give a retry more room, but only on a shape that's
                # still plain (not already some other icon/label)
                candidate = store.most_recent_shape_within(config.NODE_NAME_BIND_WINDOW_S, reference_t)
                if candidate is not None and not candidate.icon_override and not candidate.label:
                    target = candidate
            if target is not None:
                target.icon_override = intent.payload
                target.awaiting_name = True
                # restart the name-window clock from right now, not from the
                # original drawing time, so a late-recognized "action" still
                # leaves full time to say the actual name afterward
                target.created_t = reference_t
                logger.log("icon_override", shape_id=target.id, icon=intent.payload)
            return

        if intent.payload in config.TERMINAL_KEYWORDS:
            # "start"/"final" are anonymous UML terminal nodes - convert the
            # rectangle immediately, no typed-name step like action/decision
            target = store.most_recent_shape_within(config.LABEL_BIND_WINDOW_S, reference_t)
            if target is not None:
                target.icon_override = intent.payload
                target.label = intent.payload
                logger.log("icon_override", shape_id=target.id, icon=intent.payload)
            return

        target = store.most_recent_shape_within(config.LABEL_BIND_WINDOW_S, reference_t)
        if target is not None:
            target.label = intent.payload
            logger.log("label_bound", shape_id=target.id, label=intent.payload)
    elif intent.kind == "DELETE":
        deleted = fusion_engine.delete_by_intent(intent.payload)
        if deleted is not None:
            _beep(300, 120)
    elif intent.kind == "UNDO":
        store.undo()
        logger.log("undo")
    elif intent.kind == "EXPORT":
        _do_export(store, fusion_engine, logger)


def _do_export(store, fusion_engine, logger):
    path = renderer.export_svg(store)
    logger.log("export", path=path)
    fusion_engine.last_export_t = time.monotonic()
    _beep(1600, 100)
    return path


def run():
    cap = cv2.VideoCapture(config.CAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)

    if not cap.isOpened():
        raise RuntimeError("could not open webcam, check config.CAM_INDEX")

    tracker = HandTracker()
    stroke = StrokeBuffer()
    store = DiagramStore()
    logger = SessionLogger()
    fusion_engine = fusion.FusionEngine(store, logger=logger)
    speech = SpeechEngine()
    speech.start()
    hand_miss_count = 0
    # action/decision names are typed, not spoken - free-form ASR on a
    # short offline model is unreliable for open-ended phrases, so this
    # opens automatically once a shape is awaiting_name and captures
    # keystrokes until Enter/Escape instead of listening for a name
    typing_state = {"active": False, "target": None, "buffer": ""}
    startup_dismissed = False
    help_state = {"active": False}
    paused = False
    # pause only fires once both channels land within this window of each
    # other - the stop-pose gesture is held continuously so it's read fresh
    # every frame, the word only arrives once per finalized utterance
    last_pause_word_t = float("-inf")

    def _on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and _help_badge_hit(x, y):
            help_state["active"] = not help_state["active"]

    cv2.namedWindow(config.WINDOW_NAME)
    cv2.setMouseCallback(config.WINDOW_NAME, _on_mouse)

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            hand = tracker.process(rgb)
            now_t = time.monotonic()
            stop_pose_now = bool(hand and hand["stop_pose"])

            if hand is not None:
                hand_miss_count = 0
                px, py = _to_px(*hand["index_tip"], w, h)

                color = PINCH_COLOR if hand["pinching"] else FINGER_COLOR
                cv2.circle(frame, (px, py), 8, color, -1)

                if not paused:
                    fusion_engine.add_pointer_sample(now_t, px, py)

                    if hand["pinching"] and not stroke.active:
                        stroke.start()
                        # starting a new shape/stroke means whatever action or
                        # decision node was still open for a name is done -
                        # close it out, and drop out of typing mode too since
                        # its target is no longer awaiting a name
                        store.close_awaiting_names()
                        typing_state["active"] = False
                        typing_state["target"] = None
                        typing_state["buffer"] = ""
                    elif not hand["pinching"] and stroke.active:
                        _finish_stroke(stroke.finish(), store, logger, now_t)

                    if stroke.active:
                        stroke.add_point(px, py)
            elif stroke.active and not paused:
                # fast motion / corners cause a frame or two of motion blur where
                # detection drops out entirely; don't kill the stroke on that alone
                hand_miss_count += 1
                if hand_miss_count > config.HAND_LOST_GRACE_FRAMES:
                    _finish_stroke(stroke.finish(), store, logger, now_t)

            for pt in stroke.points:
                cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, TRAIL_COLOR, -1)

            _draw_edges(frame, store)

            if fusion_engine.last_deleted_bbox is not None:
                if now_t - fusion_engine.last_deleted_t <= DELETE_FLASH_DURATION_S:
                    _draw_delete_flash(frame, fusion_engine.last_deleted_bbox)

            if now_t - fusion_engine.last_export_t <= EXPORT_FLASH_DURATION_S:
                cv2.putText(frame, "Exported diagram to SVG", (10, 55),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

            if not paused and not typing_state["active"]:
                # a shape awaiting its spoken/typed name takes priority; if none,
                # a freshly drawn decision-branch arrow awaiting its yes/no label
                pending_name = (store.awaiting_name_shape(config.NODE_NAME_BIND_WINDOW_S, now_t)
                                or store.awaiting_name_edge())
                if pending_name is not None:
                    typing_state["active"] = True
                    typing_state["target"] = pending_name
                    typing_state["buffer"] = ""

            highlighted_id = None
            if fusion_engine.last_highlighted is not None:
                if now_t - fusion_engine.last_highlighted_t <= HIGHLIGHT_DURATION_S:
                    highlighted_id = fusion_engine.last_highlighted.id
            if typing_state["active"]:
                # an edge target has no .id (and nothing to highlight) - only a
                # shape target lights up
                highlighted_id = getattr(typing_state["target"], "id", None)

            for shape in store.shapes:
                _draw_shape(frame, shape, shape.id == highlighted_id)

            # drained every frame regardless of pause state, so recognized
            # speech doesn't pile up and all fire at once the moment we resume
            for final_result in speech.poll():
                if profanity.contains_profanity(final_result.text):
                    # a mis-hearing occasionally lands on something offensive -
                    # drop it before it's printed, labeled, or acted on at all
                    print("DEBUG heard: <filtered>")
                    continue
                print(f"DEBUG heard: {final_result.text!r}")
                # these are known mis-hearings of "stop" that overlap with a
                # real word/meaning elsewhere ("wow" also means "cloud";
                # "that stuff" contains the deictic "that", used constantly
                # for pointing) - only read them as "stop" while the
                # stop-pose gesture is actually showing, since saying them
                # while holding a flat open palm mid-presentation is far
                # more plausible than the other meaning at that exact moment
                if stop_pose_now and final_result.text.strip().lower() in _GESTURE_GATED_STOP_PHRASES:
                    last_pause_word_t = now_t
                    continue
                intent = command_parser.parse(final_result.text)
                print(f"DEBUG intent: {intent}")
                if intent.kind == "PAUSE":
                    last_pause_word_t = now_t
                    continue
                if paused:
                    continue  # ignore every other command while frozen
                fusion_engine.process_words(final_result.words)
                reference_t = final_result.words[0].t_start if final_result.words else now_t
                print(f"DEBUG shapes before handle: {[(s.id, s.created_t, s.label) for s in store.shapes]}, now_t={now_t}, reference_t={reference_t}")
                _handle_intent(intent, store, fusion_engine, logger, reference_t)
                print(f"DEBUG shapes after handle: {[(s.id, s.created_t, s.label) for s in store.shapes]}")

            if not paused and stop_pose_now and now_t - last_pause_word_t <= config.PAUSE_COMBO_WINDOW_S:
                paused = True
                # consume the word so it can't also satisfy a second,
                # unintended pause (e.g. re-opening the palm again after
                # resuming, without saying "stop" again)
                last_pause_word_t = float("-inf")
                if stroke.active:
                    stroke.finish()  # drop whatever was mid-stroke rather than freeze it half-drawn
                logger.log("paused")

            if not paused:
                if fusion_engine.clarification.pending:
                    _draw_clarification(frame, fusion_engine.clarification.pending)

                if typing_state["active"]:
                    # a decision-branch arrow is being labeled, not a node named
                    if isinstance(typing_state["target"], Edge):
                        prompt = f"type arrow label (yes/no), Enter to skip: {typing_state['buffer']}_"
                    else:
                        prompt = f"type name: {typing_state['buffer']}_"
                    cv2.putText(frame, prompt, (10, 85),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, HIGHLIGHT_COLOR, 2, cv2.LINE_AA)

                status_text = _status_text(store, typing_state, now_t)
                if status_text:
                    cv2.putText(frame, status_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                                0.55, (255, 255, 255), 1, cv2.LINE_AA)

                if not startup_dismissed:
                    _draw_startup_hint(frame)

            if help_state["active"]:
                _draw_help_panel(frame)
            _draw_help_badge(frame)

            if paused:
                _draw_pause_card(frame)

            cv2.imshow(config.WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF

            if paused:
                if key == 13:  # Enter - resume
                    paused = False
                    logger.log("resumed")
                elif key == ord("q"):
                    break
                continue

            if not startup_dismissed and not typing_state["active"] and key == 13:
                startup_dismissed = True

            if typing_state["active"]:
                if key == 13:  # Enter - confirm the typed name
                    name = typing_state["buffer"].strip()
                    target = typing_state["target"]
                    if name:
                        target.label = name
                        if isinstance(target, Edge):
                            logger.log("label_bound", from_id=target.from_id,
                                        to_id=target.to_id, label=name)
                        else:
                            logger.log("label_bound", shape_id=target.id, label=name)
                    typing_state["target"].awaiting_name = False
                    typing_state["active"] = False
                    typing_state["target"] = None
                    typing_state["buffer"] = ""
                elif key == 27:  # Escape - cancel without naming
                    typing_state["target"].awaiting_name = False
                    typing_state["active"] = False
                    typing_state["target"] = None
                    typing_state["buffer"] = ""
                elif key in (8, 127):  # Backspace
                    typing_state["buffer"] = typing_state["buffer"][:-1]
                elif 32 <= key <= 126:
                    typing_state["buffer"] += chr(key)
            elif key == ord("q"):
                break
            elif key == ord("e"):
                _do_export(store, fusion_engine, logger)
            elif key == ord("z"):
                store.undo()
                logger.log("undo")
            elif key == 13:  # Enter - manually confirm/close a pending action/decision name
                store.close_awaiting_names()
            elif ord("1") <= key <= ord("9") and fusion_engine.clarification.pending:
                selected = fusion_engine.clarification.resolve(key - ord("1"))
                if selected is not None:
                    fusion_engine.referent_stack.append(selected)
                    fusion_engine.highlight(selected, now_t)
    finally:
        logger.save()
        speech.stop()
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
