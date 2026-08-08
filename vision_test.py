# -*- coding: utf-8 -*-
"""
Teste real de leitura por IA de visão (folhas de CANETA fotografadas).

Mede, numa folha real (pag1.pdf):
  1. PRECISÃO  — leitura da IA vs. leitura do OCR local (+ divergências p/ conferir no olho)
  2. CUSTO     — tokens exatos cobrados -> custo por folha em US$ e R$

Uso:
  set ANTHROPIC_API_KEY=sk-ant-...   (a chave da SUA conta)
  python vision_test.py              (roda Haiku=barato e Opus=forte por padrão)
  python vision_test.py claude-haiku-4-5              (só um modelo)
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PDF = r"C:\Users\Administrator\Downloads\pag1.pdf"
BRL_POR_USD = 5.40

# preço US$ por 1M tokens (entrada, saída) — API Anthropic
PRECOS = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}
MODELOS_PADRAO = ["claude-haiku-4-5", "claude-opus-4-8"]

SCHEMA = {
    "type": "object",
    "properties": {
        "cartelas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "jogos": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["C", "E", "F", "?"]},
                    },
                },
                "required": ["n", "jogos"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cartelas"],
    "additionalProperties": False,
}

PROMPT = """Esta é a foto de uma folha de bolão de futebol com 24 cartelas (4 colunas x 6 linhas, numeradas 1..24 em ordem de leitura: linha por linha, da esquerda para a direita).

Cada cartela tem 8 jogos (linhas). Em cada jogo há duas colunas de times (time da casa à esquerda, visitante à direita) e o apostador marcou À CANETA um risco/X em UMA das 3 posições:
- caixinha à ESQUERDA do time da casa  -> "C" (Casa)
- caixinha do MEIO, entre os dois times -> "E" (Empate)
- caixinha à DIREITA do visitante       -> "F" (Fora)

Leia as marcações de caneta de TODAS as 24 cartelas, jogo por jogo (8 por cartela).
Se realmente não der para ler uma marcação, use "?".
Responda no JSON pedido: cartelas[n=1..24].jogos = lista de 8 valores "C"/"E"/"F"/"?"."""


def render_page(pdf_path, long_edge=2500, quality=88):
    import fitz

    doc = fitz.open(pdf_path)
    page = doc[0]
    r = page.rect
    zoom = long_edge / max(r.width, r.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = pix.tobytes("jpeg", jpg_quality=quality)
    doc.close()
    return img


def leitura_local():
    """Leitura do OCR atual (offline) para comparação, em letras C/E/F/?."""
    from src.omr import pipeline
    from src.omr import trainer

    trainer._data_dir = lambda: r"C:\NONEXISTENT"  # sem modelo de usuário
    ch = {0: "C", 1: "E", 2: "F", None: "?"}
    res = pipeline.process_file(PDF, dpi=300)
    out = {}
    for c in res:
        if c.page == 1:
            out[c.card_index + 1] = [ch[m.choice] for m in c.marks]
    return out


def testar(modelo, img_b64, local):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=modelo,
        max_tokens=16000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    texto = next(b.text for b in resp.content if b.type == "text")
    dados = json.loads(texto)
    ia = {c["n"]: (c["jogos"] + ["?"] * 8)[:8] for c in dados.get("cartelas", [])}

    # custo exato
    e_in, e_out = resp.usage.input_tokens, resp.usage.output_tokens
    p_in, p_out = PRECOS[modelo]
    usd = e_in / 1e6 * p_in + e_out / 1e6 * p_out
    brl = usd * BRL_POR_USD

    # comparação com o OCR local
    igual = diff = so_ia = 0
    divergencias = []
    for n in range(1, 25):
        a, b = local.get(n), ia.get(n)
        if not a or not b:
            continue
        for g in range(8):
            if a[g] == "?" and b[g] != "?":
                so_ia += 1          # IA leu onde o OCR local ficou em branco
            elif a[g] == b[g]:
                igual += 1
            else:
                diff += 1
                divergencias.append((n, g + 1, a[g], b[g]))

    print(f"\n===== {modelo} =====")
    print(f"tokens: entrada={e_in}  saida={e_out}")
    print(f"CUSTO DESTA FOLHA: US$ {usd:.4f}  =  R$ {brl:.3f}"
          f"   (em lote/batch: ~R$ {brl/2:.3f})")
    print(f"vs OCR local: {igual} iguais | {so_ia} lidas so pela IA (OCR em branco) | {diff} divergentes")
    if divergencias:
        print("divergencias (cartela, jogo, OCR->IA)  — conferir no olho na folha:")
        for n, g, a, b in divergencias[:20]:
            print(f"   cartela {n:2d} jogo {g}: {a} -> {b}")
        if len(divergencias) > 20:
            print(f"   ... e mais {len(divergencias)-20}")

    # salva a leitura da IA p/ conferência
    saida = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"leitura_ia_{modelo.replace('-', '_')}.csv")
    with open(saida, "w", encoding="utf-8") as f:
        f.write("Cartela;j1;j2;j3;j4;j5;j6;j7;j8\n")
        for n in range(1, 25):
            f.write(f"{n};" + ";".join(ia.get(n, ['?'] * 8)) + "\n")
    print(f"leitura salva em: {saida}")
    return usd


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERRO: defina a chave primeiro ->  set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    modelos = sys.argv[1:] or MODELOS_PADRAO
    print("Renderizando a folha em alta resolucao...")
    img_b64 = base64.standard_b64encode(render_page(PDF)).decode()
    print(f"Imagem: {len(img_b64) * 3 // 4 // 1024} KB")
    print("Rodando OCR local para comparacao...")
    local = leitura_local()
    total = 0.0
    for m in modelos:
        try:
            total += testar(m, img_b64, local)
        except Exception as e:
            print(f"\n===== {m} =====\nFALHOU: {e}")
    print(f"\nCusto total do teste: US$ {total:.4f} (~R$ {total * BRL_POR_USD:.2f})")


if __name__ == "__main__":
    main()
