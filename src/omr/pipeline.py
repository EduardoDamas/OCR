"""
Top-level OMR pipeline: load source → preprocess → recognize all pages.
"""

from typing import List, Callable, Optional
from .preprocessor import (load_source, correct_perspective, deskew, enhance,
                           normalize_width)
from .recognizer import recognize_page, CardResult, MarkResult


def _blank_cards(page_num, n=24, games=8):
    """24 cartelas em branco (tudo em revisão) pra uma folha-imagem combinada —
    a IA preenche na passada híbrida; se a IA estiver off, ficam pra revisão."""
    out = []
    for i in range(n):
        marks = [MarkResult(game=g, choice=None, confidence=0.0,
                            needs_review=True, raw_scores=[0, 0, 0]) for g in range(games)]
        out.append(CardResult(card_index=i, page=page_num, marks=marks,
                              has_review_flags=True, participant=None))
    return out


def _preprocess_keeping_bgr(img):
    """
    Apply geometric corrections in BGR space, then enhance to grayscale.
    Returns (corrected_bgr, enhanced_gray) with matching spatial coordinates.
    """
    img = normalize_width(img)   # make detection resolution-independent
    bgr = correct_perspective(img)
    bgr = deskew(bgr)
    gray = enhance(bgr)   # converts to grayscale + CLAHE
    return bgr, gray


def _detect_format(page_gray, page_bgr) -> str:
    """Decide the card format once, from the first page: 'extra' if the page
    locks onto the bundled EXTRA template, else 'segunda'."""
    try:
        from . import extra_reader
        if extra_reader.available() and page_bgr is not None:
            is_extra, _ = extra_reader.is_extra_page(page_gray)
            return "extra" if is_extra else "segunda"
    except Exception:
        pass
    return "segunda"


