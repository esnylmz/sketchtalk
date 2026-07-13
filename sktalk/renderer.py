"""Exports the current diagram (shapes + edges) to an SVG file."""

import os
import time
from xml.sax.saxutils import escape

import config
import symbols
from diagram_store import edge_route

SVG_HEADER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
    'viewBox="0 0 {w} {h}">\n'
    '<defs>\n'
    '  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
    'orient="auto" markerUnits="strokeWidth">\n'
    '    <path d="M0,0 L0,6 L9,3 z" fill="black" />\n'
    '  </marker>\n'
    '</defs>\n'
    '<rect width="{w}" height="{h}" fill="white" />\n'
)


def _actor_svg(x1, y1, x2, y2):
    w, h = x2 - x1, y2 - y1
    cx = x1 + w / 2
    head_r = max(min(w, h) * 0.18, 6)
    head_cy = y1 + head_r + 2
    body_top = head_cy + head_r
    body_bottom = y1 + h * 0.68
    arm_y = body_top + (body_bottom - body_top) * 0.3
    return [
        f'<circle cx="{cx}" cy="{head_cy}" r="{head_r}" fill="none" stroke="black" stroke-width="2" />',
        f'<line x1="{cx}" y1="{body_top}" x2="{cx}" y2="{body_bottom}" stroke="black" stroke-width="2" />',
        f'<line x1="{x1 + w * 0.15}" y1="{arm_y}" x2="{x2 - w * 0.15}" y2="{arm_y}" stroke="black" stroke-width="2" />',
        f'<line x1="{cx}" y1="{body_bottom}" x2="{x1 + w * 0.2}" y2="{y2}" stroke="black" stroke-width="2" />',
        f'<line x1="{cx}" y1="{body_bottom}" x2="{x2 - w * 0.2}" y2="{y2}" stroke="black" stroke-width="2" />',
    ]


def _database_svg(x1, y1, x2, y2):
    w, h = x2 - x1, y2 - y1
    cx, rx = x1 + w / 2, w / 2
    ry = max(h * 0.12, 4)
    return [
        f'<ellipse cx="{cx}" cy="{y1 + ry}" rx="{rx}" ry="{ry}" fill="none" stroke="black" stroke-width="2" />',
        f'<line x1="{x1}" y1="{y1 + ry}" x2="{x1}" y2="{y2 - ry}" stroke="black" stroke-width="2" />',
        f'<line x1="{x2}" y1="{y1 + ry}" x2="{x2}" y2="{y2 - ry}" stroke="black" stroke-width="2" />',
        f'<path d="M {x1} {y2 - ry} A {rx} {ry} 0 0 0 {x2} {y2 - ry}" fill="none" stroke="black" stroke-width="2" />',
    ]


def _server_svg(x1, y1, x2, y2):
    w, h = x2 - x1, y2 - y1
    parts = [f'<rect x="{x1}" y="{y1}" width="{w}" height="{h}" fill="none" stroke="black" stroke-width="2" />']
    for i in (1, 2):
        ly = y1 + h * i / 3
        parts.append(f'<line x1="{x1}" y1="{ly}" x2="{x2}" y2="{ly}" stroke="black" stroke-width="1.5" />')
    return parts


def _action_svg(x1, y1, x2, y2):
    w, h = x2 - x1, y2 - y1
    r = max(min(w, h) / 5, 8)
    r = min(r, w / 2, h / 2)
    return [f'<rect x="{x1}" y="{y1}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="none" stroke="black" stroke-width="2" />']


def _decision_svg(x1, y1, x2, y2):
    cx, cy = x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2
    points = f"{cx},{y1} {x2},{cy} {cx},{y2} {x1},{cy}"
    return [f'<polygon points="{points}" fill="none" stroke="black" stroke-width="2" />']


def _terminal_radius(x1, y1, x2, y2):
    return min(x2 - x1, y2 - y1) / 2 * config.TERMINAL_NODE_SCALE


