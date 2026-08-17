"""
Direct reader for DIGITAL (vector) PDFs — e.g. the "BOLÃO ESCAPA" sheets the
client's system generates.  These are not photos: the marks (●) and team names
are real text in the PDF, so we read them straight from the file with ~100 %
accuracy and zero review, instead of running image OCR.

Layout (1-X-2 per game): the ● sits far-left (before the home team) = Casa,
in the middle (between the two teams) = Empate, or far-right (after the away
team) = Fora.  24 cartelas per page (4 columns × 6 rows), 8 games each.
"""

import re
from typing import List
from .recognizer import MarkResult, CardResult

# Rodapé das cartelas "BOLÃO ESCAPA": "Nº - NOME" à esquerda (o código fica num
# elemento separado, à direita). Ex.: "1 - IVAN GILMAR", "94 - OS MOREN". Captura
# o NOME (grupo 1) pra levar pro Excel/site, sem depender de "NOME:" no cabeçalho.
_FOOTER_NOME_RE = re.compile(r"^\s*\d+\s*-\s*(.+?)\s*$")


# Rótulo das digitais. O cliente pediu duas coisas em momentos diferentes:
#   (1) "fica melhor de organizar aqui" → identificar por PÁGINA + Nº da cartela
#       pra achar a página rápido quando dá erro;
#   (2) depois: mostrar o NOME do apostador (o PDF digital tem "NOME: ...").
# Não é um OU o outro — o rótulo mostra OS DOIS: "Pág 3 #2 — João Silva". Assim
# o nome aparece E dá pra localizar a página/cartela pelo mesmo rótulo.
#   INCLUDE_NAMES=True  → "Pág X #Y — Nome" (nome + localização)
#   INCLUDE_NAMES=False → "Pág X #Y"        (só localização, sem nome)
# DECISÃO DO CLIENTE (ago 2026): manter FALSE. Com o nome ligado a organização
# dele "desconfigurava tudo" — ele prefere só Pág+Nº pra localizar. O rótulo
# combinado continua pronto (é só flipar), mas fica desligado.
INCLUDE_NAMES = False


def _gap_split(vals, k):
    """Split sorted values into k clusters at the (k-1) largest gaps; return centers."""
    vals = sorted(vals)
    if len(vals) <= k:
        return vals[:]
    gaps = sorted(((vals[i + 1] - vals[i], i) for i in range(len(vals) - 1)),
                  reverse=True)
    cuts = sorted(i for _, i in gaps[:k - 1])
    centers, start = [], 0
    for c in cuts:
        seg = vals[start:c + 1]
        centers.append(sum(seg) / len(seg))
        start = c + 1
    seg = vals[start:]
    centers.append(sum(seg) / len(seg))
    return centers


def _nearest(v, centers):
    return min(range(len(centers)), key=lambda i: abs(v - centers[i]))


def _filled_squares(p):
    """Centres of the small filled rectangles used as marks (■ checkbox style)."""
    out = []
    for d in p.get_drawings():
        fill = d.get("fill")
        if fill is None or fill == (1, 1, 1):     # need a real (non-white) fill
            continue
        for it in d.get("items", []):
            if it[0] == "re":
                r = it[1]
                w, h = r.width, r.height
                if 2 < w < 16 and 2 < h < 16 and abs(w - h) < 12:
                    out.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return out


def _filled_dots(p):
    """Centres of small FILLED vector CIRCLES used as marks — o ● desenhado em
    vetor (curvas Bézier), não glifo de texto nem ■ retângulo. Ex.: PDFs do
    'BOLÃO DA ALEGRIA': marca = círculo preenchido pequeno (~5px), opção não
    marcada = círculo só de contorno (sem fill). A marca tem fill + curva ('c')
    e bounding box pequeno e ~quadrado."""
    out = []
    for d in p.get_drawings():
        fill = d.get("fill")
        if fill is None or fill == (1, 1, 1):     # precisa de fill real (não branco)
            continue
        if not any(it[0] == "c" for it in d.get("items", [])):  # círculo = tem curva
            continue
        r = d["rect"]
        w, h = r.width, r.height
        if 2 < w < 16 and 2 < h < 16 and abs(w - h) < 3:
            out.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    return out


