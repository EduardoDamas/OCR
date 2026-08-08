# -*- coding: utf-8 -*-
"""
Leitura por IA de visão (Claude) para folhas de CANETA fotografadas/escaneadas.

Validado em campo (pag1.pdf, 15/jul/2026): 40/40 jogos conferidos no olho,
0 ilegíveis na folha inteira, custo real ~R$1,22/folha (Sonnet).

Método (o que fez dar 100%):
  1. UMA CARTELA POR CHAMADA — recorte grande (~950px). Página inteira falha.
  2. ÂNCORA POR NOME DE TIME — primeiro lê os 8 confrontos da cartela 1, depois
     injeta os nomes no prompt de cada cartela ("a 1ª linha É o jogo 1, não é
     cabeçalho"). Sem isso o modelo desloca as linhas (off-by-one).

Ativação: presença da chave em data/ai_config.json (criada pela UI) ou na
variável de ambiente ANTHROPIC_API_KEY. Sem chave => rota desativada e o
pipeline segue no OCR local, como sempre.
"""

import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional

from .recognizer import MarkResult, CardResult

MODELO = "claude-sonnet-5"
_CH = {"C": 0, "E": 1, "F": 2}

_SCHEMA_TIMES = {
    "type": "object",
    "properties": {
        "jogos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"casa": {"type": "string"}, "fora": {"type": "string"}},
                "required": ["casa", "fora"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["jogos"],
    "additionalProperties": False,
}

_SCHEMA_MARCAS = {
    "type": "object",
    "properties": {
        "jogos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "time_casa": {"type": "string"},
                    "marca": {"type": "string", "enum": ["C", "E", "F", "?"]},
                },
                "required": ["time_casa", "marca"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["jogos"],
    "additionalProperties": False,
}


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data", "ai_config.json")


def get_api_key() -> Optional[str]:
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            key = (json.load(f).get("api_key") or "").strip()
            if key:
                return key
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY") or None


def set_api_key(key: str) -> None:
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"api_key": key.strip(), "model": MODELO}, f)


# ── cache de RESUME ──────────────────────────────────────────────────────────
# Cada cartela lida pela IA é gravada em disco. Se o crédito acabar no meio, ao
# recolocar crédito e reprocessar o MESMO arquivo, as cartelas já lidas voltam
# do cache SEM chamar a API de novo (custo zero) — o processo "continua de onde
# parou" em vez de pagar tudo outra vez.

def _cache_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "ai_cache.json")


def _file_key(path: str) -> str:
    """Identidade estável do arquivo (nome + tamanho) — muda se o PDF mudar."""
    try:
        return f"{os.path.basename(path)}:{os.path.getsize(path)}"
    except Exception:
        return os.path.basename(path)


def _load_cache() -> dict:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


def cached_count(path: str) -> int:
    """Quantas cartelas deste arquivo já estão no cache (0 = leitura do zero)."""
    fk = _file_key(path)
    return sum(1 for k in _load_cache() if k.startswith(fk + "|"))


def available() -> bool:
    if not get_api_key():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def test_connection() -> str:
    """Diagnóstico completo pro usuário final (botão 'Testar IA agora'):
    mostra onde a chave deveria estar, se existe, e faz UMA chamada mínima
    real (custa ~R$0,0001) traduzindo o erro exato em linguagem simples."""
    linhas = []
    cfg = _config_path()
    linhas.append(f"Arquivo da chave: {cfg}")
    linhas.append("Arquivo existe: " + ("SIM" if os.path.exists(cfg) else "NÃO"))
    key = get_api_key()
    if not key:
        linhas.append("\n❌ NENHUMA CHAVE ENCONTRADA.")
        linhas.append("Clique em 'Configurar chave da IA' e cole a chave sk-ant-...")
        return "\n".join(linhas)
    linhas.append(f"Chave encontrada: {key[:12]}... ({len(key)} caracteres)")
    if not key.startswith("sk-ant-"):
        linhas.append("\n⚠️ A chave não começa com 'sk-ant-' — confira se copiou certo.")
    try:
        import anthropic
    except Exception as e:
        linhas.append(f"\n❌ Componente da IA não carregou: {e}")
        return "\n".join(linhas)
    try:
        client = anthropic.Anthropic(api_key=key, max_retries=1)
        client.messages.create(model="claude-haiku-4-5", max_tokens=1,
                               messages=[{"role": "user", "content": "oi"}])
        linhas.append("\n✅ IA FUNCIONANDO! Chave válida, conexão OK, crédito OK.")
    except anthropic.AuthenticationError:
        linhas.append("\n❌ CHAVE INVÁLIDA — a API recusou a chave.")
        linhas.append("Causa mais comum: a chave foi CORTADA no copiar/colar "
                      "(faltou ou sobrou algum caractere).")
        linhas.append("Solução: no site console.anthropic.com > API Keys, "
                      "gere uma chave NOVA e copie usando o BOTÃO de copiar "
                      "do site (não selecione com o mouse). Depois cole aqui "
                      "em 'Configurar chave da IA' e teste de novo.")
    except anthropic.PermissionDeniedError as e:
        linhas.append(f"\n❌ SEM PERMISSÃO/CRÉDITO: {e}")
    except anthropic.BadRequestError as e:
        msg = str(e)
        if "credit" in msg.lower() or "billing" in msg.lower():
            linhas.append("\n❌ SEM CRÉDITO na conta — adicione créditos em "
                          "console.anthropic.com > Billing.")
        else:
            linhas.append(f"\n❌ Erro na requisição: {msg[:200]}")
    except anthropic.APIConnectionError:
        linhas.append("\n❌ SEM CONEXÃO com a IA — verifique a internet "
                      "(ou firewall/antivírus bloqueando o programa).")
    except Exception as e:
        linhas.append(f"\n❌ Erro: {type(e).__name__}: {str(e)[:200]}")
    return "\n".join(linhas)