def process_file(
    path: str,
    dpi: int = 300,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    fmt: str = "auto",
    ai_confirm_cb: Optional[Callable[[int, float, float], bool]] = None,
) -> List[CardResult]:
    """
    Process a PDF or image file end-to-end.

    `fmt`: "segunda" | "extra" | "auto" (default). "auto" detects the format
    once on the first page and applies it to the whole file (a file is always a
    single format), which avoids attempting registration on every page.

    progress_cb(current, total, message) called at each step if provided.
    Returns flat list of CardResult objects across all pages.
    """
    if progress_cb:
        progress_cb(0, 1, "Carregando arquivo...")

    # ROTEAMENTO POR PÁGINA — um mapa de rodada é normalmente MISTO (ex.: 448
    # págs = ~397 digitais do sistema + ~51 de caneta escaneadas). Cada página
    # vai pra rota certa: digital → leitura direta grátis; foto → IA (se
    # ativada) → OCR local. Antes a decisão era pelo arquivo inteiro (olhando
    # só a pág 1) e as digitais eram pagas na IA à toa.
    is_pdf = str(path).lower().endswith(".pdf")
    digital_pages = set()
    dig_results: List[CardResult] = []
    if fmt in ("auto", "digital") and is_pdf:
        try:
            from . import digital_reader
            digital_pages = set(digital_reader.digital_page_numbers(path))
            if digital_pages:
                if progress_cb:
                    progress_cb(0, 1, f"{len(digital_pages)} página(s) digitais "
                                      "— leitura direta grátis...")
                dig_results = digital_reader.read_digital(path, pages=digital_pages)
        except Exception:
            digital_pages = set()
            dig_results = []

    if is_pdf:
        try:
            import fitz
            _doc = fitz.open(path)
            n_pages = _doc.page_count
            _doc.close()
        except Exception:
            n_pages = 0
    else:
        n_pages = 1
    photo_pages = set(range(1, n_pages + 1)) - digital_pages

    if not photo_pages and dig_results:
        if progress_cb:
            progress_cb(1, 1, f"Concluído: {len(dig_results)} cartelas (digital).")
        dig_results.sort(key=lambda c: (c.page, c.card_index))
        return dig_results

    # Páginas de CANETA — MODO HÍBRIDO (o único que fecha a conta do cliente):
    #   1. OCR local GRÁTIS lê todas as páginas de foto (primeira opinião);
    #   2. IA lê SÓ as cartelas duvidosas (1 leitura) — e a página INTEIRA
    #      quando o local se afogou nela (foto ruim: 8+ cartelas em dúvida);
    #   3. concordância local×IA = confirmado; divergência = revisão humana;
    #   4. as leituras da IA viram amostras de treino do modelo local, que
    #      melhora com o uso (e o custo cai junto).
    # Custo: ~R$0,15-0,40/folha típica vs R$2,60 do tudo-IA com consenso.
    all_results: List[CardResult] = list(dig_results)

    # Folhas ACHATADAS em imagem (a administração junta cartelas de vários
    # vendedores e exporta a folha como imagem). Não têm dado vetorial e o OCR
    # local não entende o layout misto → vão DIRETO pra IA (leitura por imagem).
    # SÓ desvia folha-scan pra IA se a IA estiver LIGADA. Sem IA, a folha vai pro
    # OCR local — folha de CANETA fotografada (que também é imagem sem texto) lê
    # ~ok no local; mandá-la pra rota de scan sem IA gera cartela em BRANCO, que
    # é pior. (Regressão v49-v50: folha de caneta virava scan→IA e, com IA
    # desligada, nada lia — "72 cartelas duvidosas".)
    scan_pages = set()
    if is_pdf:
        try:
            from . import digital_reader, ai_reader
            if ai_reader.available():
                scan_pages = set(digital_reader.scanned_sheet_page_numbers(path)) & photo_pages
        except Exception:
            scan_pages = set()

    if photo_pages:
        images = load_source(path, dpi=dpi)
        total = len(images)
        page_fmt = "auto" if fmt == "ia" else fmt
        local_results: List[CardResult] = []
        for i, img in enumerate(images):
            page_num = i + 1
            if page_num in digital_pages:
                continue  # digital já lida de graça
            if page_num in scan_pages:
                # folha-imagem combinada: não roda OCR local (layout misto que ele
                # não lê); cria 24 cartelas em branco pra IA preencher.
                local_results.extend(_blank_cards(page_num))
                continue
            if progress_cb:
                progress_cb(i, total, f"Leitura local: página {page_num}/{total}...")
            page_bgr, page_gray = _preprocess_keeping_bgr(img)
            if page_fmt == "auto":
                page_fmt = _detect_format(page_gray, page_bgr)
            local_results.extend(recognize_page(page_gray, page_number=page_num,
                                                page_bgr=page_bgr, fmt=page_fmt))

        _hybrid_ai_pass(path, local_results, fmt, progress_cb, ai_confirm_cb, scan_pages)
        all_results.extend(local_results)

    if progress_cb:
        progress_cb(1, 1, f"Concluído: {len(all_results)} cartelas.")
    # arquivo misto: digital + local/IA chegam fora de ordem
    all_results.sort(key=lambda c: (c.page, c.card_index))
    return all_results


# Página com 8+ cartelas duvidosas = foto ruim: o local não é confiável nela,
# a folha inteira vai pra IA (não só as flagadas).
ESCALATE_FLAGS = 8


