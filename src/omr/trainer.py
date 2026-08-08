"""
In-app incremental training (pure NumPy — no torch/sklearn, runs inside the exe).

Flow: user picks an image/PDF of a page + a CSV with the correct answers, presses
"Treinar".  We locate the cards, extract a small feature vector per game, pair it
with the CSV label, append everything to a persistent dataset, and (re)train a
tiny MLP.  When a trained user-model exists, recognize_card() uses it to REFINE
the choice on games that are located but ambiguous.

What it improves: mark classification (Casa/Empate/Fora) — including new mark
styles.  What it can't fix: localization on badly-distorted photos (a card the
detector can't find has no features to learn from; those stay flagged).

Persistence lives next to the executable (survives, unlike the bundled models/).
"""

import os
import sys
import re
import numpy as np

from .detector import get_game_cells, LAYOUT, detect_cards
from .recognizer import (_build_blue_mask, _build_dark_mask,
                         _crop_cell, _mask_density)

# 0=Casa, 1=Empate, 2=Fora
N_FEATURES = 9          # 3 option cells × 3 ink masks (blue, coloured, dark)
N_CLASSES = 3
_MODEL_CACHE = {"mtime": None, "model": None}


# ── persistent paths (next to the exe when frozen) ───────────────────────────

def _data_dir():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    d = os.path.join(base, "data")
    os.makedirs(d, exist_ok=True)
    return d


def dataset_path():
    # v2: o dataset v1 foi ENVENENADO em campo — o usuário treinou várias vezes
    # com a saída bruta do próprio programa (lixo rotulado com confiança), e o
    # modelo passou a sobrescrever leituras erradas SEM flag de revisão.
    # Trocar o nome do arquivo dá recomeço limpo automático em toda máquina;
    # daqui em diante as amostras vêm do auto-treino da IA (rótulos bons).
    # v3: o v2 foi contaminado pelo auto-treino com leituras de IA feitas em
    # RECORTES DESALINHADOS (bug do banner na folha RODADA, 16/jul) — rótulos
    # errados de novo. Recomeço limpo; com o recorte corrigido, o auto-treino
    # daqui em diante alimenta com rótulos bons.
    return os.path.join(_data_dir(), "train_dataset_v3.npz")


def model_path():
    return os.path.join(_data_dir(), "user_marks_v3.npz")


# ── feature extraction (identical for train and inference) ───────────────────

def _colored_ink(bgr):
    """Coloured handwritten ink of any hue, dropping black print (pairs the ML reader)."""
    import cv2
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    return ((s > 50) & (v > 35) & (v < 250)).astype(np.uint8) * 255


def game_features(card, layout: dict = None) -> np.ndarray:
    """Return an (8, N_FEATURES) array — one feature row per game.

    Per game, for each of the 3 option boxes (Casa/Empate/Fora) we measure ink
    density in three masks (blue, coloured, dark).  Uses the same cell geometry
    and masks as the reader, so training and inference see identical features.
    """
    cfg = layout or LAYOUT
    games = cfg["games_per_card"]
    cells = get_game_cells(card, cfg)

    dark = _build_dark_mask(card.image)
    if card.image_bgr is not None:
        blue = _build_blue_mask(card.image_bgr)
        col = _colored_ink(card.image_bgr)
    else:
        blue = col = None

    by_game = [[] for _ in range(games)]
    for c in cells:
        by_game[c.game].append(c)

    feats = np.zeros((games, N_FEATURES), np.float32)
    for g in range(games):
        gcells = sorted(by_game[g], key=lambda c: c.option)
        for opt, cell in enumerate(gcells[:3]):
            b = _mask_density(_crop_cell(blue, cell)) if blue is not None else 0.0
            c_ = _mask_density(_crop_cell(col, cell)) if col is not None else 0.0
            d = _mask_density(_crop_cell(dark, cell))
            feats[g, opt * 3:opt * 3 + 3] = (b, c_, d)
    return feats


# ── CSV parsing (tolerant of the various gabarito layouts) ───────────────────

_WORD_CHOICE = {"C": 0, "CASA": 0, "E": 1, "EMPATE": 1, "F": 2, "FORA": 2}


def _tok_choice(tok):
    """Map a cell to a choice: C/Casa→0, E/Empate→1, F/Fora→2, else None."""
    return _WORD_CHOICE.get(str(tok).strip().upper())


