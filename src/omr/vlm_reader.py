"""
Vision-model card reader (optional high-accuracy path).

Sends a single card image to a Claude vision model and returns the 8 marks.
This is an ALTERNATIVE / fallback to the classical-CV recognizer in
recognizer.py — far more robust to skew/blur/handwriting, at the cost of
needing an API key and internet.

Requires:  pip install anthropic   and an ANTHROPIC_API_KEY.

Typical use is the HYBRID flow: run the fast/free CV recognizer first, then
only call read_card_vlm() on cards the CV flagged for review.
"""

from __future__ import annotations
import base64
import json
import re
from typing import List, Optional, Tuple

import cv2
import numpy as np

# Highest-end default; override with a cheaper model (e.g. claude-sonnet-4-6)
# once accuracy is confirmed, to cut cost.
DEFAULT_MODEL = "claude-opus-4-8"

_OPTIONS = {"casa": 0, "empate": 1, "fora": 2}

PROMPT = (
    "This image is ONE card from a Brazilian football pool (\"bolão\"). "
    "It has a header and a table of 8 game rows. Each game row shows two team "
    "names side by side, with THREE small boxes where the bettor marks ONE pick "
    "by hand (a check, X, slash or circle):\n"
    "  - the box on the FAR LEFT (before the left team) = \"Casa\"\n"
    "  - the box in the MIDDLE (between the two teams)  = \"Empate\"\n"
    "  - the box on the FAR RIGHT (after the right team) = \"Fora\"\n\n"
    "Going top to bottom, for EACH of the 8 game rows decide which box holds a "
    "handwritten mark. Rules:\n"
    "  - exactly one box marked -> \"Casa\", \"Empate\" or \"Fora\"\n"
    "  - no box marked -> null\n"
    "  - two or more boxes marked in the same row -> \"Duplo\"\n"
    "Only look at handwritten marks in the boxes; ignore the printed team names.\n"
    "Respond with ONLY a JSON array of exactly 8 items, e.g.\n"
    "[\"Casa\",\"Empate\",null,\"Fora\",\"Casa\",\"Duplo\",\"Fora\",\"Empate\"]"
)


def _encode_png(img_bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError("failed to encode card image")
    return base64.standard_b64encode(buf.tobytes()).decode("ascii")


def _parse(text: str) -> List[Optional[object]]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON array in response: {text[:120]}")
    arr = json.loads(m.group(0))
    out: List[Optional[object]] = []
    for v in arr[:8]:
        if v is None:
            out.append(None)
        else:
            s = str(v).strip().lower()
            if s == "duplo":
                out.append("Duplo")
            else:
                out.append(_OPTIONS.get(s))     # 0/1/2 or None if unrecognised
    while len(out) < 8:
        out.append(None)
    return out


def read_card_vlm(img_bgr: np.ndarray, client, model: str = DEFAULT_MODEL
                  ) -> Tuple[List[Optional[object]], dict]:
    """
    Return (marks, usage). marks is 8 items: 0=Casa,1=Empate,2=Fora,
    "Duplo"=double mark (game void for that card), None=blank/unclear.
    `client` is an anthropic.Anthropic() instance (passed in so callers reuse it).
    """
    data = _encode_png(img_bgr)
    resp = client.messages.create(
        model=model,
        max_tokens=200,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    marks = _parse(text)
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    return marks, usage