# ── recortes ─────────────────────────────────────────────────────────────────

def _page_images_highres(path: str, pages=None):
    """Gera (nº_página_1based, imagem alta-res). `pages` filtra páginas ANTES
    de renderizar (num PDF misto de 448 págs, pular as ~397 digitais aqui
    economiza minutos de renderização)."""
    import cv2
    import numpy as np
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz
        doc = fitz.open(path)
        for pi in range(doc.page_count):
            num = pi + 1
            if pages is not None and num not in pages:
                continue
            page = doc[pi]
            # ~6x num A4 — resolução em que a leitura foi validada em campo
            # (renderizar menos e dar upscale borra o risco fino da caneta)
            zoom = 4900 / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
            yield num, cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
        doc.close()
    else:
        img = cv2.imread(path)
        if img is not None:
            # Imagem (PNG/JPG): pode ser um print de tela PEQUENO. A IA precisa
            # de recorte grande (~950px/cartela). Se a foto for pequena, dá
            # upscale pro lado maior ~4900px — não cria detalhe que não existe,
            # mas evita entregar cartela de 130px (ilegível) pra IA.
            long_edge = max(img.shape[:2])
            if long_edge < 4900:
                s = 4900.0 / long_edge
                img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
            yield 1, img


def input_resolution_warning(path: str) -> Optional[str]:
    """Aviso se a IMAGEM de entrada é pequena demais pra IA ler bem (ex.: print
    de tela). Retorna a mensagem ou None. PDF nunca avisa (é vetorial, renderiza
    em qualquer resolução)."""
    if str(path).lower().endswith(".pdf"):
        return None
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return None
        # cartela real = lado_maior/6 (6 fileiras). <220px na origem = ruim.
        cart_px = max(img.shape[:2]) / 6.0
        if cart_px < 220:
            return (f"A imagem tem só {img.shape[1]}x{img.shape[0]} pixels "
                    f"(cada cartela ~{int(cart_px)}px) — pequena demais pra IA "
                    f"ler a caneta com precisão.\n\n"
                    f"👉 Use o ARQUIVO PDF original (não um print de tela), ou "
                    f"uma FOTO em alta resolução da folha. O PDF sempre lê melhor.")
    except Exception:
        pass
    return None


def _sheet_quad(gray):
    """Quadrilátero da folha (4 pontos ordenados) ou None — mesma lógica do
    correct_perspective do preprocessor, mas devolvendo os PONTOS para que o
    warp possa ser aplicado escalado na imagem de alta resolução."""
    import cv2
    import numpy as np
    from .preprocessor import _order_points

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 11, 2)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            if cv2.contourArea(approx) > 0.3 * gray.shape[0] * gray.shape[1]:
                return _order_points(approx.reshape(4, 2).astype(np.float32))
    return None


