"""Word-timestamp deictic fusion: resolves "this"/"that" to a shape for delete."""

import math
import time
from collections import deque

import config
from diagram_store import distance_to_shape


class PointerBuffer:
    """Rolling buffer of (t, x, y) fingertip samples, used to look up where the
    finger was pointing at an arbitrary past timestamp (a spoken word's start time)."""

    def __init__(self, window_s=config.POINTER_BUFFER_S):
        self._window_s = window_s
        self._samples = deque()

    def add(self, t, x, y):
        self._samples.append((t, x, y))
        cutoff = t - self._window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def at_time(self, t):
        """Position at time t, linearly interpolated between the two closest samples."""
        if not self._samples:
            return None
        samples = self._samples
        if t <= samples[0][0]:
            return samples[0][1], samples[0][2]
        if t >= samples[-1][0]:
            return samples[-1][1], samples[-1][2]
        for i in range(1, len(samples)):
            t_b, xb, yb = samples[i]
            if t_b >= t:
                t_a, xa, ya = samples[i - 1]
                span = t_b - t_a
                ratio = (t - t_a) / span if span > 1e-9 else 0.0
                return xa + ratio * (xb - xa), ya + ratio * (yb - ya)
        return samples[-1][1], samples[-1][2]


class ClarificationManager:
    def __init__(self):
        self.pending = None  # list[Shape] or None

    def prompt(self, candidates):
        self.pending = list(candidates)
        return self.pending

    def resolve(self, index):
        if not self.pending or index < 0 or index >= len(self.pending):
            return None
        shape = self.pending[index]
        self.pending = None
        return shape

    def clear(self):
        self.pending = None


def _overlap_ratio(bbox_a, bbox_b):
    """Intersection area over bbox_b's area (how much of bbox_b the scribble covers)."""
    ax1, ax2 = sorted((bbox_a[0], bbox_a[2]))
    ay1, ay2 = sorted((bbox_a[1], bbox_a[3]))
    bx1, bx2 = sorted((bbox_b[0], bbox_b[2]))
    by1, by2 = sorted((bbox_b[1], bbox_b[3]))

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    b_area = (bx2 - bx1) * (by2 - by1)
    if b_area <= 0:
        return 0.0
    return ((ix2 - ix1) * (iy2 - iy1)) / b_area


class FusionEngine:
    def __init__(self, store, logger=None):
        self.store = store
        self.logger = logger
        self.pointer_buffer = PointerBuffer()
        self.referent_stack = deque(maxlen=2)
        self.clarification = ClarificationManager()
        self.last_highlighted = None
        self.last_highlighted_t = 0.0
        self.last_deleted_bbox = None
        self.last_deleted_t = 0.0
        self.last_export_t = 0.0

    def add_pointer_sample(self, t, x, y):
        self.pointer_buffer.add(t, x, y)

    def highlight(self, shape, t):
        self.last_highlighted = shape
        self.last_highlighted_t = t

    def resolve_deictic(self, t_word):
        """Nearest shape to the fingertip position at t_word, or None if ambiguous/none."""
        pos = self.pointer_buffer.at_time(t_word)
        if pos is None or not self.store.shapes:
            return None

        # a fingertip literally on/near a shape's real drawn boundary is an
        # unambiguous match even on a corner far from the centroid - pure
        # centroid distance under-detects corners on larger shapes. Uses the
        # shape's actual polygon (diamond/circle), not its bounding box, so
        # a start/final node's much larger invisible bbox can't claim a
        # point that's actually next to a different, nearby shape
        contained = [s for s in self.store.shapes if distance_to_shape(s, pos) <= 15]
        if len(contained) == 1:
            nearest = contained[0]
            self.referent_stack.append(nearest)
            self.highlight(nearest, t_word)
            if self.logger:
                self.logger.log("deictic_resolved", shape_id=nearest.id, t_word=t_word)
            return nearest

        ranked = sorted(
            self.store.shapes,
            key=lambda s: math.dist(pos, s.centroid),
        )
        nearest = ranked[0]
        nearest_dist = math.dist(pos, nearest.centroid)

        if nearest_dist >= config.REF_DIST_THRESHOLD:
            return None

        if len(ranked) > 1:
            second_dist = math.dist(pos, ranked[1].centroid)
            if second_dist < config.AMBIGUITY_RATIO * nearest_dist:
                self.clarification.prompt(ranked[:2])
                if self.logger:
                    self.logger.log("clarification_prompted", candidates=[s.id for s in ranked[:2]])
                return None

        self.referent_stack.append(nearest)
        self.highlight(nearest, t_word)
        if self.logger:
            self.logger.log("deictic_resolved", shape_id=nearest.id, t_word=t_word)
        return nearest

    def process_words(self, words):
        """Scans a final transcript's per-word timestamps for deictic words."""
        for w in words:
            word = w.word.lower()
            word = config.DEICTIC_ALIASES.get(word, word)
            if word in config.DEICTIC_WORDS:
                resolved = self.resolve_deictic(w.t_start)
                print(f"DEBUG deictic: word={word!r} t={w.t_start:.3f} "
                      f"-> {resolved.id if resolved else None} "
                      f"referent_stack={[s.id for s in self.referent_stack]}")

    def delete_by_intent(self, target_text):
        target_text = target_text.strip().lower()
        target_text = config.DEICTIC_ALIASES.get(target_text, target_text)
        if target_text in config.DEICTIC_WORDS and self.referent_stack:
            return self._delete_shape(self.referent_stack[-1])

        for shape in list(self.store.shapes):
            if shape.label and shape.label.lower() in target_text:
                return self._delete_shape(shape)
        return None

    def delete_by_scribble(self, stroke_points):
        xs = [p[0] for p in stroke_points]
        ys = [p[1] for p in stroke_points]
        scribble_bbox = (min(xs), min(ys), max(xs), max(ys))
        for shape in list(self.store.shapes):
            if _overlap_ratio(scribble_bbox, shape.bbox) > 0.5:
                return self._delete_shape(shape)
        return None

    def _delete_shape(self, shape):
        self.store.remove_shape(shape)
        if shape in self.referent_stack:
            self.referent_stack.remove(shape)
        self.last_deleted_bbox = shape.bbox
        self.last_deleted_t = time.monotonic()
        if self.logger:
            self.logger.log("delete", shape_id=shape.id)
        return shape