def _hybrid_ai_pass(path, local_results, fmt, progress_cb, ai_confirm_cb,
                    scan_pages=None) -> None:
    """Etapa 2-4 do híbrido: IA nas duvidosas, merge e auto-treino (in-place)."""
    try:
        from . import ai_reader
        if not ai_reader.available() or fmt not in ("auto", "ia"):
            return
    except Exception:
        return

    scan_pages = set(scan_pages or [])
    by_page = {}
    for c in local_results:
        by_page.setdefault(c.page, []).append(c)
    plan = {}
    for page, cards in by_page.items():
        flagged = [c.card_index for c in cards if c.has_review_flags]
        # Folha ACHATADA em imagem (combinada pela administração) SEMPRE vai
        # inteira pra IA: o OCR local não entende esse layout misto, então não dá
        # pra confiar nas flags dele; a IA lê qualquer marca (●/■/X) na imagem.
        if fmt == "ia" or page in scan_pages or len(flagged) >= ESCALATE_FLAGS:
            plan[page] = "all"
        elif flagged:
            plan[page] = flagged
    if not plan:
        return

    n = sum(24 if v == "all" else len(v) for v in plan.values())
    total = len(local_results)
    # desconta o que já está no cache (leitura anterior): o custo/tempo mostrado
    # é só do que FALTA ler — resume depois de recarregar crédito
    try:
        n_cache = ai_reader.cached_count(path)
    except Exception:
        n_cache = 0
    n_pagar = max(0, n - n_cache)
    # MODO LOTE: 50% do custo, mas assíncrono (minutos, não na hora)
    try:
        lote = ai_reader.use_batch()
    except Exception:
        lote = False
    preco_cartela = 0.055 * (0.5 if lote else 1.0)
    est_brl = n_pagar * preco_cartela   # medido: ~R$1,25 por 24 cartelas (única)
    est_min = 0 if lote else n_pagar * 3.5 / 3 / 60   # lote: tempo é indeterminado
    if ai_confirm_cb is not None:
        try:
            if not ai_confirm_cb(n, est_brl, est_min):
                if progress_cb:
                    progress_cb(1, 1, "IA dispensada — resultado local mantido "
                                      "(cartelas duvidosas ficam na revisão).")
                return
        except Exception:
            pass
    if progress_cb:
        modo = "lote" if lote else "híbrido"
        progress_cb(0, 1, f"IA ({modo}): {n} de {total} cartelas precisam de "
                          f"conferência (~R$ {est_brl:.0f})...")
    try:
        leitura = ai_reader.batch_read_pages if lote else ai_reader.hybrid_read_pages
        ai_marks, usd, erro = leitura(path, plan, progress_cb)
    except Exception as e:
        if progress_cb:
            progress_cb(0, 1, f"IA indisponível ({type(e).__name__}) — "
                              "resultado local mantido...")
        return

    _ch = {"C": 0, "E": 1, "F": 2}
    idx = {(c.page, c.card_index): c for c in local_results}
    for (page, ci), marcas in ai_marks.items():
        card = idx.get((page, ci))
        if card is None:
            continue
        # Página ESCALADA = o local se afogou nela (foto ruim) — comparar IA
        # com um leitor sabidamente ruim ali só geraria divergência em massa e
        # mandaria a folha inteira pra revisão à toa. Nessas, a IA (validada em
        # campo) é a autoridade; revisão fica só onde nem a IA leu ("?").
        escalada = plan.get(page) == "all"
        for g, m in enumerate(card.marks):
            ai_ch = _ch.get(marcas[g]) if g < len(marcas) else None
            if ai_ch is None:
                m.needs_review = True            # nem a IA leu → revisão
            elif escalada:
                m.choice = ai_ch                 # IA manda na folha escalada
                m.confidence = 0.9
                m.needs_review = False
            elif ai_ch == m.choice:
                m.confidence = 0.97              # local e IA concordam
                m.needs_review = False
            elif m.choice is None:
                m.choice = ai_ch                 # local em branco → IA preenche
                m.confidence = 0.85
                m.needs_review = False
            else:
                m.choice = ai_ch                 # divergiram → IA vence, mas
                m.confidence = 0.6               # o humano bate o martelo
                m.needs_review = True
        card.has_review_flags = any(m.needs_review for m in card.marks)

    if progress_cb:
        extra = f" · custo ~R$ {usd*5.4:.2f}" if ai_marks else ""
        fim = f"IA parou ({erro}) — parcial aplicado" if erro else "IA (híbrido) concluída"
        progress_cb(1, 1, fim + extra)

    # auto-treino: cada leitura da IA ensina o modelo local (melhora o grátis
    # com o uso — inclusive caneta preta). Nunca pode derrubar a leitura.
    try:
        from . import trainer
        labels = {}
        for (page, ci), marcas in ai_marks.items():
            labels[(page, ci + 1)] = [_ch.get(m) for m in marcas]
        if labels:
            trainer.add_ai_samples(path, labels)
            if progress_cb:
                progress_cb(1, 1, f"Modelo local treinado com {len(labels)} "
                                  "cartela(s) lidas pela IA.")
    except Exception:
        pass