def _skew_norm(gray_norm):
    """Ângulo de rotação estimado na imagem normalizada (ângulo é invariante
    à escala, então pode ser aplicado direto no alta-res)."""
    import cv2
    import numpy as np
    from .preprocessor import _horizontal_line_mask

    horiz = _horizontal_line_mask(gray_norm)
    lines = cv2.HoughLinesP(horiz, 1, np.pi / 360, threshold=120,
                            minLineLength=gray_norm.shape[1] // 6, maxLineGap=30)
    if lines is None:
        return 0.0
    angs = [np.degrees(np.arctan2(l[0][3] - l[0][1], l[0][2] - l[0][0]))
            for l in lines if l[0][2] != l[0][0]]
    angs = [a for a in angs if abs(a) < 20]
    return float(np.median(angs)) if angs else 0.0


def _raw_line_bbox(gray_norm):
    """Bbox bruto das linhas impressas (sem strip de título) — o grid preto é
    sempre alto-contraste, mesmo quando a borda da folha não é."""
    import numpy as np
    from .detector import _grid_line_masks

    horiz, vert = _grid_line_masks(gray_norm)
    grid = horiz | vert
    rowsum = np.sum(grid > 0, axis=1).astype(float)
    colsum = np.sum(grid > 0, axis=0).astype(float)
    if rowsum.max() == 0 or colsum.max() == 0:
        return None
    # limiar de coluna mais alto: sombra/borda da folha gera linha fraca que
    # estica o bbox pro lado e desloca as 4 colunas de cartelas
    rows = np.where(rowsum > rowsum.max() * 0.08)[0]
    cols = np.where(colsum > colsum.max() * 0.25)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1] - cols[0]), int(rows[-1] - rows[0])