def _x_marks(p):
    """Centres of the 'X' letter used as a mark (ex.: folhas "BOLÃO ESCAPA"/"BOLÃO
    EXTRA" onde a marca é um X: `X TIME1 TIME2`=Casa, `TIME1 X TIME2`=Empate,
    `... X`=Fora). Só conta um span cujo texto seja EXATAMENTE "X" — senão o X
    DENTRO de um nome de time (ex.: "MONTERREY-MEX") entra como marca e a grade
    passa do múltiplo-de-8, fazendo a página nem ser reconhecida como digital.
    Também filtra os X das linhas NOME/FONE (apelido tipo "Espanha X Argentina")."""
    xs = []
    bad_y = []
    for b in p.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            txt = " ".join(s["text"] for s in ln["spans"]).strip()
            up = txt.upper()
            if up.startswith("NOME") or up.startswith("FONE"):
                y0, y1 = ln["bbox"][1], ln["bbox"][3]
                bad_y.append((y0 - 2, y1 + 2))
            for s in ln["spans"]:
                if s["text"].strip().upper() == "X":       # span = SÓ o X (marca)
                    x0, y0_, x1, y1_ = s["bbox"]
                    xs.append(((x0 + x1) / 2, (y0_ + y1_) / 2))
    if not xs:
        return []
    # exclui X que caem dentro de uma linha NOME:/FONE: (apelido do apostador)
    keep = [(x, y) for x, y in xs
            if not any(lo <= y <= hi for lo, hi in bad_y)]
    return keep


