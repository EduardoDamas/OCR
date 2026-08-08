"""
Machine-learning card reader (free, offline).

Runs a small CNN (exported to ONNX, executed via OpenCV's DNN module — no torch
or sklearn needed at runtime) to classify each game row as Casa / Empate / Fora.
Trained on client-confirmed pages; ~86% on held-out pages vs ~77% for the
classical recognizer.

If models/mark_cnn.onnx is present, recognize_card() in recognizer.py routes
here automatically; otherwise it falls back to the classical CV path.
"""

import os
import numpy as np
import cv2

from .recognizer import (_build_blue_mask, _build_dark_mask, MarkResult,
                         CardResult, CONFIDENCE_LOW)
from .detector import _find_game_table, LAYOUT

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "mark_cnn.onnx")
RH, RW = 20, 80          # must match training
_net = None


def available() -> bool:
    return os.path.exists(MODEL_PATH)


def _load():
    global _net
    if _net is None:
        _net = cv2.dnn.readNetFromONNX(MODEL_PATH)
    return _net


def _colored_ink(bgr: np.ndarray) -> np.ndarray:
    """Handwritten coloured marks of ANY hue (blue/green/purple), dropping black print."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    return ((s > 50) & (v > 35) & (v < 250)).astype(np.uint8) * 255


def _softmax(x):
    e = np.exp(x - np.max(x))
    return e / np.sum(e)


def recognize_card_ml(card, page: int, layout: dict = None) -> CardResult:
    """Recognize one card's 8 marks with the CNN. Same output shape as recognize_card."""
    cfg = layout or LAYOUT
    net = _load()
    cg, cb = card.image, card.image_bgr
    ch = card.h

    tb = _find_game_table(cg, 8) if cg is not None else None
    if tb is not None:
        y0, y1 = tb
    else:
        y0, y1 = int(ch * cfg["games_y_start"]), int(ch * cfg["games_y_end"])
    rowh = (y1 - y0) / 8.0

    blue = _build_blue_mask(cb)
    col = _colored_ink(cb)
    dark = _build_dark_mask(cg)

    blobs, ink = [], []
    for g in range(8):
        ry0 = max(0, int(y0 + g * rowh))
        ry1 = int(y0 + (g + 1) * rowh)
        chans = []
        for mask in (blue, col, dark):
            st = mask[ry0:ry1, :]
            st = st if st.size else np.zeros((2, 2), np.uint8)
            chans.append(cv2.resize(st, (RW, RH), interpolation=cv2.INTER_AREA))
        blobs.append(np.stack(chans, axis=0).astype(np.float32) / 255.0)
        # blank check: colour + blue ink present in this row?
        b = blue[ry0:ry1, :]; c = col[ry0:ry1, :]
        ink.append(max(float(b.mean()), float(c.mean())) / 255.0)

    net.setInput(np.stack(blobs, axis=0))   # (8,3,RH,RW)
    out = net.forward()                      # (8,3)

    marks = []
    for g in range(8):
        probs = _softmax(out[g])
        choice = int(np.argmax(probs))
        conf = float(probs[choice])
        if ink[g] < 0.003:                   # essentially no mark → blank, flag
            marks.append(MarkResult(game=g, choice=None, confidence=0.0,
                                    needs_review=True, raw_scores=[float(p) for p in probs]))
        else:
            marks.append(MarkResult(game=g, choice=choice, confidence=round(conf, 3),
                                    needs_review=(conf < CONFIDENCE_LOW),
                                    raw_scores=[float(p) for p in probs]))
    return CardResult(card_index=card.index, page=page, marks=marks,
                      has_review_flags=any(m.needs_review for m in marks))
