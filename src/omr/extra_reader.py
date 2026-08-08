"""
EXTRA-format reader ("BOLÃO EXTRA / FOLHA Nº ...") — ECC template registration.

The EXTRA sheets carry a *fixed* printed form (header, team names, table lines);
only the marks and the photo distortion change page to page. Re-finding faint
grid lines per page is unreliable (classical CV topped out ~77%, far worse on
faint carbon copies). Instead we treat localization as REGISTRATION:

  1. A bundled template (models/extra_template.npz) holds a few well-aligned
     EXEMPLAR pages, each with its 24×8×3 sample-box grid pre-computed.
  2. Each input page is aligned to whichever exemplar gives the best ECC
     correlation (coarse-to-fine pyramid, affine → homography). ECC is
     intensity-based so it locks even when the printed lines have faded.
  3. The exemplar's known grid is warped into the page and the 3 option boxes
     per game are sampled for ink; an optional ONNX classifier refines the call.

Measured on folhas 11-20 vs the client gabarito: 95% (ink) / 96% (model),
versus 50% for the previous per-card line-detection reader. The ECC correlation
`cc` doubles as a format detector (high ⇒ this IS an EXTRA page).

Free/offline: OpenCV + NumPy only at runtime (the optional model runs through
cv2.dnn, like mark_cnn.onnx — no torch/sklearn needed to read).
"""

import os
import numpy as np
import cv2
from typing import List, Optional, Tuple

from .recognizer import MarkResult, CardResult, _scores_to_choice, CONFIDENCE_LOW

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "models", "extra_template.npz")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "extra_marks.onnx")

# Box centres as a fraction of table width, and ink threshold — calibrated on the
# EXTRA layout (Casa far-left, Empate centre divider, Fora far-right).
BOXF = (0.047, 0.50, 0.955)
INK_THR = 0.13
LEVELS = [0.18, 0.30, 0.50, 0.75, 1.0]
LOCK_CC = 0.45              # cc above this = a reliable lock / an EXTRA page
CC_MARGINAL = 0.50         # a page that locks only *marginally* (cc within ~0.05
                           # of the floor) is registered too shakily to trust: the
                           # warp drifts and boxes sample off the real marks. Flag
                           # the whole page for review. Measured: catches the silent
                           # errors on the two shakiest unseen pages (cc 0.47/0.49)
                           # with zero false flags on the labelled 11-20 (all ≥0.59).
MIN_GRID_DENSITY = 0.045    # printed-content density the warped grid must cover;
                           # below this the grid landed off the form (bad warp) →
                           # flag the whole page for manual review (never emit it
                           # silently — wrong values in a money context are unsafe)
GAMES = 8
N_CARDS = 24

_template = None
_net = None


# ── template + model loading ────────────────────────────────────────────────

def available() -> bool:
    return os.path.exists(TEMPLATE_PATH)


def _load_template():
    global _template
    if _template is None:
        z = np.load(TEMPLATE_PATH)
        n = int(z["n"])
        ex = []
        for i in range(n):
            img = z[f"img_{i}"]
            ex.append({
                "img": img,
                "pyr": {lvl: cv2.resize(img, None, fx=lvl, fy=lvl) for lvl in LEVELS},
                "pts": z[f"pts_{i}"],
                "cent": z[f"cent_{i}"],
                "bbox": tuple(float(v) for v in z[f"bbox_{i}"]),
            })
        _template = {"exemplars": ex, "ref_win": z["ref_win"]}
    return _template


def _load_model():
    global _net
    if _net is None and os.path.exists(MODEL_PATH):
        _net = cv2.dnn.readNetFromONNX(MODEL_PATH)
    return _net


# ── geometry helpers (mirrors the validated scratchpad pipeline) ────────────

def _content_mask(gray):
    clahe = cv2.createCLAHE(2.5, (8, 8)).apply(gray)
    return cv2.adaptiveThreshold(cv2.GaussianBlur(clahe, (3, 3), 0), 255,
        cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 10)