def _image_marks(p):
    """Centros (em coordenadas de PÁGINA) dos ■ quando a marca está RASTERIZADA
    numa imagem — folha "TODA SEGUNDA"/RESSACA: os nomes dos times são texto
    vetorial, mas o quadrado preenchido fica dentro de uma imagem (não é desenho
    nem glifo). Renderiza a página e acha quadrados sólidos e UNIFORMES (digital
    limpo); uma foto/scan dá blobs irregulares e não passa no múltiplo-de-8."""
    if not p.get_images():
        return []                              # sem imagem → nada a extrair
    try:
        import fitz
        import cv2
        import numpy as np
    except Exception:
        return []
    zoom = 3.0
    pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    th = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV)[1]
    n, _, stats, cent = cv2.connectedComponentsWithStats(th, 8)
    cand = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 8 < w < 34 and 8 < h < 34 and 0.7 < w / h < 1.4 and area / (w * h + 1e-9) > 0.80:
            cand.append((cent[i][0] / zoom, cent[i][1] / zoom, w))
    if len(cand) < 8:
        return []
    ws = sorted(c[2] for c in cand)
    med = ws[len(ws) // 2]                      # só os de tamanho ~mediano (marca)
    return [(x, y) for x, y, w in cand if 0.6 * med <= w <= 1.6 * med]


def _mark_positions(p):
    """(x,y) de cada marca da página. Tenta, em ordem, os estilos conhecidos:
    ● (bullet), ■ vetorial, X (letra) e ■ RASTERIZADO em imagem (RESSACA "TODA
    SEGUNDA") — o primeiro que formar uma grade completa (múltiplo de 8) vence."""
    dots = [((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) for r in p.search_for("●")]
    if len(dots) >= 8 and len(dots) % 8 == 0:
        return dots
    sq = _filled_squares(p)
    if len(sq) >= 8 and len(sq) % 8 == 0:
        return sq
    fd = _filled_dots(p)                     # ● vetorial (círculo preenchido)
    if len(fd) >= 8 and len(fd) % 8 == 0:
        return fd
    xs = _x_marks(p)
    if len(xs) >= 8 and len(xs) % 8 == 0:
        return xs
    im = _image_marks(p)
    if len(im) >= 8 and len(im) % 8 == 0:
        return im
    # nenhum formou grade exata — devolve o mais forte (o %8 decide em is_digital)
    return max([dots, sq, fd, xs, im], key=len)


def _page_is_digital(p) -> bool:
    """True se ESTA página é vetorial com estrutura de marcas (não scan/foto)."""
    try:
        text = p.get_text().strip()
        if len(p.get_images()) > 0 and len(text) < 100:
            return False                      # página só-imagem → é scan/foto
        n = len(_mark_positions(p))
        return len(text) > 150 and n >= 8 and n % 8 == 0
    except Exception:
        return False


def is_digital_sheet(path: str) -> bool:
    """True if this is a vector PDF with the ●-mark structure (not a scan/photo)."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return False
    try:
        if doc.page_count == 0:
            return False
        return _page_is_digital(doc[0])
    except Exception:
        return False
    finally:
        doc.close()


def digital_page_numbers(path: str):
    """Nº (1-based) das páginas DIGITAIS de um PDF — um mapa de rodada é
    normalmente MISTO (ex.: 448 págs = ~397 digitais do sistema + ~51 de
    caneta escaneadas), e o roteamento precisa ser por página: digital lê
    direto de graça; só as de foto vão pra IA/OCR."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return []
    try:
        return [i + 1 for i in range(doc.page_count) if _page_is_digital(doc[i])]
    except Exception:
        return []
    finally:
        doc.close()


def _page_is_scanned_sheet(p) -> bool:
    """Página que é uma folha de bolão ACHATADA em imagem (a administração junta
    cartelas de vários vendedores e exporta a folha como IMAGEM — sem texto/marca
    vetorial). É uma folha cheia de cartelas, não uma foto solta: 1 imagem grande
    cobrindo a página e quase nenhum texto vetorial. Essas SÓ leem pela IA."""
    try:
        imgs = p.get_images()
        if not imgs or len(p.get_text().strip()) >= 100:
            return False
        # imagem GRANDE = folha inteira achatada (não um selo/logo pequeno).
        # im[2]/im[3] = largura/altura em pixels da imagem embutida.
        return any(im[2] * im[3] >= 500 * 500 for im in imgs)
    except Exception:
        return False


def scanned_sheet_page_numbers(path: str):
    """Nº (1-based) das páginas que são FOLHAS achatadas em imagem (combinadas
    pela administração) — vão direto pra IA (não têm dado vetorial pra ler grátis
    nem são foto de caneta que o OCR local entenda)."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return []
    try:
        return [i + 1 for i in range(doc.page_count) if _page_is_scanned_sheet(doc[i])]
    except Exception:
        return []
    finally:
        doc.close()


def _page_confrontos(p):
    """Os 8 confrontos (time_casa, time_visitante) de UMA folha digital — lidos da
    1ª cartela (mesmos jogos pra todas). Pega o texto dos times, separa em
    sub-colunas casa (esquerda) / visitante (direita) e junta os 8 de cima."""
    hdr_text, hdr = _header_positions(p, 3, 0)
    if len(hdr) < 2:
        return None
    n_cols = len(_split_by_gaps([h[0] for h in hdr]))
    min_hy = min(h[1] for h in hdr)            # y do 1º cabeçalho (topo)
    lines = []
    for b in p.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            x0, y0, x1, y1 = ln["bbox"]
            if y0 < min_hy - 2:                # acima do 1º cabeçalho = banner
                continue
            t = " ".join(s["text"] for s in ln["spans"]).strip()
            t = t.replace("●", "").strip()     # tira a marca colada no nome
            if t[:2] == "X ":                  # X-marca no começo (folha "BARRAS")
                t = t[2:].strip()
            if t[-2:] == " X":
                t = t[:-2].strip()
            up = t.upper()
            if (len(t) < 3 or up.startswith(("NOME", "FONE")) or t == hdr_text
                    or any(ch.isdigit() for ch in t)):
                continue
            lines.append(((x0 + x1) / 2, y0, t))
    if len(lines) < 2 * n_cols * 4:            # precisa de time o suficiente
        return None
    subs = sorted(_gap_split([x for x, _, _ in lines], 2 * n_cols))
    # coluna 0: sub 0 = time da casa, sub 1 = visitante; 8 de cima (1ª cartela)
    casa = sorted([(y, t) for x, y, t in lines if _nearest(x, subs) == 0])[:8]
    fora = sorted([(y, t) for x, y, t in lines if _nearest(x, subs) == 1])[:8]
    if len(casa) != 8 or len(fora) != 8:
        return None
    return [[casa[i][1], fora[i][1]] for i in range(8)]


def extract_confrontos(path: str):
    """Os 8 confrontos da rodada (mesmos jogos pra todas as cartelas), lidos da 1ª
    folha DIGITAL do PDF. Retorna [[casa, fora], …] (8) ou None. Usado só pra
    ENFEITAR a grade da cartela no ranking (mostrar 'GRÊMIO × BOLÍVAR')."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return None
    try:
        for pi in range(doc.page_count):
            if _page_is_digital(doc[pi]):
                conf = _page_confrontos(doc[pi])
                if conf:
                    return conf
        return None
    except Exception:
        return None
    finally:
        doc.close()


def _header_positions(p, min_repeat, rows):
    """Linhas repetidas que servem de âncora do layout (uma por cartela). Retorna
    (x_center, y_center, x0, x1) da melhor linha-âncora.

    Achar o cabeçalho por 'a mais larga' falha quando um apostador repete um NOME
    longo em várias cartelas; por 'a mais frequente' falha quando a marca vira
    texto ('X' 56×) ou há rótulo 2×/cartela ('ANULADO' 48×). Sinal robusto: uma
    linha que aparece 1×/cartela forma uma GRADE n_cols × n_rows; pegamos n_cols e
    n_rows como a MODA (ponderada por frequência) entre as candidatas, e então a
    linha mais larga que bate exatamente nessa grade."""
    from collections import Counter
    by_text = {}
    for b in p.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            txt = " ".join(s["text"] for s in ln["spans"]).strip()
            if len(txt) < 4 or "●" in txt:      # <4 = marca virada texto ('X','V')
                continue
            x0, y0, x1, y1 = ln["bbox"]
            by_text.setdefault(txt, []).append(((x0 + x1) / 2, (y0 + y1) / 2, x0, x1))
    cands = {t: pos for t, pos in by_text.items() if len(pos) >= min_repeat}
    if not cands:
        return "", []
    grid = {t: (len(_split_by_gaps([q[0] for q in pos])),
               len(_split_by_gaps([q[1] for q in pos]))) for t, pos in cands.items()}
    gx_votes, gy_votes = Counter(), Counter()
    for t, (gx, gy) in grid.items():
        gx_votes[gx] += len(cands[t])           # voto ponderado pela frequência
        gy_votes[gy] += len(cands[t])
    gx_mode = gx_votes.most_common(1)[0][0]
    gy_mode = gy_votes.most_common(1)[0][0]
    good = [t for t in cands if grid[t] == (gx_mode, gy_mode)] or list(cands)
    # Entre as linhas da grade-moda, a âncora certa é a que aparece 1×/CARTELA e
    # é o CABEÇALHO/TÍTULO — que fica no TOPO de cada cartela (menor y médio).
    # Desempatar por "mais larga" pegava um NOME DE TIME longo (ex.: "UNIV.
    # CENTRAL-VEN", que fica numa sub-coluna, não no meio) → centros de coluna
    # errados. Prioriza frequência (1×/cartela) e depois o topo (título).
    def _avg_y(t):
        return sum(yc for _, yc, _, _ in cands[t]) / len(cands[t])
    header = max(good, key=lambda t: (len(cands[t]), -_avg_y(t)))
    return header, [(xc, yc, x0, x1) for xc, yc, x0, x1 in cands[header]]


def _text_col_centers(p, n_cols, header_text):
    """Centros REAIS das colunas de cartela pelos NOMES DOS TIMES (texto vetorial,
    sempre presente no MEIO da cartela — casa à esquerda, visitante à direita).
    Agrupa o x dos textos em 2×n_cols sub-colunas (casa/visitante de cada coluna)
    e PAREIA → centro exato de cada cartela.

    Bem mais preciso que: o centro do CABEÇALHO (que fica deslocado do meio das
    marcas) ou a folga entre MARCAS (que some quando a caixa Fora de uma coluna
    encosta na Casa da vizinha) — as duas erravam a divisa em folhas densas."""
    tc = []
    for b in p.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            t = " ".join(s["text"] for s in ln["spans"]).strip()
            up = t.upper()
            if len(t) < 3 or up.startswith(("NOME", "FONE")) or t == header_text:
                continue
            x0, y0, x1, y1 = ln["bbox"]
            tc.append((x0 + x1) / 2)
    if len(tc) < 2 * n_cols:
        return None
    subs = sorted(_gap_split(tc, 2 * n_cols))
    return [(subs[2 * c] + subs[2 * c + 1]) / 2 for c in range(n_cols)]


def _split_by_gaps(vals):
    """Agrupa valores 1-D em clusters; novo cluster onde a folga passa de METADE
    da maior folga (numa grade regular as folgas dentro do cluster são ~0 e as
    folgas entre clusters são grandes e parecidas). Serve pra DETECTAR quantas
    colunas/fileiras de cartela a página tem (24, 15, …) — não força 4×6."""
    vals = sorted(vals)
    if len(vals) <= 1:
        return [list(vals)]
    gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    thr = 0.5 * max(gaps)
    clusters, cur = [], [vals[0]]
    for i, g in enumerate(gaps):
        if thr > 0 and g > thr:
            clusters.append(cur)
            cur = [vals[i + 1]]
        else:
            cur.append(vals[i + 1])
    clusters.append(cur)
    return clusters


def _column_bounds(col_centers, bullets):
    """Divisa x entre colunas = meio da MAIOR folga na distribuição das marcas,
    procurada numa JANELA estreita em volta do meio dos centros de coluna. Acha o
    vão real entre cartelas vizinhas (a marca Fora de uma quase encosta na Casa da
    próxima) sem depender do alinhamento do cabeçalho (centralizado OU à esquerda)
    nem da largura da marca — os dois quebravam o meio-dos-centros e o x0."""
    xs = sorted(x for x, _ in bullets)
    n = len(col_centers)
    S = (col_centers[-1] - col_centers[0]) / (n - 1) if n > 1 else 0
    bounds = []
    for c in range(n - 1):
        mid = (col_centers[c] + col_centers[c + 1]) / 2
        half = 0.35 * S
        win = [x for x in xs if mid - half <= x <= mid + half]
        best = mid
        if len(win) >= 2:
            gap = -1.0
            for i in range(len(win) - 1):
                g = win[i + 1] - win[i]
                if g > gap:
                    gap, best = g, (win[i] + win[i + 1]) / 2
        bounds.append(best)
    return bounds


def _assign_col(x, bounds):
    for c, b in enumerate(bounds):
        if x < b:
            return c
    return len(bounds)


def _read_page(p, page_number: int, games=8) -> List[CardResult]:
    bullets = _mark_positions(p)
    if not bullets:
        return []

    # LAYOUT DETECTADO dos cabeçalhos (linha repetida mais larga: "BOLÃO ESCAPA",
    # "BOLÃO DA ALEGRIA", "RODADA DO DIA …"). Quantas colunas E fileiras a página
    # REALMENTE tem — não força 4×6: uma folha pode ter 24, 15, … cartelas.
    hdr_text, hdr = _header_positions(p, 3, 0)
    if len(hdr) >= 2:
        hdr_cols = sorted(sum(g) / len(g) for g in _split_by_gaps([h[0] for h in hdr]))
        hdr_rows = sorted(sum(g) / len(g) for g in _split_by_gaps([h[1] for h in hdr]))
        n_cols, n_rows = len(hdr_cols), len(hdr_rows)
        # DIVISA das colunas: 1º tenta os centros REAIS pelos nomes dos times
        # (preciso em folha densa onde a marca Fora encosta na Casa vizinha). Só
        # confia se saírem UNIFORMEMENTE espaçados — numa folha onde a marca é a
        # letra "X" (parte do texto do time), o centro do texto desloca e sai
        # torto; aí cai no método antigo (folga entre marcas), que serve pra ela.
        tcenters = _text_col_centers(p, n_cols, hdr_text)
        gaps = ([tcenters[c + 1] - tcenters[c] for c in range(n_cols - 1)]
                if tcenters and len(tcenters) == n_cols else [])
        if gaps and min(gaps) > 0 and max(gaps) / min(gaps) < 1.15:
            col_centers = tcenters
            col_bounds = [(col_centers[c] + col_centers[c + 1]) / 2 for c in range(n_cols - 1)]
        else:
            col_centers = hdr_cols
            col_bounds = _column_bounds(col_centers, bullets)
        # células que EXISTEM (têm cabeçalho) — evita cartela-fantasma numa fileira
        # incompleta (ex.: página 91 = 15 cartelas = 3 fileiras de 4 + 1 de 3).
        valid = set()
        for xc, yc, x0, x1 in hdr:
            valid.add((_nearest(yc, hdr_rows), _assign_col(xc, col_bounds)))
    else:
        n_cols, n_rows = 4, 6
        col_centers = sorted(_gap_split([x for x, _ in bullets], n_cols))
        col_bounds = [(col_centers[c] + col_centers[c + 1]) / 2 for c in range(n_cols - 1)]
        valid = {(r, c) for r in range(n_rows) for c in range(n_cols)}

    # Fileiras das MARCAS: a grade fica abaixo do cabeçalho; agrupa o y das marcas
    # em n_rows faixas (o vão entre cartelas separa as faixas).
    row_centers = sorted(_gap_split([y for _, y in bullets], n_rows))

    cells = {}
    for x, y in bullets:
        c = _assign_col(x, col_bounds)
        r = _nearest(y, row_centers)
        cells.setdefault((r, c), []).append((x, y))

    # ZONAS Casa/Empate/Fora pela posição RELATIVA da marca ao centro da SUA
    # coluna, juntando TODAS as marcas da página (não coluna a coluna). As 3
    # caixas são iguais em toda cartela, então a página inteira SEMPRE tem as 3
    # zonas — mesmo que UMA coluna não tenha marca em alguma (ex.: página com
    # pouco Empate). Antes o gap-split-de-3 POR COLUNA errava quando faltava uma
    # zona, e a leitura saía torta só nessas páginas (a 205 lia, a 206/207 não).
    offsets = [x - col_centers[_assign_col(x, col_bounds)] for x, y in bullets]
    zone_offsets = sorted(_gap_split(offsets, 3)) if len(offsets) >= 3 else None

    # Nome do apostador: cada cartela tem "NOME: ..." — casado à sua célula pela
    # posição (fileira/coluna). SEMPRE capturado num campo SEPARADO (`nome`), que vai
    # só pro EXPORT/site (coluna "Nome"). O RÓTULO (participant) continua "Pág X #Y" —
    # INCLUDE_NAMES controla apenas se o nome TAMBÉM aparece grudado no rótulo, o que
    # desconfigurava a organização (por isso fica False). Assim o site mostra o nome
    # do cliente sem mexer no display/PDF/ranking do programa.
    # Fileiras de referência pra casar o RODAPÉ (que fica ABAIXO da cartela) com a
    # cartela certa: o cabeçalho logo acima do rodapé. Usa os cabeçalhos quando há;
    # senão, os centros das marcas.
    row_anchors = hdr_rows if len(hdr) >= 2 else row_centers
    names = {}
    for b in p.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            txt = " ".join(s["text"] for s in ln["spans"]).strip()
            up = txt.upper()
            if up.startswith("NOME") or up.startswith("APOSTADOR"):
                nm = txt.split(":", 1)[1] if ":" in txt else ""
                nm = "".join(ch for ch in nm if ch.isprintable()).strip()
                if nm:
                    x0, y0, x1, y1 = ln["bbox"]
                    rr = _nearest(y0, row_centers); cc = _assign_col(x0, col_bounds)
                    names[(rr, cc)] = nm
            else:
                # Rodapé "Nº - NOME" das cartelas recebidas de outro sistema. Casa pela
                # COLUNA (x) e pela fileira do cabeçalho logo ACIMA do rodapé (o rodapé
                # fica no rodapé da cartela, então _nearest por centro erraria a fileira).
                fm = _FOOTER_NOME_RE.match(txt)
                if fm:
                    nm = re.sub(r"\s+", " ", fm.group(1)).strip()
                    if nm:
                        x0, y0 = ln["bbox"][0], ln["bbox"][1]
                        cc = _assign_col(x0, col_bounds)
                        rr = max(0, min(sum(1 for hy in row_anchors if hy < y0) - 1, n_rows - 1))
                        names.setdefault((rr, cc), nm)

    results: List[CardResult] = []
    for r in range(n_rows):
        for c in range(n_cols):
            if (r, c) not in valid:
                continue   # célula sem cabeçalho não existe (fileira incompleta)
            bs = sorted(cells.get((r, c), []), key=lambda b: b[1])[:games]
            marks = []
            for g in range(games):
                if g < len(bs) and zone_offsets and len(zone_offsets) == 3:
                    choice = _nearest(bs[g][0] - col_centers[c], zone_offsets)  # 0=Casa 1=Empate 2=Fora
                    raw = [0.0, 0.0, 0.0]; raw[choice] = 1.0
                    marks.append(MarkResult(game=g, choice=choice, confidence=1.0,
                                            needs_review=False, raw_scores=raw))
                else:
                    marks.append(MarkResult(game=g, choice=None, confidence=0.0,
                                            needs_review=True, raw_scores=[0, 0, 0]))
            ci = r * n_cols + c
            # Rótulo (participant) = SEMPRE Pág+Nº pra localizar a página no display/PDF.
            # O nome vai num campo SEPARADO (`nome`) → só pro export/site. Só grudo no
            # rótulo se INCLUDE_NAMES (hoje False, pra não desconfigurar a organização).
            nm = names.get((r, c))
            label = f"Pág {page_number} #{ci + 1}"
            if nm and INCLUDE_NAMES:
                label = f"{label} — {nm}"
            results.append(CardResult(card_index=ci, page=page_number,
                                      marks=marks,
                                      has_review_flags=any(m.needs_review for m in marks),
                                      participant=label, nome=nm))
    return results


def read_digital(path: str, pages=None) -> List[CardResult]:
    """Read cartelas directly from the vector PDF.

    pages: conjunto opcional de nºs de página (1-based) — num PDF misto, lê só
    as páginas digitais e deixa as de foto para a rota IA/OCR."""
    import fitz
    doc = fitz.open(path)
    out: List[CardResult] = []
    try:
        for pi in range(doc.page_count):
            if pages is not None and (pi + 1) not in pages:
                continue
            out.extend(_read_page(doc[pi], pi + 1))
    finally:
        doc.close()
    return out
