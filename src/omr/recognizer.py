"""
Mark recognition: determines which cells are marked and assigns confidence scores.
Works with any mark type: X, stroke, circle, pen or pencil (blue or black ink).

Detection strategy (combined):
  1. Blue-ink mask  – HSV hue 95-145° + blue-channel excess; reliable for coloured marks.
  2. Dark-ink mask  – adaptive threshold on grayscale, medium-grey range only; catches
     black pen and pencil while reducing printed-text noise.
  Final cell score = max(blue_score × 2.0, dark_score)
  The ×2 weight on blue rewards the high-specificity signal; dark carries the load
  when ink is black.  Scores > 1.0 are possible and handled by ratio comparison only.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .detector import CardRegion, GameCell, get_game_cells, LAYOUT


CONFIDENCE_LOW = 0.40   # below this → flag for review (tuned down from 0.50 to
                        # cut the review pile ~1/3 at negligible extra silent
                        # error; genuinely-blank reads stay flagged either way)
CONFIDENCE_HIGH = 0.75  # above this → accepted automatically
USER_MODEL_TRUST = 0.70 # a user-trained model only overrides a read when it is
                        # at least this confident (see trainer.py)

# ── Ink masks ──────────────────────────────────────────────────────────────


def _build_blue_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of blue/coloured ink pixels in a BGR image."""
    b, g, r = cv2.split(bgr)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, (95, 30, 60), (145, 255, 255))
    excess = np.clip(b.astype(int) - np.maximum(r, g).astype(int), 0, 255).astype(np.uint8)
    m2 = cv2.threshold(excess, 15, 255, cv2.THRESH_BINARY)[1]
    mask = cv2.bitwise_or(m1, m2)
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)


