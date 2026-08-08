# -*- coding: utf-8 -*-
"""
Teste v2 — leitura por IA de visão, UMA CARTELA POR CHAMADA (recorte grande).

Valida contra gabarito visual (cartelas 1-3 conferidas no olho) e mede o
custo real por cartela -> extrapola o custo por folha (24 cartelas).
"""

import base64
import json
import os
import sys

PDF = r"C:\Users\Administrator\Downloads\pag1.pdf"
BRL_POR_USD = 5.40

PRECOS = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}
MODELOS = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"]

# gabarito conferido visualmente (zoom 6x) nas cartelas 1-3
GT = {
    1: list("CCECFFCE"),
    2: list("FCECEEEC"),
    3: list("CECCEFFC"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "jogos": {"type": "array",
                  "items": {"type": "string", "enum": ["C", "E", "F", "?"]}},
    },
    "required": ["jogos"],
    "additionalProperties": False,
}

PROMPT = """Esta é UMA cartela de bolão de futebol com 8 jogos (8 linhas da tabela).

Layout de cada linha: [caixinha] TIME DA CASA | [caixinha] TIME VISITANTE | [caixinha]
O apostador marcou À CANETA (um risco "/" ou X) exatamente UMA caixinha por linha:
- caixinha à ESQUERDA do time da casa -> "C"
- caixinha do MEIO (imediatamente à esquerda do time visitante) -> "E"
- caixinha à DIREITA do time visitante (borda direita da tabela) -> "F"

Leia as 8 linhas de cima para baixo. Se não der para ler, use "?".
Responda no JSON: {"jogos": [8 valores "C"/"E"/"F"/"?"]}."""


def crops():
    """Recorta as cartelas 1-3 (linha de cima) em alta resolução."""
    import fitz
    doc = fitz.open(PDF)
    p = doc[0]
    r = p.rect
    w4, h6 = r.width / 4, r.height / 6
    out = {}
    for n, cx in [(1, 0), (2, 1), (3, 2)]:
        clip = fitz.Rect(cx * w4, 0, (cx + 1) * w4 + 10, h6 + 10)
        pix = p.get_pixmap(matrix=fitz.Matrix(6, 6), clip=clip)
        out[n] = base64.standard_b64encode(pix.tobytes("jpeg", jpg_quality=88)).decode()
    doc.close()
    return out


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERRO: defina ANTHROPIC_API_KEY")
        sys.exit(1)
    import anthropic
    client = anthropic.Anthropic()

    imgs = crops()
    total_usd = 0.0
    print(f"{'modelo':22s} {'acertos':>9s} {'tok/cartela':>12s} {'R$/folha(24)':>13s} {'R$ folha lote':>14s}")
    resumo = {}
    for modelo in MODELOS:
        certo = tot = e_in = e_out = 0
        detal = []
        for n, b64 in imgs.items():
            resp = client.messages.create(
                model=modelo,
                max_tokens=2000,
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64",
                                                     "media_type": "image/jpeg",
                                                     "data": b64}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
            texto = next(b.text for b in resp.content if b.type == "text")
            jogos = (json.loads(texto).get("jogos", []) + ["?"] * 8)[:8]
            e_in += resp.usage.input_tokens
            e_out += resp.usage.output_tokens
            hits = sum(1 for a, b in zip(GT[n], jogos) if a == b)
            certo += hits
            tot += 8
            detal.append(f"   cartela {n}: IA={''.join(jogos)} GT={''.join(GT[n])} -> {hits}/8")
        n_cartelas = len(imgs)
        p_in, p_out = PRECOS[modelo]
        usd_teste = e_in / 1e6 * p_in + e_out / 1e6 * p_out
        total_usd += usd_teste
        # custo por folha = média por cartela × 24
        usd_folha = usd_teste / n_cartelas * 24
        brl_folha = usd_folha * BRL_POR_USD
        resumo[modelo] = (certo, tot, brl_folha)
        print(f"{modelo:22s} {certo:>5d}/{tot:<3d} {(e_in+e_out)//n_cartelas:>12d} "
              f"{brl_folha:>12.2f} {brl_folha/2:>13.2f}")
        for d in detal:
            print(d)
    print(f"\nCusto deste teste: US$ {total_usd:.4f} (~R$ {total_usd*BRL_POR_USD:.2f})")


if __name__ == "__main__":
    main()
