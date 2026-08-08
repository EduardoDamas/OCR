"""
Card and grid detection for the "Bola Escapa / Bolão de Coluna" format.

Real page layout (calibrated from ruler-image measurement at 300dpi on client PDF):
  - 4 cols × 6 rows = 24 cards per page
  - Game area Y: starts at ~41.5% of card height (header takes up the top 41%)
  - Game rows: 8 rows, each ~4.8% of card height
  - Mark columns (fraction of card width):
      CASA   : centre 0.333, half 0.018  (left-edge narrow column)
      EMPATE : centre 0.658, half 0.028  (centre divider between teams)
      FORA   : centre 0.970, half 0.020  (far-right narrow column)
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class CardRegion:
    index: int          # 0-based card index on the page (0..23)
    x: int
    y: int
    w: int
    h: int
    image: np.ndarray        # full card crop (grayscale)
    image_bgr: Optional[np.ndarray] = None  # BGR crop for colour-ink detection


@dataclass
class GameCell:
    game: int           # 0-based (0..7)
    option: int         # 0=Casa, 1=Empate, 2=Fora
    x: int              # pixel coords inside card image
    y: int
    w: int
    h: int


# Default layout — calibrated against the "TODA SEGUNDA / BOLÃO DA RESSACA"
# cards (validated to 24/24 on three labelled cards).
#
# The 8 game rows are located PER CARD from the printed table lines
# (_find_game_table); games_y_start/end below are only a fallback for cards
# whose lines can't be detected.  The three mark boxes are fixed fractions of
# the card width: CASA at the far left, EMPATE on the centre divider, FORA at
# the far right.
LAYOUT = {
    "cards_cols": 4,
    "cards_rows": 6,
    "games_per_card": 8,
    "options_per_game": 3,
    # Fallback vertical extent (used only when table lines aren't found)
    "games_y_start": 0.19,
    "games_y_end":   0.66,
    # Mark column centres (fraction of card width)
    "col_casa_cx":   0.10,
    "col_empate_cx": 0.49,
    "col_fora_cx":   0.86,
    # Per-column half-widths (fraction of card width)
    "col_half_ws":   [0.055, 0.055, 0.055],
    "col_half_w":    0.055,   # scalar fallback
}


def _grid_line_masks(gray: np.ndarray):
    """Return (horizontal, vertical) printed-line masks via morphology."""
    h, w = gray.shape[:2]
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 15, 8)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 40), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 50)))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)
    return horiz, vert


def _card_skew_angle(card_gray: np.ndarray) -> float:
    """
    Local slant (degrees) of a single card crop, from its near-horizontal edges.

    The global deskew removes the sheet's overall rotation, but photographed
    sheets keep a residual perspective tilt that VARIES across the page (worst
    at the corners / top rows).  That local slant turns the table's horizontal
    lines diagonal, which the 1px-tall line morphology can't detect — so the
    top-row cards otherwise fail completely.  Rotating each crop flat first
    fixes it.
    """
    edges = cv2.Canny(cv2.GaussianBlur(card_gray, (3, 3), 0), 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=60,
                            minLineLength=max(20, card_gray.shape[1] // 4),
                            maxLineGap=15)
    if lines is None:
        return 0.0
    angs = []
    for ln in lines:
        x1, y1, x2, y2 = ln[0]
        if x2 != x1:
            a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(a) < 15:        # near-horizontal table lines only
                angs.append(a)
    return float(np.median(angs)) if angs else 0.0


def _rotate(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.3:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _strip_title_band(horiz: np.ndarray, w: int, y0: int, bh: int) -> int:
    """
    If the content box opens with a TITLE band ("BOLÃO DA RESSACA - DIA ..."),
    return a new top y that skips past it; otherwise return y0 unchanged.

    The title is a rounded box of a few WIDE horizontal rules near the very top,
    separated from the first card row by a clear vertical gap.  Even-division
    would otherwise count that band as card-grid height and push the whole top
    row of cards up onto the header — and on faint/angled photos the top cards'
    own table lines vanish, so this is what keeps row 0 from being read blank.
    Self-guards: only a SHORT band close to the top qualifies, and it never
    strips more than half the box (returns y0 when unsure ⇒ no-op).
    """
    rowsum = np.sum(horiz > 0, axis=1).astype(float)
    band = rowsum[y0:y0 + bh]
    ys = np.where(band >= w * 0.30)[0]     # wide rules (title border / card rows)
    if len(ys) == 0:
        return y0

    # The first CARD ROW is a regular run of table lines spaced ~1/8 of a card
    # height apart; the title above it is only a top rule + text (RESSACA: a
    # bordered box; ESCAPA: a rule + "RODADA DO DIA").  Cluster the wide rules
    # into distinct lines, then find the first run of ≥5 lines at card-row pitch —
    # that skips any title style (a solid border is one clustered line, not a run).
    card_h = max(1, bh // 6)
    groups = []                             # centroids of distinct wide lines
    s = p = int(ys[0])
    for y in list(ys[1:]) + [10 ** 9]:
        if y - p > 8:
            groups.append((s + p) // 2)
            s = int(y)
        p = int(y)
    lo, hi = 0.06 * card_h, 0.55 * card_h   # plausible inter-row spacing
    grid_start = None
    for i in range(len(groups) - 4):
        if groups[i] > 0.35 * bh:
            break
        run = groups[i:i + 5]
        gaps = np.diff(run)
        if np.all((gaps >= lo) & (gaps <= hi)):
            grid_start = int(groups[i])
            break
    if grid_start is not None:
        if grid_start > 0.04 * bh and (bh - grid_start) >= 0.55 * bh:
            return y0 + grid_start
        return y0

    # Fallback: original short-title-box logic.
    s = p = int(ys[0])
    for y in ys[1:]:
        if y - p > 40:
            break
        p = int(y)
    title_end = p
    if title_end > 0.20 * bh or (title_end - s) > 0.15 * bh:
        return y0
    after = band[title_end + 1:]
    nz = np.where(after > w * 0.05)[0]
    new_top_rel = (title_end + 1 + nz[0]) if len(nz) else title_end + 1
    if bh - new_top_rel < 0.5 * bh:
        return y0
    return y0 + int(new_top_rel)


def _content_bbox(gray: np.ndarray) -> Tuple[int, int, int, int]:
    """
    Bounding box of the printed card grid (ink), ignoring the photographed
    background margins and the page title (which has no table lines).
    Falls back to the full image if no grid is found.
    """
    h, w = gray.shape[:2]
    horiz, vert = _grid_line_masks(gray)
    grid = cv2.bitwise_or(horiz, vert)
    rowsum = np.sum(grid > 0, axis=1).astype(float)
    colsum = np.sum(grid > 0, axis=0).astype(float)
    if rowsum.max() == 0 or colsum.max() == 0:
        return 0, 0, w, h
    rows = np.where(rowsum > rowsum.max() * 0.08)[0]
    cols = np.where(colsum > colsum.max() * 0.08)[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0, 0, w, h
    x0, x1 = int(cols[0]), int(cols[-1])
    y0, y1 = int(rows[0]), int(rows[-1])
    # Drop a leading title band so the top card row isn't localised on the header.
    y0 = _strip_title_band(horiz, w, y0, max(1, y1 - y0))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _horizontal_line_ys(card_gray: np.ndarray) -> List[int]:
    """y-positions of strong full-width horizontal table lines in a card crop."""
    horiz, _ = _grid_line_masks(card_gray)
    w = card_gray.shape[1]
    rowsum = np.sum(horiz > 0, axis=1).astype(float)
    thr = w * 0.45
    ys: List[int] = []
    y, H = 0, len(rowsum)
    while y < H:
        if rowsum[y] >= thr:
            y0 = y
            while y < H and rowsum[y] >= thr * 0.6:
                y += 1
            ys.append((y0 + y) // 2)
        else:
            y += 1
    return ys


def _find_game_table(card_gray: np.ndarray,
                     n_rows: int = 8) -> Optional[Tuple[int, int]]:
    """
    Locate the game table inside a card crop as the run of (n_rows + 1) most
    evenly-spaced horizontal lines.  This is what pins the 8 game rows precisely
    — far more reliable on distorted photos than fixed vertical fractions, and
    it ignores the title/header and the NOME/FONE lines (different spacing).
    Returns (y_top, y_bottom) of the table, or None if not found.
    """
    ys = _horizontal_line_ys(card_gray)
    if len(ys) < n_rows + 1:
        return None
    best = None
    for i in range(len(ys) - n_rows):
        seg = ys[i:i + n_rows + 1]
        gaps = np.diff(seg)
        med = np.median(gaps)
        if med <= 2:
            continue
        if np.all(np.abs(gaps - med) <= 0.45 * med):
            spread = np.std(gaps) / med          # lower = more even
            if best is None or spread < best[0]:
                best = (spread, int(seg[0]), int(seg[-1]))
    if best is None:
        return None
    return best[1], best[2]


def _find_mark_columns(card_gray: np.ndarray, y_top: int, y_bot: int
                       ) -> Optional[Tuple[float, float, float]]:
    """
    Locate the three mark-box column centres (CASA, EMPATE, FORA) in pixels from
    the card's printed vertical lines, within the game-table band [y_top, y_bot].

    The two team-name columns are the widest gaps between vertical lines; the
    three mark boxes are the narrow gaps immediately left of, between, and right
    of them.  Choosing the team pair that straddles the crop centre makes this
    robust to horizontal drift (perspective) and to neighbour cards bleeding
    into the over-cropped image.  Returns None if the structure isn't clear.
    """
    h_reg = y_bot - y_top
    if h_reg <= 0:
        return None
    _, vert = _grid_line_masks(card_gray)
    colsum = np.sum(vert[y_top:y_bot, :] > 0, axis=0).astype(float)
    thr = h_reg * 0.45
    xs: List[int] = []
    x, W = 0, len(colsum)
    while x < W:
        if colsum[x] >= thr:
            x0 = x
            while x < W and colsum[x] >= thr * 0.5:
                x += 1
            xs.append((x0 + x) // 2)
        else:
            x += 1
    if len(xs) < 5:
        return None

    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    wide = [i for i, g in enumerate(gaps) if g > 0.13 * W]   # team-column gaps
    if len(wide) < 2:
        return None

    centre = W / 2.0
    best = None
    for a in range(len(wide) - 1):
        iL, iR = wide[a], wide[a + 1]
        if iR - iL != 2:                 # exactly one box line between two teams
            continue
        if iL < 1 or iR + 1 >= len(xs):
            continue
        casa = (xs[iL - 1] + xs[iL]) / 2.0
        empate = (xs[iL + 1] + xs[iR]) / 2.0
        if iR + 2 < len(xs):
            fora = (xs[iR + 1] + xs[iR + 2]) / 2.0
        else:
            fora = xs[iR + 1] + (xs[iL] - xs[iL - 1]) / 2.0
        d = abs((casa + fora) / 2.0 - centre)
        if best is None or d < best[0]:
            best = (d, casa, empate, fora)
    if best is None:
        return None
    return best[1], best[2], best[3]


def detect_cards(page_gray: np.ndarray,
                 layout: dict = None,
                 page_bgr: Optional[np.ndarray] = None) -> List[CardRegion]:
    """
    Detect and return the 24 card regions from a preprocessed grayscale page.

    Pipeline (robust to photographed, mildly distorted sheets):
      1. Crop to the printed-content bounding box (drops table-surface margins
         and the page title that even-division would otherwise swallow).
      2. Divide that box into the calibrated 4×6 grid.
      3. Crop each card with generous vertical AND horizontal headroom so its
         full game table is always captured (perspective drift never clips a row
         or a mark column).  The exact rows and mark columns are then found per
         card by _find_game_table() / _find_mark_columns() in get_game_cells().

    page_bgr: optional full-colour page; when provided each CardRegion also
    stores a BGR crop (used for blue-ink mark detection).
    """
    cfg = layout or LAYOUT
    cols = cfg["cards_cols"]
    rows = cfg["cards_rows"]
    h, w = page_gray.shape[:2]

    bx, by, bw, bh = _content_bbox(page_gray)
    # Guard against a degenerate bbox (e.g. blank page) — fall back to full frame
    if bw < w * 0.3 or bh < h * 0.3:
        bx, by, bw, bh = 0, 0, w, h

    card_w = bw // cols
    card_h = bh // rows
    head = int(card_h * 0.25)   # headroom above so row 1 is never clipped
    pad = int(card_h * 0.08)    # a little below for the last row's bottom line
    hpad = int(card_w * 0.18)   # side headroom so drifted cards stay whole

    cards: List[CardRegion] = []
    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col
            cx = bx + col * card_w
            cy = by + row * card_h

            cy_s = max(0, cy - head)
            y_end = min(cy + card_h + pad, h)
            cx_s = max(0, cx - hpad)
            x_end = min(cx + card_w + hpad, w)

            crop_gray = page_gray[cy_s:y_end, cx_s:x_end]
            crop_bgr = (page_bgr[cy_s:y_end, cx_s:x_end].copy()
                        if page_bgr is not None else None)

            # Local deskew: flatten this card's residual perspective slant so its
            # table lines are detectable (essential for the top-row cards).
            ang = _card_skew_angle(crop_gray)
            if abs(ang) >= 0.3:
                crop_gray = _rotate(crop_gray, ang)
                if crop_bgr is not None:
                    crop_bgr = _rotate(crop_bgr, ang)

            cards.append(CardRegion(index=idx, x=cx_s, y=cy_s,
                                    w=x_end - cx_s, h=y_end - cy_s,
                                    image=crop_gray, image_bgr=crop_bgr))
    return cards


def _detect_sheet_bounds(gray: np.ndarray, w: int, h: int) -> Tuple[int, int, int, int]:
    """Find the printable area of the sheet; fall back to a small margin."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255,
                                   cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 15, 4)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        for c in contours[:3]:
            if cv2.contourArea(c) > 0.25 * w * h:
                rx, ry, rw, rh = cv2.boundingRect(c)
                return rx, ry, rw, rh

    # 5% margin fallback
    mx, my = int(w * 0.05), int(h * 0.05)
    return mx, my, w - 2 * mx, h - 2 * my


