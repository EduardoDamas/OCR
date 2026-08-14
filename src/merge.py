"""
Combine cartelas exported from several PCs/files into one session, so the final
ranking covers everyone — the 2-PC collaborative workflow (split the sheets,
each person reviews their part, then merge here for the winners).
"""

import re

from .omr.trainer import _load_rows, _tok_choice
from .omr.recognizer import MarkResult, CardResult
from . import database as db

# "Pág 5 #20" dentro do rótulo de origem — pra RENUMERAR as páginas de forma
# contínua ao juntar arquivos (senão o 2º arquivo volta pra "Pág 1" e duplica).
_PAGE_RE = re.compile(r"P[áa]g\.?\s*(\d+)\s*#\s*(\d+)", re.IGNORECASE)


_JOGO_RE = re.compile(r"jogo\s*0*(\d+)$", re.IGNORECASE)


def _first_text(pre):
    """Participante = 1ª célula de texto (não-número) antes dos jogos."""
    return next(
        (c for c in pre
         if not c.replace("-", "").replace(" ", "").replace("#", "").isdigit()
         and len(c) >= 2),
        pre[0] if pre else "")


def _game_columns(rows):
    """Se houver cabeçalho com 'Jogo 1'..'Jogo 8', devolve (índices das 8 colunas
    de jogo POR POSIÇÃO, índice da coluna Participante/rótulo, índice da coluna
    Nome). Ler por posição é o que mantém um jogo EM BRANCO no lugar certo. A coluna
    "Nome" (nome do apostador da cartela digital) é SEPARADA do rótulo — preserva o
    nome ao juntar arquivos, sem sujar o "Pág X #Y"."""
    for row in rows:
        cols = {}
        for i, c in enumerate(row):
            m = _JOGO_RE.match(str(c).strip())
            if m:
                cols[int(m.group(1))] = i
        if all(g in cols for g in range(1, 9)):
            part = next((i for i, c in enumerate(row)
                         if re.search(r"particip|apostador", str(c), re.IGNORECASE)),
                        None)
            nome = next((i for i, c in enumerate(row)
                         if re.fullmatch(r"\s*nome\s*", str(c), re.IGNORECASE)),
                        None)
            if part is None:            # arquivo antigo/3º sem "Participante": o
                part = nome             # rótulo cai na própria coluna "Nome"
            return [cols[g] for g in range(1, 9)], part, nome
    return None, None, None


def parse_cards(path):
    """Parse an exported CSV/Excel into [{participant, choices[8]}] rows.

    Os 8 jogos são lidos POR POSIÇÃO (coluna), não "os tokens que existem": um
    jogo em BRANCO fica None NAQUELA posição. Antes as células vazias eram
    descartadas antes de indexar, então um jogo em branco no meio (ex.: o cliente
    esqueceu o jogo 4) sumia e os jogos seguintes subiam uma casa — o branco
    "escorregava" pro jogo 8. Agora cada coluna guarda o seu valor."""
    rows = [[str(c).strip() for c in row] for row in _load_rows(path)]
    gcols, pcol, ncol = _game_columns(rows)
    cards = []
    for cells in rows:
        nome = ""
        if gcols is not None:                  # cabeçalho achado → lê por coluna
            choices = [(_tok_choice(cells[i]) if i < len(cells) else None) for i in gcols]
            part = cells[pcol] if (pcol is not None and pcol < len(cells)) else ""
            # nome do apostador (coluna "Nome") — só se for coluna SEPARADA do rótulo
            if ncol is not None and ncol != pcol and ncol < len(cells):
                nome = cells[ncol]
            # NÃO exige nº mínimo de marcas: uma cartela com poucos jogos (ou até
            # nenhum) marcados AINDA é cartela — antes o corte "< 4 marcas" a
            # derrubava do ranking e bagunçava a numeração das outras. Pula só o
            # cabeçalho ("Participante"/"Jogo N") e linhas totalmente vazias.
            is_header = (part.lower() in ("participante", "nome", "apostador")
                         or any(_JOGO_RE.match(cells[i]) for i in gcols if i < len(cells)))
            n_choice = sum(c is not None for c in choices)
            if is_header or (not part and n_choice == 0):
                continue
            participant = part or _first_text(cells[:min(gcols)])
        else:                                  # sem cabeçalho: 8 colunas seguidas
            idx = [i for i, c in enumerate(cells) if _tok_choice(c) is not None]
            if len(idx) < 2:                   # header/ruído (sem coluna, precisa de sinal)
                continue
            first = idx[0]                      # começa no 1º jogo e lê 8 em ordem,
            choices = [(_tok_choice(cells[i]) if i < len(cells) else None)  # vazio=None
                       for i in range(first, first + 8)]
            participant = _first_text(cells[:first])
        cards.append({"participant": participant, "choices": choices, "nome": nome or ""})
    return cards


def import_file(session_id, path, source_label="", db_path=None):
    """Add every cartela from an exported file into the session. Returns count.

    Páginas CONTÍNUAS: ao juntar um 2º arquivo, as páginas dele seguem depois das
    que já existem (ex.: 1-5 → o próximo vira 6-10), em vez de voltar pra 1. Sem
    isso a numeração duplicava e ficava confuso pra conferir no Excel/ranking."""
    parsed = parse_cards(path)
    existing = db.get_cards(session_id, db_path)
    base = max([c["card_index"] for c in existing], default=-1) + 1
    base_page = max([c.get("page", 1) for c in existing], default=0)

    # RENUMERAÇÃO CONTÍNUA (escolha do cliente): ao juntar um arquivo, as páginas
    # dele CONTINUAM logo depois das que já existem (ex.: já tem até a pág 40 → o
    # próximo arquivo vira 41, 42...). O 1º arquivo mantém a própria numeração.
    # Antes fazia `base_page + P`, o que SOMAVA os números: arquivo até 40 + outro
    # que começa na 54 dava 94 (bug reportado). Agora desloca pelo MENOR número do
    # arquivo, então o 1º card dele cai exatamente em base_page+1, sem pulo.
    file_pages = [int(m.group(1)) for pc in parsed
                  if (m := _PAGE_RE.search(pc["participant"] or ""))]
    file_min = min(file_pages) if file_pages else 1
    page_shift = 0 if base_page == 0 else (base_page - file_min + 1)

    results = []
    for i, pc in enumerate(parsed):
        marks = [MarkResult(game=g, choice=pc["choices"][g], confidence=1.0,
                            needs_review=pc["choices"][g] is None,
                            raw_scores=[0, 0, 0]) for g in range(8)]
        mp = _PAGE_RE.search(pc["participant"] or "")
        if mp:                                   # "Pág P #Q" → desloca P, mantém Q
            page = int(mp.group(1)) + page_shift
            label = f"Pág {page} #{int(mp.group(2))}"   # renumerado, sem duplicar
        else:                                    # nome real: mantém, 24 por página
            page = base_page + (i // 24) + 1
            label = pc["participant"] or f"Pág {page} #{i % 24 + 1}"
        if source_label:
            label = f"{source_label} · {label}"
        results.append(CardResult(card_index=base + i, page=page, marks=marks,
                                  has_review_flags=any(m.needs_review for m in marks),
                                  participant=label, nome=(pc.get("nome") or None)))
    db.save_card_results(session_id, results, db_path)
    return len(results)