def _form_bbox_proj(gray):
    """Dense-content bbox via smoothed row/column projections. The printed form
    dominates the projections; sparse floor lines/shadows fall below threshold —
    robust where the blob method bridges the form to the background."""
    cm = (_content_mask(gray) > 0).astype(np.float32)
    h, w = cm.shape
    col = cv2.GaussianBlur(cm.sum(0).reshape(1, -1), (31, 1), 0).ravel()
    row = cv2.GaussianBlur(cm.sum(1).reshape(-1, 1), (1, 31), 0).ravel()
    cx = np.where(col > 0.30 * col.max())[0]
    ry = np.where(row > 0.30 * row.max())[0]
    if len(cx) < 2 or len(ry) < 2:
        return (0, 0, w, h)
    return (int(cx[0]), int(ry[0]), int(cx[-1] - cx[0]), int(ry[-1] - ry[0]))


def _form_bbox(gray):
    """Form bounding box. Largest dense blob normally; if that over-merges with
    the background (spans the whole frame from the origin — seen on photos with
    strong floor texture, e.g. folha 30) fall back to the projection method."""
    h, w = gray.shape[:2]
    bw = _content_mask(gray)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 35))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return _form_bbox_proj(gray)
    x, y, fw, fh = cv2.boundingRect(max(cnts, key=cv2.contourArea))
    if x <= 2 and y <= 2 and fw > 0.97 * w and fh > 0.88 * h:
        return _form_bbox_proj(gray)
    return (x, y, fw, fh)


def _find_cuts(proj, n, lo, hi, win):
    cuts = []
    for k in range(1, n):
        guess = lo + (hi - lo) * k / n
        a = int(max(lo, guess - win)); b = int(min(hi, guess + win))
        if b <= a:
            cuts.append(int(guess)); continue
        cuts.append(a + int(np.argmin(proj[a:b])))
    return cuts


def _card_grid(gray, fb):
    """Per-page projection grid → 24 card boxes (used only to seed registration)."""
    x0, y0, fw, fh = fb
    F = (_content_mask(gray)[y0:y0 + fh, x0:x0 + fw] > 0).astype(np.float32)
    rs = cv2.GaussianBlur(F.sum(1).reshape(-1, 1), (1, 9), 0).ravel()
    thr = rs.max() * 0.35
    top = 0; seen = False
    for i in range(fh):
        if rs[i] > thr: seen = True
        elif seen and rs[i] < thr * 0.5: top = i; break
    bot = fh - 1
    for i in range(fh - 1, top, -1):
        if rs[i] > thr * 0.6: bot = i; break
    colsum = cv2.GaussianBlur(F[top:bot].sum(0).reshape(-1, 1), (1, 9), 0).ravel()
    vcuts = [0] + _find_cuts(colsum, 4, fw * 0.02, fw * 0.98, fw * 0.06) + [fw]
    hsum = cv2.GaussianBlur(F[top:bot].sum(1).reshape(-1, 1), (1, 9), 0).ravel()
    hcuts = [0] + _find_cuts(hsum, 6, 0, bot - top, (bot - top) * 0.05) + [bot - top]
    cent = []
    for r in range(6):
        for c in range(4):
            cx = x0 + (vcuts[c] + vcuts[c + 1]) / 2.0
            cy = y0 + top + (hcuts[r] + hcuts[r + 1]) / 2.0
            cent.append([cx, cy])
    return np.array(cent, np.float32) if len(cent) == 24 else None


def _ecc_prep(gray):
    return cv2.GaussianBlur(cv2.createCLAHE(2.0, (8, 8)).apply(gray), (5, 5), 0).astype(np.float32) / 255.0


def _warp_pts(pts, W):
    arr = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    if W.shape == (3, 3):
        return cv2.perspectiveTransform(arr, W).reshape(-1, 2)
    return cv2.transform(arr, W).reshape(-1, 2)


def _ecc_affine(pg, seed, ref_pyr):
    W = seed.copy().astype(np.float32); cc = -1.0
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 120, 1e-6)
    for lvl in LEVELS:
        pgL = cv2.resize(pg, None, fx=lvl, fy=lvl)
        Ws = W.copy(); Ws[:, 2] *= lvl
        try:
            cc, Ws = cv2.findTransformECC(ref_pyr[lvl], pgL, Ws, cv2.MOTION_AFFINE, crit, None, 5)
            W = Ws.copy(); W[:, 2] /= lvl
        except cv2.error:
            pass
    return W, cc


