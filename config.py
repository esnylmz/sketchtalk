"""Paths and tuning constants shared across modules."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HAND_MODEL_PATH = os.path.join(BASE_DIR, "models", "hand_landmarker.task")
VOSK_MODEL_PATH = os.path.join(BASE_DIR, "models", "vosk-model-small-en-us-0.15")

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DIAGRAM_DIR = os.path.join(OUTPUT_DIR, "diagrams")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
BENCHMARK_DIR = os.path.join(OUTPUT_DIR, "benchmark")

# camera / window
CAM_INDEX = 0
FRAME_W = 1280
FRAME_H = 720
WINDOW_NAME = "SketchTalk"

# hand tracking
MAX_HANDS = 1
INDEX_TIP = 8  # landmark id, index finger tip
THUMB_TIP = 4
MIN_DETECTION_CONF = 0.6
MIN_TRACKING_CONF = 0.5

# stop-pose (open palm, all four fingers extended) - used together with the
# spoken "stop" for the pause gesture, landmark ids per the standard
# MediaPipe hand topology
WRIST = 0
FINGER_MCP_TIP = {"index": (5, 8), "middle": (9, 12), "ring": (13, 16), "pinky": (17, 20)}
# a finger counts as extended when its tip is this much farther from the
# wrist than its own base knuckle - well clear of 1.0 so a half-curled
# finger (e.g. mid-pinch) doesn't false-positive. Loosened from 1.35 - real
# open-palm gestures (camera angle, a pinky that doesn't fully extend) often
# didn't clear the stricter ratio, making "stop" hard to trigger in practice
STOP_POSE_EXTENSION_RATIO = 1.2
# only 3 of 4 fingers need to read as extended, not all 4 - the same
# real-world-angle reasoning, one finger (often the pinky) reading short
# shouldn't block the whole gesture
STOP_POSE_MIN_FINGERS = 3
STOP_POSE_CONFIRM_FRAMES = 3  # consecutive frames needed before flipping stop-pose state, same idea as pinch debounce

# pinch-to-draw
PINCH_DIST_THRESHOLD = 0.052  # normalized (0-1) distance between thumb/index tips
# rectangles/circles need a lot of wrist rotation, which briefly inflates the
# apparent 2D thumb-index gap even while still physically pinched; keep this
# well clear of that so mid-stroke corners/curves don't get read as a release.
# circles need continuous rotation through the whole stroke (worse than a
# rectangle's four corners), so this needs to be even more forgiving
PINCH_RELEASE_THRESHOLD = 0.13
PINCH_DIST_SMOOTHING = 4  # moving average over last N frames, reduces landmark jitter
FINGERTIP_SMOOTHING = 4  # moving average on the tip position itself, worse jitter near frame edges
PINCH_CONFIRM_FRAMES = 3  # consecutive frames needed before flipping pinch state
HAND_LOST_GRACE_FRAMES = 15  # tolerate this many frames of no detection before ending a stroke

# stroke buffer
MIN_STROKE_POINTS = 4

# draw a line starting in one shape and ending in a different one -> connect
# gesture (draw.io-style), no voice needed. Margin gives slack for a fingertip
# that starts/ends just outside the box edge rather than exactly on a shape
CONNECT_ENDPOINT_MARGIN = 60
# how far (px) a connect stroke's raw point can bow off the straight chord
# before it counts as a real corner instead of hand jitter. Lower = follows
# every wobble (jagged route); higher = smooths out real turns too (loses
# the shape you actually drew)
ROUTE_SIMPLIFY_EPSILON = 30
# sideways spacing (px) between auto-routed edges that connect the same pair
# of shapes, so a second (or third...) connection fans out instead of
# drawing exactly on top of the first
EDGE_PARALLEL_OFFSET_PX = 14

# fusion (used from phase 3 onward, kept here so thresholds live in one place)
REF_DIST_THRESHOLD = 120
AMBIGUITY_RATIO = 1.5
LABEL_BIND_WINDOW_S = 2.0
POINTER_BUFFER_S = 3.0
DEICTIC_WORDS = {"this", "that", "these", "those"}
# specific ASR mis-hearings of a deictic word, observed in testing (e.g.
# "delete this" heard as "the delete does") - normalized before matching
# against DEICTIC_WORDS above
DEICTIC_ALIASES = {"does": "this"}

FUSION_MODE = "FUSED"  # FUSED | GESTURE_ONLY | SPEECH_ONLY

# speech (vosk)
SAMPLE_RATE = 16000  # vosk needs 16kHz mono
BLOCK_SIZE = 8000  # ~0.5s per chunk
AUDIO_GAIN = 1.4  # mild input boost so quieter speech still lands clearly for vosk

# bare label keywords accepted without an article ("server" alone, not just
# "a server") since ASR sometimes drops the article
LABEL_KEYWORDS = {"user", "actor", "person", "client", "cloud", "server",
                   "app", "application", "database", "db", "action", "decision",
                   "start", "final"}

# these two set the shape's icon (like a composite gesture would) instead of
# becoming the visible label text, since the actual action/decision name is
# said as a separate follow-up utterance ("action" ... "check stock")
NODE_TYPE_KEYWORDS = {"action", "decision"}
# extra time (seconds) to say the action/decision's actual name after saying
# the node-type keyword itself - two utterances instead of one, needs more room
NODE_NAME_BIND_WINDOW_S = 4.0

# UML activity-diagram start/final (initial/final) nodes - unlike action/decision
# these are anonymous terminal markers, no follow-up name utterance needed, so
# a rectangle just converts immediately, no awaiting_name/typing step at all.
# "final" is used instead of "end" - "end" turned out to be a near-miss for
# "and", a common word too risky to alias without causing false positives
TERMINAL_KEYWORDS = {"start", "final"}
# real UML initial/final markers are small relative to other nodes, not
# full-sized shapes - both the rendered circle (ui.py) and the connector
# clipping boundary (diagram_store.py) shrink to this fraction of the drawn
# bbox's shorter side, so a connector touches the visible circle, not the
# larger invisible rectangle around it
TERMINAL_NODE_SCALE = 0.55

# how long after drawing an unlabeled shape the status bar nudges the user to
# say its name / draw inside it for an icon, before falling back to the
# general command reminder
NEW_SHAPE_HINT_WINDOW_S = 4.0

# words that pause the app (speech and drawing freeze until Enter is
# pressed) - only takes effect combined with the stop-pose hand gesture,
# never from voice or gesture alone, since either channel misfiring by
# itself shouldn't interrupt a live drawing session
PAUSE_WORDS = {"stop"}
# how long a spoken pause word and the stop-pose gesture can be apart (in
# either order) and still count as the same combined command - generous
# since the gesture is held for a couple of seconds, not a single instant
# like a deictic "this"/"that". Widened from 2.5s after live testing showed
# the gesture debounce (STOP_POSE_CONFIRM_FRAMES) plus normal reaction time
# between saying "stop" and raising a hand often ate more than 2.5s
PAUSE_COMBO_WINDOW_S = 4.0