def _start_svg(x1, y1, x2, y2):
    """UML activity initial node: solid filled circle."""
    cx, cy = x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2
    r = _terminal_radius(x1, y1, x2, y2)
    return [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="black" />']


def _final_svg(x1, y1, x2, y2):
    """UML activity final node: ring with filled inner circle."""
    cx, cy = x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2
    r = _terminal_radius(x1, y1, x2, y2)
    inner_r = max(r - 2 - 4, 3)
    return [
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="black" stroke-width="2" />',
        f'<circle cx="{cx}" cy="{cy}" r="{inner_r}" fill="black" />',
    ]


def _cloud_svg(x1, y1, x2, y2):
    contour = symbols.cloud_contour(x2 - x1, y2 - y1)
    points = " ".join(f"{px + x1},{py + y1}" for px, py in contour)
    return [f'<polyline points="{points}" fill="none" stroke="black" stroke-width="2" stroke-linejoin="round" />']


def _shape_svg(shape):
    icon = symbols.icon_for(shape)
    x1, y1, x2, y2 = shape.bbox

    if icon == "line":
        parts = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                  f'stroke="black" stroke-width="2" />']
        label_x, label_y = min(x1, x2), max(y1, y2) + 18
    else:
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if icon == "actor":
            parts = _actor_svg(x1, y1, x2, y2)
        elif icon == "database":
            parts = _database_svg(x1, y1, x2, y2)
        elif icon == "server":
            parts = _server_svg(x1, y1, x2, y2)
        elif icon == "cloud":
            parts = _cloud_svg(x1, y1, x2, y2)
        elif icon == "action":
            parts = _action_svg(x1, y1, x2, y2)
        elif icon == "decision":
            parts = _decision_svg(x1, y1, x2, y2)
        elif icon == "start":
            parts = _start_svg(x1, y1, x2, y2)
        elif icon == "final":
            parts = _final_svg(x1, y1, x2, y2)
        else:
            w, h = x2 - x1, y2 - y1
            parts = [f'<rect x="{x1}" y="{y1}" width="{w}" height="{h}" '
                      f'fill="none" stroke="black" stroke-width="2" />']
        if icon in ("action", "decision"):
            label_x, label_y = x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2 + 5
        elif icon in ("start", "final"):
            cx, cy = x1 + (x2 - x1) / 2, y1 + (y2 - y1) / 2
            r = _terminal_radius(x1, y1, x2, y2)
            label_x, label_y = cx, cy + r + 18
        else:
            label_x, label_y = x1 + (x2 - x1) / 2, y2 + 18

    if shape.label:
        parts.append(f'<text x="{label_x}" y="{label_y}" font-size="14" text-anchor="middle" '
                      f'font-family="sans-serif">{escape(shape.label)}</text>')
    return "\n".join(parts)


def _edge_svg(edge, store, offset=0.0):
    from_shape = next((s for s in store.shapes if s.id == edge.from_id), None)
    to_shape = next((s for s in store.shapes if s.id == edge.to_id), None)
    if from_shape is None or to_shape is None:
        return ""
    points = edge_route(from_shape, to_shape, edge.route_hint, offset=offset)
    path_d = "M " + " L ".join(f"{x} {y}" for x, y in points)
    return f'<path d="{path_d}" fill="none" stroke="black" stroke-width="2" marker-end="url(#arrow)" />'


def export_svg(store, path=None):
    if path is None:
        os.makedirs(config.DIAGRAM_DIR, exist_ok=True)
        filename = time.strftime("diagram_%Y%m%d_%H%M%S.svg")
        path = os.path.join(config.DIAGRAM_DIR, filename)

    lines = [SVG_HEADER.format(w=config.FRAME_W, h=config.FRAME_H)]
    groups = {}
    for edge in store.edges:
        key = tuple(sorted((edge.from_id, edge.to_id)))
        groups.setdefault(key, []).append(edge)
    for edge in store.edges:
        key = tuple(sorted((edge.from_id, edge.to_id)))
        siblings = groups[key]
        index = siblings.index(edge)
        offset = (index - (len(siblings) - 1) / 2) * config.EDGE_PARALLEL_OFFSET_PX
        lines.append(_edge_svg(edge, store, offset=offset))
    for shape in store.shapes:
        lines.append(_shape_svg(shape))
    lines.append("</svg>\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