def _prepare_highres(img_big):
    """Corrige a geometria da FOTO em alta resolução (a divisão 4x6 só funciona
    com a folha ocupando o quadro): 1) deskew (ângulo estimado nas linhas
    impressas da versão normalizada — invariante à escala); 2) warp pelo quad
    da folha, se encontrado; 3) senão, recorte pelo bbox bruto das linhas
    impressas com folga (funciona mesmo com folha branca em fundo claro,
    onde o quad falha)."""
    import cv2
    import numpy as np
    from .preprocessor import normalize_width, to_gray

    # 1) deskew primeiro — endireita as linhas p/ o bbox ficar justo
    ang = _skew_norm(to_gray(normalize_width(img_big)))
    if abs(ang) >= 0.2:
        h, w = img_big.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), ang, 1.0)
        img_big = cv2.warpAffine(img_big, M, (w, h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)

    norm = normalize_width(img_big)
    s = img_big.shape[1] / float(norm.shape[1])
    g = to_gray(norm)

    # 2) quad da folha (quando a borda tem contraste)
    quad = _sheet_quad(g)
    if quad is not None:
        pts = quad * s
        (tl, tr, br, bl) = pts
        wd = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        hg = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        if wd > 300 and hg > 300:
            dst = np.array([[0, 0], [wd - 1, 0], [wd - 1, hg - 1], [0, hg - 1]],
                           np.float32)
            M = cv2.getPerspectiveTransform(pts.astype(np.float32), dst)
            return cv2.warpPerspective(img_big, M, (wd, hg))

    # 3) fallback: recorta o miolo impresso com folga de 1.5%.
    #    LINHAS (y): via _content_bbox do detector, que APLICA o strip de
    #    título — o banner "BOLÃO ESCAPA" tem linha tracejada de página
    #    inteira que ancorava o bbox nele e descia TODAS as bandas de fileira
    #    (recortes desalinhados → IA lia lixo). COLUNAS (x): limiar 0.25
    #    próprio, que ignora sombra de borda de folha.
    bb = _raw_line_bbox(g)
    try:
        from .detector import _content_bbox
        bs = _content_bbox(g)
    except Exception:
        bs = None
    if bb is not None:
        H, W = img_big.shape[:2]
        bx, _, bw, _ = [int(v * s) for v in bb]
        if bs is not None:
            by, bh = int(bs[1] * s), int(bs[3] * s)
        else:
            by, bh = int(bb[1] * s), int(bb[3] * s)
        if bw > 0.4 * W and bh > 0.4 * H:
            px, py = int(bw * 0.015), int(bh * 0.015)
            x0, y0 = max(0, bx - px), max(0, by - py)
            x1, y1 = min(W, bx + bw + px), min(H, by + bh + py)
            return img_big[y0:y1, x0:x1]
    return img_big


def _row_tops(gray_norm):
    """y (na página normalizada) do TOPO DA TABELA de cada fileira de cartelas.

    Cada cartela tem título + tabela de 8 jogos + NOME/FONE; a assinatura
    confiável de uma fileira é o começo de um RUN de ≥5 linhas horizontais
    fortes com passo de linha-de-jogo. Devolve os 6 tops, ou None."""
    import numpy as np
    from .detector import _grid_line_masks

    h, w = gray_norm.shape[:2]
    horiz, _ = _grid_line_masks(gray_norm)
    rowsum = np.sum(horiz > 0, axis=1).astype(float)
    ys = np.where(rowsum >= w * 0.25)[0]
    if len(ys) < 12:
        return None
    # agrupa pixels consecutivos em linhas
    cl = []
    s = p = int(ys[0])
    for y in ys[1:]:
        if y - p > 8:
            cl.append((s + p) // 2)
            s = int(y)
        p = int(y)
    cl.append((s + p) // 2)
    # topo = começo de um run LONGO (>=7 linhas) de passo auto-consistente
    # (a tabela tem 9 linhas no passo dos jogos; NOME/FONE tem só 2-3)
    tops = []
    i = 0
    n = len(cl)
    while i < n - 6:
        gaps = [cl[i + k + 1] - cl[i + k] for k in range(5)]
        med = sorted(gaps)[2]
        ok = (h * 0.007 <= med <= h * 0.022
              and all(abs(gp - med) <= 0.38 * med for gp in gaps)
              and (i == 0 or cl[i] - cl[i - 1] > 1.6 * med))
        if ok:
            j = i + 1
            while j < n and cl[j] - cl[j - 1] <= 1.5 * med:
                j += 1
            if j - i >= 7:          # run longo = tabela de jogos
                tops.append(cl[i])
                i = j
                continue
        i += 1
    if len(tops) == 6:
        return tops
    # GRADE PARCIAL: em foto borrada no topo, as fileiras de cima não geram
    # linhas detectáveis (visto em campo: runs só das fileiras 3-6). Com 2+
    # runs dá pra ajustar o passo e EXTRAPOLAR as fileiras que faltam.
    if len(tops) >= 2:
        difs = np.diff(tops)
        passo = float(np.median(difs))
        if h * 0.10 <= passo <= h * 0.22:
            # índice relativo de cada run e refino do passo/origem
            ks = [round((t - tops[0]) / passo) for t in tops]
            if len(set(ks)) == len(ks) and max(ks) >= 1:
                passo = (tops[-1] - tops[0]) / max(ks)
                t0 = float(np.median([t - k * passo for t, k in zip(tops, ks)]))
                # desloca a origem p/ a fileira 0: as 6 tabelas têm que caber
                for m in range(0, 6):
                    first = t0 - m * passo
                    last = first + 5 * passo
                    if first >= h * 0.005 and last + 0.83 * passo <= h * 1.01:
                        return [first + i * passo for i in range(6)]
    return None


def _crops_24(img_bgr):
    """24 recortes JPEG-base64 (4 colunas × 6 fileiras) em alta resolução.

    Fileiras ancoradas nos 6 topos de tabela detectados (robusto a cabeçalho
    de página de qualquer altura); fallback = divisão uniforme da página."""
    import cv2
    from .preprocessor import normalize_width

    h, w = img_bgr.shape[:2]
    norm = normalize_width(img_bgr)
    g = cv2.cvtColor(norm, cv2.COLOR_BGR2GRAY)
    s = w / float(norm.shape[1])

    tops = _row_tops(g)
    if tops:
        import numpy as np
        # ajusta a uma grade uniforme: um top individual detectado 1-2 linhas
        # baixo (1ª linha da tabela fraca na foto) é corrigido pela mediana
        difs = np.diff(tops)
        pitch = float(np.median(difs))
        t0 = float(np.median([t - i * pitch for i, t in enumerate(tops)]))
        tops_fit = [t0 + i * pitch for i in range(6)]
        # banda generosa p/ cima (título + tolerância) e até perto da próxima
        bands = [(int((t - 0.28 * pitch) * s), int((t + 0.88 * pitch) * s))
                 for t in tops_fit]
    else:
        bh = h / 6.0
        bands = [(int(r * bh - 0.04 * bh), int((r + 1) * bh + 0.10 * bh))
                 for r in range(6)]

    cw = w / 4.0
    # geometria validada em campo (24/24): esquerda quase sem folga (uma lasca
    # da cartela vizinha à esquerda confunde a IA), direita com folga generosa
    ml, mr = max(4, int(w * 0.005)), int(w * 0.017)
    out = []
    for (y0, y1) in bands:
        y0 = max(0, y0)
        y1 = min(h, y1)
        for col in range(4):
            x0 = max(0, int(col * cw) - ml)
            x1 = min(w, int((col + 1) * cw) + mr)
            crop = img_bgr[y0:y1, x0:x1]
            scale = 950.0 / max(1, crop.shape[1])
            if scale > 1.05:  # garante recorte grande o suficiente
                crop = cv2.resize(crop, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_CUBIC)
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
            out.append(base64.standard_b64encode(buf.tobytes()).decode() if ok else None)
    return out


# ── chamadas à IA ────────────────────────────────────────────────────────────

def _msg(client, b64, prompt, schema):
    return client.messages.create(
        model=MODELO,
        max_tokens=3000,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )


def _texto(resp):
    """Extrai o bloco de texto com segurança (nunca deixa StopIteration vazar
    de dentro de um generator — PEP 479 viraria RuntimeError opaco)."""
    t = next((b.text for b in resp.content if b.type == "text"), None)
    if t is None:
        raise RuntimeError(f"resposta da IA sem texto (stop_reason={resp.stop_reason})")
    return t


def _ler_times(client, b64):
    """Lê os 8 confrontos (nomes impressos) da cartela — a âncora do prompt."""
    resp = _msg(client, b64,
                "Esta é uma cartela de bolão com 8 jogos (8 linhas). A primeira linha "
                "TAMBÉM é um jogo (não é cabeçalho). Liste os 8 confrontos NA ORDEM, "
                "com o nome IMPRESSO de cada time: casa (coluna esquerda) e fora "
                "(coluna direita). Ignore marcas de caneta.", _SCHEMA_TIMES)
    texto = _texto(resp)
    jogos = json.loads(texto).get("jogos", [])
    times = [(j.get("casa", "").strip(), j.get("fora", "").strip()) for j in jogos[:8]]
    usage = (resp.usage.input_tokens, resp.usage.output_tokens)
    return times, usage


def _prompt_marcas(times):
    linhas = "\n".join(f"{i+1}. {c}  x  {f}" for i, (c, f) in enumerate(times))
    return f"""Esta é UMA cartela de bolão com EXATAMENTE 8 jogos (8 linhas). A tabela NÃO tem linha de cabeçalho — a primeira linha ({times[0][0]} x {times[0][1]}) É o jogo 1.

Os 8 confrontos, na ordem das linhas, são:
{linhas}

Layout de cada linha: [caixinha] TIME DA CASA | [caixinha] TIME VISITANTE | [caixinha]

Em cada linha há UMA caixinha marcada à mão. A marca pode ter QUALQUER formato:
uma barra "/", um "X", um "V" (visto/tique), um círculo "O" ou "⭕", um risco,
um rabisco, um preenchimento, um ponto — QUALQUER traço de caneta dentro de uma
caixinha CONTA COMO MARCA. Não ignore círculos nem "X": eles marcam a caixinha
igual a uma barra. A caixinha marcada define a resposta:
- caixinha ANTES do time da casa (borda esquerda) -> "C"
- caixinha do MEIO (entre os dois times) -> "E"
- caixinha DEPOIS do visitante (borda direita) -> "F"

Regras:
- Escolha a caixinha que tiver QUALQUER marca de caneta, seja qual for o formato.
- Se houver marca em mais de uma, escolha a mais forte/escura.
- Use "?" SÓ se a linha estiver realmente em branco (nenhum traço em nenhuma caixinha).

Para CADA um dos 8 jogos listados acima, localize a linha pelo nome do time da casa e diga em qual caixinha está a marca.
Responda no JSON: jogos = lista de 8 itens {{time_casa, marca}}, na ordem 1..8."""


def _ler_cartela(client, b64, prompt):
    resp = _msg(client, b64, prompt, _SCHEMA_MARCAS)
    texto = _texto(resp)
    js = json.loads(texto).get("jogos", [])
    marcas = [j.get("marca", "?") for j in js][:8]
    marcas += ["?"] * (8 - len(marcas))
    return marcas, (resp.usage.input_tokens, resp.usage.output_tokens)


def _anchor(client, crops):
    """Monta o prompt-âncora lendo os 8 confrontos. Tenta VÁRIAS cartelas da
    folha (0, 1, 2 e 12) — em campo, a cartela 1 pode estar borrada/cortada e
    uma falha aqui não pode condenar a folha inteira."""
    tin = tout = 0
    for i in (0, 1, 2, 12):
        if i >= len(crops) or crops[i] is None:
            continue
        try:
            times, u = _ler_times(client, crops[i])
            tin += u[0]; tout += u[1]
            if len(times) >= 8:
                return _prompt_marcas(times), tin, tout
        except Exception:
            continue
    raise RuntimeError("IA não identificou os 8 jogos da folha "
                       "(tentei 4 cartelas diferentes)")


def hybrid_read_pages(path: str, plan: dict,
                      progress_cb: Optional[Callable[[int, int, str], None]] = None):
    """MODO HÍBRIDO: lê pela IA só o que o plano pedir, UMA leitura por cartela
    (o OCR local grátis já é a primeira opinião; concordância confirma,
    divergência vira revisão — consenso caro de 2-3 leituras não é preciso).

    plan: {página(1-based): "all" | [índices 0-based de cartelas duvidosas]}
    Retorna ({(página, idx): [8 marcas C/E/F/?]}, custo_usd, erro|None)."""
    import anthropic
    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=8)

    out = {}
    tin = tout = 0
    erro = None
    puladas = []
    reaproveitadas = 0
    cache = _load_cache()
    fk = _file_key(path)
    total_cartelas = sum(24 if v == "all" else len(v) for v in plan.values())
    feitas = [0]
    for page_num, img in _page_images_highres(path, set(plan.keys())):
        wanted = plan[page_num]
        idxs = list(range(24)) if wanted == "all" else sorted(wanted)

        # RESUME: cartelas já no cache (leitura anterior) não vão pra API
        for i in list(idxs):
            ck = f"{fk}|{page_num}|{i}"
            if ck in cache:
                out[(page_num, i)] = cache[ck]
                idxs.remove(i)
                feitas[0] += 1
                reaproveitadas += 1
        if not idxs:
            continue  # página inteira já estava no cache — custo zero

        try:
            img = _prepare_highres(img)
            crops = _crops_24(img)
            if not crops or crops[0] is None:
                raise RuntimeError("falha ao recortar cartelas")
            prompt, t_in, t_out = _anchor(client, crops)
            tin += t_in; tout += t_out

            def um(i):
                marcas, u2 = _ler_cartela(client, crops[i], prompt)
                feitas[0] += 1
                if progress_cb:
                    progress_cb(feitas[0], max(1, total_cartelas),
                                f"IA (híbrido): cartela {feitas[0]}/{total_cartelas}...")
                return i, marcas, u2

            with ThreadPoolExecutor(max_workers=3) as ex:
                for i, marcas, u2 in ex.map(um, idxs):
                    tin += u2[0]; tout += u2[1]
                    out[(page_num, i)] = marcas
                    cache[f"{fk}|{page_num}|{i}"] = marcas
            _save_cache(cache)   # grava por página: se cair na próxima, não perde
        except (RuntimeError, ValueError, KeyError) as e:
            # problema DESTA página (recorte/âncora/leitura) — pula só ela e
            # segue: a página fica com a leitura local. Antes, a página 1
            # falhar ABORTAVA o híbrido inteiro (bug pego em campo: 61 folhas
            # ficaram no local por causa de uma).
            puladas.append(page_num)
            if progress_cb:
                progress_cb(feitas[0], max(1, total_cartelas),
                            f"IA pulou a página {page_num} ({e}) — segue local nela...")
            continue
        except Exception as e:
            # sistêmico (rede/chave/limite): não adianta insistir nas próximas
            erro = f"{type(e).__name__}: {e}"
            break

    _save_cache(cache)
    if reaproveitadas and progress_cb:
        progress_cb(1, 1, f"IA: {reaproveitadas} cartela(s) reaproveitadas do "
                          "cache (leitura anterior — sem custo).")
    if puladas and erro is None and progress_cb:
        progress_cb(1, 1, f"IA: página(s) {puladas} ficaram na leitura local.")
    usd = tin / 1e6 * 3.0 + tout / 1e6 * 15.0
    return out, usd, erro


# ── MODO LOTE (Batch API) ────────────────────────────────────────────────────
# Mesma leitura do híbrido, porém ASSÍNCRONA e 50% mais barata: manda todas as
# cartelas num lote só, a IA processa "quando tem espaço" (minutos, teto 24h) e
# devolve tudo junto. Encaixa no bolão (processa no fim, não em tempo real). O
# id do lote fica salvo por arquivo — se o app cair/fechar, rodar de novo RETOMA
# o mesmo lote em vez de reenviar (e repagar).

def use_batch() -> bool:
    """True se o modo lote está ligado (config 'batch')."""
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return bool(json.load(f).get("batch"))
    except Exception:
        return False


def set_use_batch(v: bool) -> None:
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg["batch"] = bool(v)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _batch_state_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "ai_batch.json")


def _load_batch_state() -> dict:
    try:
        with open(_batch_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_batch_state(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_batch_state_path()), exist_ok=True)
        with open(_batch_state_path(), "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


# teto de espera do poll (o app não pode ficar preso pra sempre). Ao estourar,
# o id do lote continua salvo → rodar de novo retoma de onde parou.
_BATCH_POLL_SECS = 20
_BATCH_MAX_WAIT_SECS = 2 * 60 * 60   # 2h de espera ativa; depois "rode de novo"


def batch_read_pages(path: str, plan: dict,
                     progress_cb: Optional[Callable[[int, int, str], None]] = None):
    """Igual a `hybrid_read_pages`, mas pela Batch API (50% do custo, assíncrono).
    Mesma assinatura/retorno — é um drop-in. Retoma lote pendente do mesmo arquivo."""
    import time
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=8)
    cache = _load_cache()
    fk = _file_key(path)
    out = {}

    # RESUME por cache (cartelas já lidas antes não voltam pro lote)
    reaproveitadas = 0
    for page_num, wanted in plan.items():
        idxs = list(range(24)) if wanted == "all" else sorted(wanted)
        for i in idxs:
            ck = f"{fk}|{page_num}|{i}"
            if ck in cache:
                out[(page_num, i)] = cache[ck]
                reaproveitadas += 1
    pending = {}
    for page_num, wanted in plan.items():
        idxs = list(range(24)) if wanted == "all" else sorted(wanted)
        rem = [i for i in idxs if (page_num, i) not in out]
        if rem:
            pending[page_num] = rem

    def _cid(p, i):
        return f"c_{p}_{i}"

    def _parse_cid(s):
        _, p, i = s.split("_")
        return int(p), int(i)

    usd = 0.0
    state = _load_batch_state()
    batch_id = state.get(fk)

    # Nada pra ler e nenhum lote pendente → já resolvido pelo cache
    if not pending and not batch_id:
        return out, 0.0, None

    # 1) Cria o lote (se não houver um pendente deste arquivo)
    if not batch_id:
        if progress_cb:
            progress_cb(0, 1, "Modo lote: preparando cartelas...")
        prompt = None
        requests = []
        for page_num, img in _page_images_highres(path, set(pending.keys())):
            big = _prepare_highres(img)
            crops = _crops_24(big)
            if prompt is None:                 # 1 âncora p/ o arquivo todo
                try:                            # (mesma rodada = mesmos 8 times)
                    prompt, ti, to = _anchor(client, crops)
                    usd += ti / 1e6 * 3.0 + to / 1e6 * 15.0   # âncora é chamada normal
                except Exception as e:
                    return out, usd, f"âncora: {type(e).__name__}"
            for i in pending.get(page_num, []):
                if i < len(crops) and crops[i] is not None:
                    requests.append(Request(
                        custom_id=_cid(page_num, i),
                        params=MessageCreateParamsNonStreaming(
                            model=MODELO, max_tokens=3000,
                            output_config={"format": {"type": "json_schema",
                                                      "schema": _SCHEMA_MARCAS}},
                            messages=[{"role": "user", "content": [
                                {"type": "image", "source": {
                                    "type": "base64", "media_type": "image/jpeg",
                                    "data": crops[i]}},
                                {"type": "text", "text": prompt},
                            ]}],
                        )))
        if not requests:
            return out, usd, None
        try:
            batch = client.messages.batches.create(requests=requests)
        except Exception as e:
            return out, usd, f"lote (envio): {type(e).__name__}"
        batch_id = batch.id
        state[fk] = batch_id
        _save_batch_state(state)
        if progress_cb:
            progress_cb(0, 1, f"Lote enviado: {len(requests)} cartelas. Aguardando "
                              "a IA processar (pode levar de minutos até ~1h)...")

    # 2) Espera concluir (ativo, com teto). Se estourar, mantém o id salvo.
    waited = 0
    try:
        while True:
            b = client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended":
                break
            rc = getattr(b, "request_counts", None)
            if progress_cb and rc is not None:
                prontas = rc.succeeded + rc.errored + rc.canceled + rc.expired
                progress_cb(0, 1, f"Lote processando... {prontas} cartela(s) prontas "
                                  "(pode fechar e voltar depois — retoma sozinho)")
            if waited >= _BATCH_MAX_WAIT_SECS:
                return out, usd, ("lote ainda processando — abra de novo mais "
                                  "tarde pra pegar o resultado (nada foi perdido)")
            time.sleep(_BATCH_POLL_SECS)
            waited += _BATCH_POLL_SECS
    except Exception as e:
        return out, usd, f"lote (espera): {type(e).__name__}"

    # 3) Coleta os resultados (chegam fora de ordem → mapeia por custom_id)
    try:
        for result in client.messages.batches.results(batch_id):
            try:
                p, i = _parse_cid(result.custom_id)
            except Exception:
                continue
            if result.result.type == "succeeded":
                msg = result.result.message
                texto = next((bl.text for bl in msg.content if bl.type == "text"), None)
                if not texto:
                    continue
                js = json.loads(texto).get("jogos", [])
                marcas = [j.get("marca", "?") for j in js][:8]
                marcas += ["?"] * (8 - len(marcas))
                out[(p, i)] = marcas
                cache[f"{fk}|{p}|{i}"] = marcas
                usd += (msg.usage.input_tokens / 1e6 * 3.0
                        + msg.usage.output_tokens / 1e6 * 15.0) * 0.5   # -50% no lote
    except Exception as e:
        return out, usd, f"lote (resultado): {type(e).__name__}"

    _save_cache(cache)
    state.pop(fk, None)
    _save_batch_state(state)
    if progress_cb:
        extra = f" · custo ~R$ {usd*5.4:.2f}" if out else ""
        progress_cb(1, 1, "Lote concluído (metade do preço)" + extra)
    return out, usd, None


def read_photo_sheet(path: str,
                     progress_cb: Optional[Callable[[int, int, str], None]] = None
                     ) -> List[CardResult]:
    """Lê todas as páginas pela IA; exceção em falha (compatibilidade)."""
    results, _, erro = read_photo_sheet_partial(path, progress_cb)
    if erro is not None:
        raise RuntimeError(erro)
    return results


def read_photo_sheet_partial(path: str,
                             progress_cb: Optional[Callable[[int, int, str], None]] = None,
                             pages=None):
    """Lê o máximo que conseguir pela IA (só as páginas em `pages`, se dado).

    Retorna (results, paginas_concluidas: set, erro|None). Num arquivo grande,
    se a IA parar no meio (limite de velocidade da API, crédito, rede), as
    páginas JÁ LIDAS são aproveitadas e o pipeline continua as restantes no
    OCR local — antes, uma falha na página 27 jogava fora as 26 anteriores."""
    import anthropic
    # contas novas têm limite baixo de tokens/minuto: retries pacientes (o SDK
    # respeita o retry-after do servidor) e menos threads simultâneas
    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=8)

    results: List[CardResult] = []
    tin = tout = 0
    paginas_ok = set()
    erro = None
    for page_num, img in _page_images_highres(path, pages):
        try:
            if progress_cb:
                progress_cb(0, 25, f"IA: localizando a folha (pág {page_num})...")
            img = _prepare_highres(img)
            crops = _crops_24(img)
            if not crops or crops[0] is None:
                raise RuntimeError("falha ao recortar cartelas")

            if progress_cb:
                progress_cb(0, 25, f"IA: lendo os times (pág {page_num})...")
            prompt, t_in, t_out = _anchor(client, crops)
            tin += t_in; tout += t_out

            feitos = [0]

            def um(idx_b64):
                """CONSENSO: lê 2x; jogo divergente vai pra 3ª leitura (maioria).

                Marcas limítrofes flipam entre leituras (não-determinismo) — em
                contexto de dinheiro, divergência nunca pode virar erro silencioso:
                2x iguais = aceita; maioria 2/3 = aceita COM flag; sem maioria = "?"."""
                idx, b64 = idx_b64
                m1, u1 = _ler_cartela(client, b64, prompt)
                m2, u2 = _ler_cartela(client, b64, prompt)
                tin2, tout2 = u1[0] + u2[0], u1[1] + u2[1]
                marcas, duvida = [], []
                if m1 != m2 or "?" in m1:
                    m3, u3 = _ler_cartela(client, b64, prompt)
                    tin2 += u3[0]; tout2 += u3[1]
                    for a, b, c in zip(m1, m2, m3):
                        votos = [v for v in (a, b, c) if v != "?"]
                        win = max(set(votos), key=votos.count) if votos else "?"
                        n = votos.count(win)
                        if n >= 3:
                            marcas.append(win); duvida.append(False)
                        elif n == 2:
                            marcas.append(win); duvida.append(True)   # maioria: aceita + flag
                        else:
                            marcas.append("?"); duvida.append(True)
                else:
                    marcas = m1
                    duvida = [v == "?" for v in m1]
                feitos[0] += 1
                if progress_cb:
                    progress_cb(feitos[0], 25,
                                f"IA: cartela {feitos[0]}/24 (pág {page_num})...")
                return idx, (marcas, duvida), (tin2, tout2)

            with ThreadPoolExecutor(max_workers=3) as ex:
                for idx, (marcas, duvida), u in ex.map(um, list(enumerate(crops))):
                    tin += u[0]; tout += u[1]
                    marks = []
                    for g, (mv, dv) in enumerate(zip(marcas, duvida)):
                        ch = _CH.get(mv)
                        marks.append(MarkResult(
                            game=g, choice=ch,
                            confidence=(0.0 if ch is None else (0.6 if dv else 0.95)),
                            needs_review=dv or ch is None,
                            raw_scores=[0, 0, 0]))
                    results.append(CardResult(
                        card_index=idx, page=page_num, marks=marks,
                        has_review_flags=any(m.needs_review for m in marks)))
            paginas_ok.add(page_num)
        except Exception as e:
            # aproveita as páginas completas; descarta a página incompleta
            results = [c for c in results if c.page in paginas_ok]
            erro = f"{type(e).__name__}: {e}"
            break

    usd = tin / 1e6 * 3.0 + tout / 1e6 * 15.0
    if progress_cb:
        progress_cb(25, 25,
                    f"IA: {len(results)} cartelas lidas · custo ~R$ {usd*5.4:.2f}")
    results.sort(key=lambda c: (c.page, c.card_index))
    return results, paginas_ok, erro
