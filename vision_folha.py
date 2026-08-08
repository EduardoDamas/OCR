# -*- coding: utf-8 -*-
"""Leitura da FOLHA INTEIRA (24 cartelas) com Sonnet 5, 1 cartela por chamada."""
import base64, json, os, sys

PDF = r"C:\Users\Administrator\Downloads\pag1.pdf"
BRL = 5.40
MODELO = "claude-sonnet-5"; P_IN, P_OUT = 3.0, 15.0

SCHEMA = {"type": "object", "properties": {"jogos": {"type": "array", "items": {
    "type": "object", "properties": {
        "time_casa": {"type": "string"},
        "marca": {"type": "string", "enum": ["C", "E", "F", "?"]}},
    "required": ["time_casa", "marca"], "additionalProperties": False}}},
    "required": ["jogos"], "additionalProperties": False}

PROMPT = """Esta é UMA cartela de bolão com EXATAMENTE 8 jogos (8 linhas). A tabela NÃO tem linha de cabeçalho — a primeira linha (ESPANHA x BÉLGICA) É o jogo 1.

Os 8 times da casa, na ordem das linhas, são:
1. ESPANHA  2. ATLAS-MEX  3. ALIANZA ATL.-PER  4. FERNANDO-PAR
5. JUVENTUDE-RS  6. LEONES-EQU  7. SPORT-PE  8. TÉCNICO-EQU

Layout de cada linha: [caixinha] TIME DA CASA | [caixinha] TIME VISITANTE | [caixinha]
Há UMA marca de caneta (risco / ou X) por linha:
- caixinha ANTES do time da casa (borda esquerda) -> "C"
- caixinha do MEIO (entre os dois times) -> "E"
- caixinha DEPOIS do visitante (borda direita) -> "F"

Para CADA um dos 8 times da casa listados acima, localize a linha dele e diga onde está a marca.
Responda no JSON: jogos = lista de 8 itens {time_casa, marca}, na ordem 1..8."""

import fitz, anthropic
client = anthropic.Anthropic()
doc = fitz.open(PDF); p = doc[0]; r = p.rect
w4, h6 = r.width/4, r.height/6
ein = eout = 0
leitura = {}
for row in range(6):
    for col in range(4):
        n = row*4 + col + 1
        clip = fitz.Rect(col*w4 - 3, row*h6 - 3, (col+1)*w4 + 10, (row+1)*h6 + 12)
        pix = p.get_pixmap(matrix=fitz.Matrix(6, 6), clip=clip)
        b64 = base64.standard_b64encode(pix.tobytes("jpeg", jpg_quality=88)).decode()
        resp = client.messages.create(model=MODELO, max_tokens=3000,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT}]}])
        texto = next(b.text for b in resp.content if b.type == "text")
        js = json.loads(texto).get("jogos", [])
        jogos = [j.get("marca", "?") for j in js][:8] + ["?"]*(8-len(js))
        leitura[n] = jogos
        ein += resp.usage.input_tokens; eout += resp.usage.output_tokens
        print(f"cartela {n:2d}: {''.join(jogos)}", flush=True)
doc.close()
usd = ein/1e6*P_IN + eout/1e6*P_OUT
print(f"\nFOLHA COMPLETA: tokens in={ein} out={eout}")
print(f"CUSTO REAL DA FOLHA: US$ {usd:.4f} = R$ {usd*BRL:.2f}  (lote: R$ {usd*BRL/2:.2f})")
interr = sum(1 for v in leitura.values() for x in v if x == '?')
print(f"jogos ilegiveis (?): {interr}/192")
with open("leitura_ia_folha_completa.csv", "w", encoding="utf-8") as f:
    f.write("Cartela;j1;j2;j3;j4;j5;j6;j7;j8\n")
    for n in range(1, 25):
        f.write(f"{n};" + ";".join(leitura.get(n, ['?']*8)) + "\n")
print("salvo: leitura_ia_folha_completa.csv")
