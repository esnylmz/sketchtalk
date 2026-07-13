"""Wraps MediaPipe HandLandmarker (tasks API, solutions.hands is gone in 0.10.x)."""

import math
import time
from collections import deque

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
import mediapipe as mp

import config


class HandTracker:
    def __init__(self):
        base_options = mp_python.BaseOptions(model_asset_path=config.HAND_MODEL_PATH)
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            num_hands=config.MAX_HANDS,
            min_hand_detection_confidence=config.MIN_DETECTION_CONF,
            min_tracking_confidence=config.MIN_TRACKING_CONF,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._start_t = time.monotonic()
        self._last_timestamp_ms = -1
        self._pinching = False
        self._dist_history = deque(maxlen=config.PINCH_DIST_SMOOTHING)
        self._tip_history = deque(maxlen=config.FINGERTIP_SMOOTHING)
        # debounce: only flip pinch state once the new reading holds for a few frames,
        # otherwise a single noisy landmark frame near the threshold drops a stroke
        self._pending_state = None
        self._pending_count = 0
        self._stop_pose = False
        self._stop_pending_state = None
        self._stop_pending_count = 0

    def process(self, frame_rgb):
        """Returns dict with fingertip/pinch info for the first detected hand, or None."""
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        timestamp_ms = int((time.monotonic() - self._start_t) * 1000)
        # detect_for_video requires strictly increasing timestamps; two calls
        # landing in the same millisecond (fast loop iterations) would
        # otherwise crash the whole app
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.hand_landmarks:
            return None

        landmarks = result.hand_landmarks[0]  # only track one hand (config.MAX_HANDS)
        index_tip = landmarks[config.INDEX_TIP]
        thumb_tip = landmarks[config.THUMB_TIP]

        # landmark noise gets worse near the frame edges (partial occlusion,
        # perspective), smooth the tip position itself, not just the pinch distance
        self._tip_history.append((index_tip.x, index_tip.y))
        tip_x = sum(p[0] for p in self._tip_history) / len(self._tip_history)
        tip_y = sum(p[1] for p in self._tip_history) / len(self._tip_history)

        raw_dist = math.hypot(index_tip.x - thumb_tip.x, index_tip.y - thumb_tip.y)
        self._dist_history.append(raw_dist)
        dist = sum(self._dist_history) / len(self._dist_history)

        threshold = config.PINCH_RELEASE_THRESHOLD if self._pinching else config.PINCH_DIST_THRESHOLD
        raw_pinching = dist < threshold

        if raw_pinching != self._pinching:
            if raw_pinching == self._pending_state:
                self._pending_count += 1
            else:
                self._pending_state = raw_pinching
                self._pending_count = 1
            if self._pending_count >= config.PINCH_CONFIRM_FRAMES:
                self._pinching = raw_pinching
                self._pending_state = None
                self._pending_count = 0
        else:
            self._pending_state = None
            self._pending_count = 0

        raw_stop_pose = self._is_stop_pose(landmarks)
        if raw_stop_pose != self._stop_pose:
            if raw_stop_pose == self._stop_pending_state:
                self._stop_pending_count += 1
            else:
                self._stop_pending_state = raw_stop_pose
                self._stop_pending_count = 1
            if self._stop_pending_count >= config.STOP_POSE_CONFIRM_FRAMES:
                self._stop_pose = raw_stop_pose
                self._stop_pending_state = None
                self._stop_pending_count = 0
        else:
            self._stop_pending_state = None
            self._stop_pending_count = 0

        return {
            "index_tip": (tip_x, tip_y),
            "thumb_tip": (thumb_tip.x, thumb_tip.y),
            "pinch_dist": dist,
            "pinching": self._pinching,
            "stop_pose": self._stop_pose,
            "landmarks": landmarks,
        }

    @staticmethod
    def _is_stop_pose(landmarks):
        """Open palm - at least STOP_POSE_MIN_FINGERS of the 4 fingers
        clearly extended (fingertip farther from the wrist than its own base
        knuckle). Not all 4, since one finger (often the pinky) reading as
        not-quite-extended at an angle shouldn't block the whole gesture."""
        wrist = landmarks[config.WRIST]
        extended = 0
        for mcp_id, tip_id in config.FINGER_MCP_TIP.values():
            mcp, tip = landmarks[mcp_id], landmarks[tip_id]
            mcp_dist = math.hypot(mcp.x - wrist.x, mcp.y - wrist.y)
            tip_dist = math.hypot(tip.x - wrist.x, tip.y - wrist.y)
            if tip_dist >= mcp_dist * config.STOP_POSE_EXTENSION_RATIO:
                extended += 1
        return extended >= config.STOP_POSE_MIN_FINGERS

    def close(self):
        self._landmarker.close()
