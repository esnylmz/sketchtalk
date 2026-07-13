"""Timestamped JSON event log for a session, used for the benchmark later."""

import json
import os
import time

import config


class SessionLogger:
    def __init__(self):
        self._events = []
        self._t0 = time.monotonic()

    def log(self, event_type, **fields):
        self._events.append({
            "t": round(time.monotonic() - self._t0, 4),
            "event": event_type,
            **fields,
        })

    def save(self, path=None):
        if path is None:
            os.makedirs(config.LOG_DIR, exist_ok=True)
            filename = time.strftime("session_%Y%m%d_%H%M%S.json")
            path = os.path.join(config.LOG_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._events, f, indent=2)
        return path