def _build_dark_mask(gray: np.ndarray) -> np.ndarray:
    """Binary mask of dark handwritten marks (black pen / pencil), reducing text noise."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    local = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY_INV, 11, 3)
    medium = cv2.inRange(gray, 70, 210)
    mask = cv2.bitwise_and(local, medium)
    return cv2.dilate(mask, np.ones((2, 2), np.uint8), iterations=1)


@dataclass
class MarkResult:
    game: int            # 0-based game index
    choice: Optional[int]  # 0=Casa, 1=Empate, 2=Fora; None = no mark / ambiguous
    confidence: float    # 0.0 – 1.0
    needs_review: bool
    raw_scores: List[float]   # fill density per option


@dataclass
class CardResult:
    card_index: int      # 0-based position on page
    page: int
    marks: List[MarkResult]
    has_review_flags: bool
    participant: Optional[str] = None   # bettor name (digital sheets / merged files)


_INSET = 2


def _crop_cell(mask: np.ndarray, cell: GameCell) -> np.ndarray:
    """Crop a mask (or image) to the cell region with a small inset."""
    y1 = max(0, cell.y + _INSET)
    y2 = min(mask.shape[0], cell.y + cell.h - _INSET)
    x1 = max(0, cell.x + _INSET)
    x2 = min(mask.shape[1], cell.x + cell.w - _INSET)
    return mask[y1:y2, x1:x2]


def _mask_density(region: np.ndarray) -> float:
    """Fraction of non-zero pixels in a binary mask region."""
    if region.size == 0:
        return 0.0
    return float(np.sum(region > 0)) / region.size


# ── Backward-compat shims (used by unit tests) ──────────────────────────────

def _fill_density(gray_img: np.ndarray) -> float:
    """Fraction of dark pixels (< 128) in a grayscale cell."""
    if gray_img.size == 0:
        return 0.0
    return float(np.sum(gray_img < 128)) / gray_img.size


def _cell_score(gray_img: np.ndarray) -> float:
    """Single-channel mark score for a grayscale cell crop (no blue detection)."""
    return _fill_density(gray_img)


# ─────────────────────────────────────────────────────────────────────────────

def _cell_score_from_masks(cell: GameCell,
                           blue_mask: Optional[np.ndarray],
                           dark_mask: np.ndarray) -> float:
    """
    Combined mark score from precomputed masks.
    Blue signal is weighted 2× because it is high-specificity.
    Dark signal carries the load for black/pencil marks.
    Returns values in [0, ∞); comparison by ratio only.
    """
    sd = _mask_density(_crop_cell(dark_mask, cell))
    if blue_mask is not None:
        sb = _mask_density(_crop_cell(blue_mask, cell))
        return max(sb * 2.0, sd)
    return sd


def _scores_to_choice(scores: List[float]) -> Tuple[Optional[int], float, bool]:
    """
    Convert raw option scores to a single choice with confidence.

    Returns: (choice_index_or_None, confidence, needs_review)
    """
    if not scores or max(scores) < 0.04:
        # Nothing marked at all
        return None, 0.0, True

    sorted_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    best = sorted_idx[0]
    second = sorted_idx[1] if len(sorted_idx) > 1 else None

    best_score = scores[best]
    second_score = scores[second] if second is not None else 0.0

    # Separation ratio: how much clearer the best mark is vs the runner-up
    separation = (best_score - second_score) / (best_score + 1e-9)

    # Map to confidence
    if separation > 0.50:
        confidence = min(1.0, 0.75 + separation * 0.5)
    elif separation > 0.25:
        confidence = 0.55 + separation * 0.8
    else:
        confidence = separation * 1.5

    needs_review = confidence < CONFIDENCE_LOW or (
        second_score > 0.08 and separation < 0.35
    )

    return best, round(confidence, 3), needs_review


def _maybe_refine(card: CardRegion, result: "CardResult",
                  layout: dict = None) -> "CardResult":
    """If the user has trained an in-app model, use it to refine confident marks.

    Additive and safe: does nothing until the user trains one; never touches a
    blank read (choice None = a localization miss it can't help); only overrides
    when the model is clearly confident.  See trainer.py.
    """
    try:
        from . import trainer
        if not trainer.available():
            return result
        feats = trainer.game_features(card, layout)
        choices, confs = trainer.predict_games(feats)
        if choices is None:
            return result
        for g, m in enumerate(result.marks):
            if m.choice is None or g >= len(choices):
                continue
            uc, ucf = int(choices[g]), float(confs[g])
            if ucf >= USER_MODEL_TRUST:
                m.choice = uc
                m.confidence = round(ucf, 3)
                m.needs_review = ucf < CONFIDENCE_LOW
        result.has_review_flags = any(m.needs_review for m in result.marks)
    except Exception:
        pass
    return result


def recognize_card(card: CardRegion, page: int,
                   layout: dict = None) -> CardResult:
    """
    Run mark recognition on a single card.

    If a trained ML model is bundled (models/mark_cnn.onnx), it is used (more
    accurate on these photos); otherwise the classical blue/dark-mask scoring
    below is used.  Any ML error falls back to the classical path.  A user-trained
    in-app model (trainer.py), if present, then refines the confident marks.
    """
    try:
        from . import ml_reader
        if card.image_bgr is not None and ml_reader.available():
            return _maybe_refine(card, ml_reader.recognize_card_ml(card, page, layout), layout)
    except Exception:
        pass  # fall back to the classical CV path below

    cells = get_game_cells(card, layout)
    cfg = layout or LAYOUT
    games = cfg["games_per_card"]

    # Build masks once for the entire card
    blue_mask = (_build_blue_mask(card.image_bgr)
                 if card.image_bgr is not None else None)
    dark_mask = _build_dark_mask(card.image)

    # Group cells by game
    game_cells: List[List[GameCell]] = [[] for _ in range(games)]
    for cell in cells:
        game_cells[cell.game].append(cell)

    marks: List[MarkResult] = []
    for game_idx, gcells in enumerate(game_cells):
        gcells_sorted = sorted(gcells, key=lambda c: c.option)
        scores = [_cell_score_from_masks(cell, blue_mask, dark_mask)
                  for cell in gcells_sorted]

        choice, confidence, needs_review = _scores_to_choice(scores)
        marks.append(MarkResult(
            game=game_idx,
            choice=choice,
            confidence=confidence,
            needs_review=needs_review,
            raw_scores=scores,
        ))

    has_review = any(m.needs_review for m in marks)
    return _maybe_refine(card, CardResult(card_index=card.index, page=page,
                         marks=marks, has_review_flags=has_review), layout)


def recognize_page(page_gray: np.ndarray, page_number: int,
                   layout: dict = None,
                   page_bgr: Optional[np.ndarray] = None,
                   fmt: str = "auto") -> List[CardResult]:
    """
    Full pipeline for one page: detect cards → recognize marks.
    page_bgr: optional colour image of the same page for blue-ink detection.

    `fmt` selects the card format:
      - "extra"   : the "BOLÃO EXTRA / FOLHA Nº" sheets → ECC template reader.
      - "segunda" : the default "TODA SEGUNDA" path (per-card line detection).
      - "auto"    : try the EXTRA reader; if the page locks onto the bundled
                    template (high ECC correlation) use it, else fall back to
                    the SEGUNDA path. Prefer passing an explicit format at the
                    file level (see pipeline.process_file) to avoid the cost of
                    attempting registration on every page.

    Uses the fixed, calibrated LAYOUT (4×6 grid = 24 cards/page) for the SEGUNDA
    path.  Returns list of CardResult (24 per page).
    """
    if fmt in ("auto", "extra"):
        try:
            from . import extra_reader
            if extra_reader.available() and page_bgr is not None:
                out = extra_reader.recognize_page_extra(page_gray, page_bgr, page_number)
                if out is not None:
                    cards, cc = out
                    if fmt == "extra" or cc >= extra_reader.LOCK_CC:
                        return cards
        except Exception:
            pass  # fall back to the SEGUNDA path below

    from .detector import detect_cards

    cfg = layout or LAYOUT
    cards = detect_cards(page_gray, cfg, page_bgr=page_bgr)
    return [recognize_card(c, page_number, cfg) for c in cards]


OPTION_LABELS = ["Casa", "Empate", "Fora"]


def choice_label(choice: Optional[int]) -> str:
    if choice is None:
        return "?"
    return OPTION_LABELS[choice] if 0 <= choice < len(OPTION_LABELS) else "?"