def _ecc_homography(pg, W2, ref_pyr):
    H = np.eye(3, dtype=np.float32); H[:2] = W2; cc = -1.0
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 120, 1e-6)
    for lvl in [0.30, 0.50, 0.75, 1.0]:
        pgL = cv2.resize(pg, None, fx=lvl, fy=lvl)
        Hs = H.copy(); Hs[:2, 2] *= lvl; Hs[2, :2] /= lvl
        try:
            cc, Hs = cv2.findTransformECC(ref_pyr[lvl], pgL, Hs, cv2.MOTION_HOMOGRAPHY, crit, None, 5)
            H = Hs.copy(); H[:2, 2] /= lvl; H[2, :2] *= lvl
        except cv2.error:
            pass
    return H, cc


def _register_to_exemplar(gray, pg, fb, ex):
    """Register a prepped page to one exemplar. Returns (page_pts(576,2), cc)."""
    ex0, ey0, efw, efh = ex["bbox"]; px0, py0, pfw, pfh = fb
    sx = pfw / efw; sy = pfh / efh
    seeds = [np.array([[sx, 0, px0 - ex0 * sx], [0, sy, py0 - ey0 * sy]], np.float32),
             np.array([[1, 0, px0 - ex0], [0, 1, py0 - ey0]], np.float32)]
    page_cent = _card_grid(gray, fb)
    if page_cent is not None:
        aff, _ = cv2.estimateAffine2D(ex["cent"], page_cent, method=cv2.RANSAC)
        if aff is not None:
            seeds.append(aff.astype(np.float32))
    best_W, best_cc = seeds[0], -2.0
    for s in seeds:
        W, cc = _ecc_affine(pg, s, ex["pyr"])
        if cc > best_cc:
            best_W, best_cc = W, cc
    H, cch = _ecc_homography(pg, best_W, ex["pyr"])
    if cch > best_cc:
        best_W, best_cc = H, cch
    return _warp_pts(ex["pts"], best_W), best_cc


GOOD_CC = 0.70             # a clearly-confident lock — stop trying more exemplars


def register_page(page_gray) -> Tuple[Optional[np.ndarray], float, int]:
    """Align a page to the best-matching bundled exemplar.
    Returns (sample_points(576,2) | None, best_cc, exemplar_index).
    Exemplars are tried in order and the search stops early once one locks
    confidently (GOOD_CC), so clean pages are fast; faint pages try them all."""
    tpl = _load_template()
    pg = _ecc_prep(page_gray)
    fb = _form_bbox(page_gray)
    best = (None, -2.0, -1)
    for i, ex in enumerate(tpl["exemplars"]):
        pts, cc = _register_to_exemplar(page_gray, pg, fb, ex)
        if cc > best[1]:
            best = (pts, cc, i)
        if cc >= GOOD_CC:
            break
    return best


# ── Fast format probe ────────────────────────────────────────────────────────
# Deciding the reader for a file only needs page 1, but a full non-locking
# register_page (SEGUNDA page) costs ~60s: it exhausts all 3 exemplars × seeds
# with affine + homography refinement because it never reaches GOOD_CC.  The
# probe below does affine-only ECC (no homography), coarse levels, few
# iterations, with an early-out — ~3s worst case — purely to FAST-REJECT the
# common SEGUNDA page.  Anything it can't confidently reject is confirmed by the
# accurate register_page, so the reading path and EXTRA accuracy are untouched.
PROBE_LEVELS = (0.18, 0.30, 0.50)
PROBE_ITERS = 25
PROBE_EARLYOUT = 0.60      # probe cc ≥ this ⇒ certainly EXTRA — stop probing
PROBE_REJECT = 0.52        # probe cc < this ⇒ certainly not EXTRA — skip the full
                           # search. Measured page-1 probe: coluna/SEGUNDA family
                           # (incl. QUINTA) ≤0.513, EXTRA ≥0.553.