def get_game_cells(card: CardRegion, layout: dict = None) -> List[GameCell]:
    """
    Return the list of GameCell objects for all 8 games × 3 options.

    Both axes are located from the card's printed table lines so they stay
    aligned despite photo distortion:
      • rows    — _find_game_table()   (8 game rows)
      • columns — _find_mark_columns()  (CASA / EMPATE / FORA boxes)
    Either falls back to fixed fractions of the card crop when its lines can't
    be detected.
    """
    cfg = layout or LAYOUT
    games   = cfg["games_per_card"]
    cw, ch  = card.w, card.h
    have_img = card.image is not None

    table = _find_game_table(card.image, games) if have_img else None
    if table is not None:
        y_start, y_end = table
    else:
        y_start = int(ch * cfg["games_y_start"])
        y_end   = int(ch * cfg["games_y_end"])
    game_h = max(1, (y_end - y_start) // games)

    # Support per-column half-widths (col_half_ws) falling back to scalar col_half_w
    scalar_hw = int(cw * cfg.get("col_half_w", 0.020))
    half_ws_frac = cfg.get("col_half_ws", None)

    mark_cols = _find_mark_columns(card.image, y_start, y_end) if have_img else None
    if mark_cols is not None:
        col_centres = [int(round(x)) for x in mark_cols]
    else:
        col_centres = [
            int(cw * cfg["col_casa_cx"]),
            int(cw * cfg["col_empate_cx"]),
            int(cw * cfg["col_fora_cx"]),
        ]

    cells: List[GameCell] = []
    for g in range(games):
        gy = y_start + g * game_h
        for opt, cx in enumerate(col_centres):
            if half_ws_frac is not None and opt < len(half_ws_frac):
                hw = int(cw * half_ws_frac[opt])
            else:
                hw = scalar_hw
            x0 = max(0, cx - hw)
            x1 = min(cw, cx + hw)
            cells.append(GameCell(game=g, option=opt,
                                  x=x0, y=gy, w=x1 - x0, h=game_h))
    return cells


def auto_tune_layout(page_gray: np.ndarray) -> dict:
    """
    Attempt to refine the column-count guess from projection profiles.
    Returns a layout dict; falls back to LAYOUT defaults.
    """
    cfg = dict(LAYOUT)
    h, w = page_gray.shape[:2]

    blur  = cv2.GaussianBlur(page_gray, (3, 3), 0)
    edges = cv2.Canny(blur, 30, 90)

    h_profile = np.sum(edges, axis=1).astype(float)
    v_profile = np.sum(edges, axis=0).astype(float)

    h_peaks = _count_peaks(h_profile, min_gap=h // 30)
    v_peaks = _count_peaks(v_profile, min_gap=w // 20)

    if h_peaks >= 5:
        cfg["cards_rows"] = h_peaks - 1
    if v_peaks >= 3:
        cfg["cards_cols"] = v_peaks - 1

    return cfg


def _count_peaks(profile: np.ndarray, min_gap: int) -> int:
    threshold = np.max(profile) * 0.3
    above = profile > threshold
    peaks, in_peak, last_peak = 0, False, -min_gap
    for i, val in enumerate(above):
        if val and not in_peak:
            if i - last_peak >= min_gap:
                peaks += 1
                last_peak = i
            in_peak = True
        elif not val:
            in_peak = False
    return peaks
