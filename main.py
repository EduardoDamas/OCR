"""
Entry point for the Bolão OCR desktop application.
Run: python main.py
"""

import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.dirname(__file__))

from src.ui.main_window import run

if __name__ == "__main__":
    run()
