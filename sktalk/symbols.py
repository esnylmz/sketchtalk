"""Picks which icon a shape renders as.

Icon choice comes from three places, checked in order: (1) a composite
gesture override (e.g. a line drawn inside a rectangle marks it "server"
without needing speech at all - offline ASR isn't reliable enough to carry
this alone), (2) the spoken label, (3) plain box as the default.
"""

from difflib import SequenceMatcher

import cv2
import numpy as np

_LABEL_ICONS = {
    "database": ("database", "db"),
    "server": ("server",),
    "cloud": ("cloud",),
    # "actor" itself is deliberately not a trigger here - it fuzzy-matches
    # too close to the "action" node keyword below, so only "user" selects
    # the stick-figure icon now
    "actor": ("user", "person", "client"),
    "action": ("action",),
    "decision": ("decision",),
}

_FUZZY_MATCH_RATIO = 0.8  # catches near-miss ASR output like "clout" for "cloud"


def _label_matches(label_words, keyword):
    if any(keyword in word for word in label_words):
        return True
    return any(SequenceMatcher(None, word, keyword).ratio() >= _FUZZY_MATCH_RATIO
               for word in label_words)


def icon_for(shape):
    if shape.icon_override:
        return shape.icon_override
    if shape.type == "line":
        return "line"

    label_words = shape.label.lower().split()
    for icon, keywords in _LABEL_ICONS.items():
        if any(_label_matches(label_words, kw) for kw in keywords):
            return icon
    return "box"


_CLOUD_CANONICAL_W, _CLOUD_CANONICAL_H = 200, 120


def _build_cloud_points():
    """Cloud silhouette at a fixed, good-looking aspect ratio, built once
    from four overlapping circles and reused by every cloud shape. One big
    circle owns the whole top of the cloud - two similarly-sized circles
    competing for the top always produced a seam/notch where they cross, and
    an oversized top circle whose radius pushed past the canvas edge got
    clipped into a flat roof, so this one is sized to stay fully in bounds."""
    scale = 4
    sw, sh = _CLOUD_CANONICAL_W * scale, _CLOUD_CANONICAL_H * scale
    mask = np.zeros((sh, sw), dtype=np.uint8)
    bumps = [
        (0.50, 0.42, 0.34),  # single dominant top bump
        (0.22, 0.62, 0.26),  # left bump
        (0.78, 0.62, 0.26),  # right bump
        (0.50, 0.70, 0.32),  # base fill
    ]
    for fx, fy, fr in bumps:
        cv2.circle(mask, (int(sw * fx), int(sh * fy)), int(sh * fr), 255, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=scale * 1.0)
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    largest = max(contours, key=cv2.contourArea)
    smoothed = cv2.approxPolyDP(largest, epsilon=scale * 0.6, closed=True)
    return [(p[0][0] / scale, p[0][1] / scale) for p in smoothed]


_CLOUD_POINTS = _build_cloud_points()


def cloud_contour(w, h):
    """Cloud silhouette fit into a w x h box without distorting it - scaled
    uniformly (same factor on both axes) and centered, the way a real icon
    asset behaves, instead of stretching to match whatever aspect ratio the
    user happened to draw."""
    w, h = max(w, 1), max(h, 1)
    fit = min(w / _CLOUD_CANONICAL_W, h / _CLOUD_CANONICAL_H)
    off_x = (w - _CLOUD_CANONICAL_W * fit) / 2
    off_y = (h - _CLOUD_CANONICAL_H * fit) / 2
    return [(px * fit + off_x, py * fit + off_y) for px, py in _CLOUD_POINTS]
