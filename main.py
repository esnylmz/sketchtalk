"""Entry point. From the repo root (after setup): python main.py"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "sktalk"))

from sktalk import ui

if __name__ == "__main__":
    ui.run()
