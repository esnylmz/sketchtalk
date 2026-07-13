"""Collects fingertip points while pinching, building the raw stroke path."""

import time

import config


class StrokeBuffer:
    def __init__(self):
        self._points = []  # list of (x, y) in canvas coords, current stroke
        self.active = False
        self.start_t = None

    def start(self):
        self._points = []
        self.active = True
        self.start_t = time.monotonic()

    def add_point(self, x, y):
        if not self.active:
            return
        # fingertip position is already smoothed upstream (hand_tracker), smoothing
        # it again here just adds lag that skews the stroke's start/end points
        self._points.append((x, y))

    def finish(self):
        """Ends the stroke and returns its points (empty list if too short)."""
        self.active = False
        pts = self._points
        self._points = []
        if len(pts) < config.MIN_STROKE_POINTS:
            return []
        return pts

    @property
    def points(self):
        return self._points