def _load_rows(path: str):
    """Yield each row as a list of cell strings, from a CSV or an .xlsx/.xls file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            yield ["" if c is None else str(c) for c in row]
        wb.close()
    else:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            for raw in f:
                line = raw.rstrip("\n\r")
                if line.strip():
                    yield re.split(r"[;,\t]", line)


def parse_labels(path: str) -> dict:
    """Return {(page_1based, card_1based): [c0..c7]} with c in {0,1,2}.

    Reads CSV or Excel (.xlsx).  Per row: the choice cells (C/E/F or
    Casa/Empate/Fora, in order) give the 8 games; the integers in the cells
    BEFORE the first choice give (page, card) — the last two, or a single one as
    (1, card).  Header rows (no choice cells) are skipped automatically, so both
    the 'pag: 1 C3;C;E;...' style and the app's own tabular export work.
    """
    labels = {}
    for row in _load_rows(path):
        cells = [c for c in row if str(c).strip() != ""]
        if not cells:
            continue
        choice_idx = [i for i, c in enumerate(cells) if _tok_choice(c) is not None]
        if not choice_idx:
            continue
        first = choice_idx[0]
        choices = [_tok_choice(cells[i]) for i in choice_idx][:8]
        nums = []
        for c in cells[:first]:
            nums += [int(n) for n in re.findall(r"\d+", str(c))]
        if len(nums) >= 2:
            page, card = nums[-2], nums[-1]
        elif len(nums) == 1:
            page, card = 1, nums[0]
        else:
            continue
        labels[(page, card)] = (choices + [None] * 8)[:8]
    return labels


# ── card extraction from an image/PDF page ───────────────────────────────────

def _pages_cards(path: str):
    """Yield (page_1based, [CardRegion,...]) for each page of an image or PDF."""
    from .preprocessor import load_source
    from .pipeline import _preprocess_keeping_bgr
    imgs = load_source(path, dpi=300)
    for i, img in enumerate(imgs):
        bgr, gray = _preprocess_keeping_bgr(img)
        yield i + 1, detect_cards(gray, page_bgr=bgr)


def build_samples(image_path: str, csv_path: str):
    """Extract (X, y) training samples pairing located games with CSV labels.

    Returns (X (M,N_FEATURES), y (M,), n_cards_matched, n_pages)."""
    labels = parse_labels(csv_path)
    if not labels:
        raise ValueError("Nenhum gabarito válido encontrado no CSV "
                         "(esperado C/E/F por jogo).")
    Xs, ys = [], []
    n_cards = n_pages = 0
    for page, cards in _pages_cards(image_path):
        n_pages += 1
        for card in cards:
            lab = labels.get((page, card.index + 1))
            if lab is None:
                lab = labels.get((1, card.index + 1))   # single-page CSV fallback
            if lab is None:
                continue
            feats = game_features(card)
            for g in range(8):
                if lab[g] is None:
                    continue
                Xs.append(feats[g])
                ys.append(lab[g])
            n_cards += 1
    if not Xs:
        raise ValueError("Nenhuma cartela do gabarito bateu com a imagem. "
                         "Confira se o CSV corresponde a esta página.")
    return np.asarray(Xs, np.float32), np.asarray(ys, np.int64), n_cards, n_pages


# ── the tiny MLP (9 → 16 → 3), trained in NumPy ──────────────────────────────

def _train_mlp(X, y, hidden=16, epochs=400, lr=0.05, l2=1e-4, seed=0):
    rng = np.random.RandomState(seed)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xn = (X - mu) / sd
    n, d = Xn.shape
    Y = np.eye(N_CLASSES)[y]
    W1 = rng.randn(d, hidden).astype(np.float32) * np.sqrt(2.0 / d)
    b1 = np.zeros(hidden, np.float32)
    W2 = rng.randn(hidden, N_CLASSES).astype(np.float32) * np.sqrt(2.0 / hidden)
    b2 = np.zeros(N_CLASSES, np.float32)
    for _ in range(epochs):
        Z1 = Xn @ W1 + b1
        A1 = np.maximum(Z1, 0)
        logits = A1 @ W2 + b2
        logits -= logits.max(1, keepdims=True)
        P = np.exp(logits); P /= P.sum(1, keepdims=True)
        dL = (P - Y) / n
        gW2 = A1.T @ dL + l2 * W2
        gb2 = dL.sum(0)
        dA1 = dL @ W2.T
        dZ1 = dA1 * (Z1 > 0)
        gW1 = Xn.T @ dZ1 + l2 * W1
        gb1 = dZ1.sum(0)
        W1 -= lr * gW1; b1 -= lr * gb1
        W2 -= lr * gW2; b2 -= lr * gb2
    # training accuracy
    A1 = np.maximum(Xn @ W1 + b1, 0)
    pred = (A1 @ W2 + b2).argmax(1)
    acc = float((pred == y).mean())
    return dict(W1=W1, b1=b1, W2=W2, b2=b2, mu=mu.astype(np.float32),
                sd=sd.astype(np.float32)), acc


def train_from_file(image_path: str, csv_path: str) -> dict:
    """Add this labelled page to the dataset and retrain. Returns stats dict."""
    X, y, n_cards, n_pages = build_samples(image_path, csv_path)

    dp = dataset_path()
    if os.path.exists(dp):
        prev = np.load(dp)
        X = np.vstack([prev["X"], X])
        y = np.concatenate([prev["y"], y])
    np.savez_compressed(dp, X=X, y=y)

    model, acc = _train_mlp(X, y)
    np.savez_compressed(model_path(), n_samples=len(y), **model)
    _MODEL_CACHE["mtime"] = None      # force reload
    return {
        "cards_matched": n_cards,
        "pages": n_pages,
        "new_games": int(len(y)),     # note: cumulative after vstack
        "total_samples": int(len(y)),
        "train_accuracy": acc,
        "classes": [int((y == k).sum()) for k in range(N_CLASSES)],
    }


def _pages_cards_filtered(path: str, pages):
    """Como _pages_cards, mas renderiza SÓ as páginas pedidas (num mapa de 448
    págs o auto-treino usa só as ~dezenas lidas pela IA)."""
    import cv2
    import numpy as np
    from .pipeline import _preprocess_keeping_bgr

    if str(path).lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(path)
        try:
            for pi in range(doc.page_count):
                if (pi + 1) not in pages:
                    continue
                pix = doc[pi].get_pixmap(matrix=fitz.Matrix(300 / 72.0, 300 / 72.0),
                                         colorspace=fitz.csRGB)
                img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height,
                                                                   pix.width, 3)
                bgr, gray = _preprocess_keeping_bgr(
                    cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                yield pi + 1, detect_cards(gray, page_bgr=bgr)
        finally:
            doc.close()
    else:
        img = cv2.imread(path)
        if img is not None and 1 in pages:
            bgr, gray = _preprocess_keeping_bgr(img)
            yield 1, detect_cards(gray, page_bgr=bgr)


def add_ai_samples(image_path: str, labels: dict) -> int:
    """AUTO-TREINO do modo híbrido: cada cartela lida pela IA vira amostra
    rotulada do modelo local — o método grátis melhora com o uso (inclusive
    caneta preta, ponto fraco histórico) e o custo da IA cai junto.

    labels: {(página_1based, cartela_1based): [8 escolhas 0/1/2/None]}.
    Retorna o nº de jogos adicionados ao dataset."""
    Xs, ys = [], []
    pages = {p for p, _ in labels.keys()}
    for page, cards in _pages_cards_filtered(image_path, pages):
        for card in cards:
            lab = labels.get((page, card.index + 1))
            if not lab:
                continue
            feats = game_features(card)
            for g in range(8):
                if g < len(lab) and lab[g] is not None:
                    Xs.append(feats[g])
                    ys.append(lab[g])
    if not Xs:
        return 0

    X = np.asarray(Xs, np.float32)
    y = np.asarray(ys, np.int64)
    dp = dataset_path()
    if os.path.exists(dp):
        prev = np.load(dp)
        X = np.vstack([prev["X"], X])
        y = np.concatenate([prev["y"], y])
    np.savez_compressed(dp, X=X, y=y)

    model, _ = _train_mlp(X, y)
    np.savez_compressed(model_path(), n_samples=len(y), **model)
    _MODEL_CACHE["mtime"] = None
    return len(Xs)


# ── inference-time use ───────────────────────────────────────────────────────

def available() -> bool:
    return os.path.exists(model_path())


def _load_model():
    mp = model_path()
    if not os.path.exists(mp):
        return None
    mtime = os.path.getmtime(mp)
    if _MODEL_CACHE["mtime"] != mtime:
        d = np.load(mp)
        _MODEL_CACHE["model"] = {k: d[k] for k in ("W1", "b1", "W2", "b2", "mu", "sd")}
        _MODEL_CACHE["mtime"] = mtime
    return _MODEL_CACHE["model"]


def predict_games(feats: np.ndarray):
    """feats (G,N_FEATURES) → (choices (G,), confs (G,)) or (None, None) if no model."""
    m = _load_model()
    if m is None:
        return None, None
    Xn = (feats - m["mu"]) / m["sd"]
    A1 = np.maximum(Xn @ m["W1"] + m["b1"], 0)
    logits = A1 @ m["W2"] + m["b2"]
    logits -= logits.max(1, keepdims=True)
    P = np.exp(logits); P /= P.sum(1, keepdims=True)
    return P.argmax(1), P.max(1)


def sample_count() -> int:
    dp = dataset_path()
    if not os.path.exists(dp):
        return 0
    try:
        return int(len(np.load(dp)["y"]))
    except Exception:
        return 0