EXTRA_DETECT_CC = 0.55     # a file is EXTRA only on a CLEAR confirm lock. Measured
                           # page-1 confirm cc: coluna family ≤0.457, EXTRA ≥0.672.
                           # (Deliberately well above LOCK_CC 0.45 so the coluna
                           # "Bolão de Coluna" maps never misroute to the slow
                           # EXTRA reader — the user's production is all coluna.)


def _probe_extra_cc(page_gray) -> float:
    """Cheap affine-only ECC correlation to the best exemplar, with early-out."""
    tpl = _load_template()
    pg = _ecc_prep(page_gray)
    fb = _form_bbox(page_gray)
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, PROBE_ITERS, 1e-4)
    page_cent = _card_grid(page_gray, fb)
    best = -2.0
    for ex in tpl["exemplars"]:
        ex0, ey0, efw, efh = ex["bbox"]; px0, py0, pfw, pfh = fb
        sx = pfw / efw; sy = pfh / efh
        seeds = [np.array([[sx, 0, px0 - ex0 * sx], [0, sy, py0 - ey0 * sy]], np.float32)]
        if page_cent is not None:
            aff, _ = cv2.estimateAffine2D(ex["cent"], page_cent, method=cv2.RANSAC)
            if aff is not None:
                seeds.append(aff.astype(np.float32))
        for s in seeds:
            W = s.copy(); cc = -1.0
            for lvl in PROBE_LEVELS:
                pgL = cv2.resize(pg, None, fx=lvl, fy=lvl)
                Ws = W.copy(); Ws[:, 2] *= lvl
                try:
                    cc, Ws = cv2.findTransformECC(ex["pyr"][lvl], pgL, Ws,
                                                  cv2.MOTION_AFFINE, crit, None, 5)
                    W = Ws.copy(); W[:, 2] /= lvl
                except cv2.error:
                    pass
            if cc > best:
                best = cc
            if best >= PROBE_EARLYOUT:
                return best
    return best


def is_extra_page(page_gray) -> Tuple[bool, float]:
    """Format probe: does this page lock onto the EXTRA template?
    Two-stage for speed — a cheap affine-only probe fast-rejects the common
    SEGUNDA page (~3s) instead of a full 3-exemplar homography search (~60s);
    only a page the probe can't reject is confirmed by the accurate
    register_page.  Returns (is_extra, best_cc).  Used once per file."""
    if not available():
        return False, -1.0
    pc = _probe_extra_cc(page_gray)
    if pc < PROBE_REJECT:
        return False, pc
    _, cc, _ = register_page(page_gray)
    return cc >= EXTRA_DETECT_CC, cc


# ── ink sampling + classification ───────────────────────────────────────────

def _ink_mask(bgr):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return ((g < 140) | ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40) & (hsv[:, :, 2] < 250))).astype(np.uint8)


def _feats(gray, ink, dark, sat, cx, cy, hw, hh):
    x0 = max(0, int(cx - hw)); x1 = int(cx + hw)
    y0 = max(0, int(cy - hh)); y1 = int(cy + hh)
    gi = gray[y0:y1, x0:x1]; ii = ink[y0:y1, x0:x1]
    di = dark[y0:y1, x0:x1]; si = sat[y0:y1, x0:x1]
    if ii.size == 0:
        return [0.0] * 6, 0.0
    inkf = float(ii.mean())
    nlab, _, st, _ = cv2.connectedComponentsWithStats(ii, 8)
    blob = (max([st[k, 4] for k in range(1, nlab)], default=0) / ii.size)
    return [inkf, float(di.mean()), float(si.mean()), blob,
            gi.mean() / 255.0, gi.std() / 255.0], inkf


def _classify_card(gray, ink, dark, sat, pts_card, ref_win, net):
    """pts_card: (8,3,2) box centres for one card. Returns list[MarkResult]."""
    hw, hh = float(ref_win[0]), float(ref_win[1])
    marks = []
    for g in range(GAMES):
        feats3 = []; inks = []
        for k in range(3):
            cx, cy = pts_card[g, k]
            bf, inkf = _feats(gray, ink, dark, sat, cx, cy, hw, hh)
            feats3.append(bf); inks.append(inkf)
        if net is not None:
            mean_o = [(sum(inks) - inks[k]) / 2.0 for k in range(3)]
            vec = np.array(feats3[0] + feats3[1] + feats3[2] +
                           [inks[k] - mean_o[k] for k in range(3)], np.float32).reshape(1, -1)
            net.setInput(vec)
            prob = net.forward().ravel()
            choice = int(np.argmax(prob)); conf = float(prob[choice])
            blank = inks[choice] < INK_THR
            marks.append(MarkResult(game=g, choice=(None if blank else choice),
                                    confidence=(0.0 if blank else round(conf, 3)),
                                    needs_review=(blank or conf < CONFIDENCE_LOW),
                                    raw_scores=[float(p) for p in prob]))
        else:
            choice, conf, review = _scores_to_choice(inks)
            marks.append(MarkResult(game=g, choice=choice, confidence=conf,
                                    needs_review=review, raw_scores=inks))
    return marks


def _grid_density(page_gray, pts) -> float:
    """Printed-content density inside the warped grid's bounding box. A correctly
    located grid covers the dense form (~high); a mislocated one lands on empty
    floor (~0). Used to reject geometrically-wrong registrations."""
    h, w = page_gray.shape[:2]
    x0 = int(max(0, pts[:, 0].min())); x1 = int(min(w, pts[:, 0].max()))
    y0 = int(max(0, pts[:, 1].min())); y1 = int(min(h, pts[:, 1].max()))
    if x1 - x0 < 10 or y1 - y0 < 10:
        return 0.0
    reg = _content_mask(page_gray)[y0:y1, x0:x1]
    return float((reg > 0).mean()) if reg.size else 0.0


def _registration_ok(page_gray, pts) -> bool:
    """True if the warped grid is geometrically plausible (in-bounds + lands on
    the printed form). False ⇒ bad warp; the page is flagged for manual review."""
    h, w = page_gray.shape[:2]
    if pts[:, 0].min() < -w * 0.02 or pts[:, 0].max() > w * 1.02:
        return False
    if pts[:, 1].min() < -h * 0.02 or pts[:, 1].max() > h * 1.02:
        return False
    return _grid_density(page_gray, pts) >= MIN_GRID_DENSITY


# ── public entry point ──────────────────────────────────────────────────────

def recognize_page_extra(page_gray, page_bgr, page_number: int
                         ) -> Optional[Tuple[List[CardResult], float]]:
    """Read all 24 cards on an EXTRA-format page.
    Returns (cards, cc) or None if the page can't be located (not EXTRA).
    If the registration is geometrically implausible (grid off the form), every
    card is flagged needs_review so a wrong page is never emitted silently."""
    if not available() or page_bgr is None:
        return None
    pts, cc, _ = register_page(page_gray)
    if pts is None:
        return None
    # Trust the page only if the warp is geometrically plausible AND the lock is
    # confident; a merely-marginal lock (cc near the floor) drifts per-card and
    # produces confident-but-wrong reads, so route the whole page to review.
    page_ok = _registration_ok(page_gray, pts) and cc >= CC_MARGINAL
    tpl = _load_template()
    ref_win = tpl["ref_win"]
    net = _load_model()
    ink = _ink_mask(page_bgr)
    dark = (cv2.cvtColor(page_bgr, cv2.COLOR_BGR2GRAY) < 140).astype(np.uint8)
    hsv = cv2.cvtColor(page_bgr, cv2.COLOR_BGR2HSV)
    sat = ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 40) & (hsv[:, :, 2] < 250)).astype(np.uint8)
    P = pts.reshape(N_CARDS, GAMES, 3, 2)
    cards = []
    for ci in range(N_CARDS):
        marks = _classify_card(page_gray, ink, dark, sat, P[ci], ref_win[ci], net)
        if not page_ok:                       # bad registration → flag the whole card
            marks = [MarkResult(game=m.game, choice=m.choice, confidence=m.confidence,
                                needs_review=True, raw_scores=m.raw_scores) for m in marks]
        cards.append(CardResult(card_index=ci, page=page_number, marks=marks,
                                has_review_flags=(not page_ok) or any(m.needs_review for m in marks)))
    return cards, cc
