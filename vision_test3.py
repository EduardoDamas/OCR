# -*- coding: utf-8 -*-
"""v3 — ancora cada linha pelo NOME DO TIME (mata o erro de deslocamento)."""
import base64, json, os, sys

PDF = r"C:\Users\Administrator\Downloads\pag1.pdf"
BRL = 5.40
PRECOS = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5": (3.0, 15.0), "claude-opus-4-8": (5.0, 25.0)}
MODELOS = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"]
GT = {1: list("CCECFFCE"), 2: list("FCECEEEC"), 3: list("CECCEFFC")}

TIMES = ["ESPANHA", "ATLAS - MEX", "ALIANZA ATL. - PER", "FERNANDO - PAR",
         "JUVENTUDE - RS", "LEONES - EQU", "SPORT - PE", "TECNICO - EQU"]

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

def crops():
    import fitz
    doc = fitz.open(PDF); p = doc[0]; r = p.rect
    w4, h6 = r.width/4, r.height/6
    out = {}
    for n, cx in [(1,0),(2,1),(3,2)]:
        clip = fitz.Rect(cx*w4, 0, (cx+1)*w4+10, h6+10)
        pix = p.get_pixmap(matrix=fitz.Matrix(6,6), clip=clip)
        out[n] = base64.standard_b64encode(pix.tobytes("jpeg", jpg_quality=88)).decode()
    doc.close(); return out

def main():
    import anthropic
    client = anthropic.Anthropic()
    imgs = crops(); total = 0.0
    for modelo in MODELOS:
        certo = tot = ein = eout = 0; det = []
        for n, b64 in imgs.items():
            resp = client.messages.create(model=modelo, max_tokens=3000,
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": PROMPT}]}])
            texto = next(b.text for b in resp.content if b.type == "text")
            js = json.loads(texto).get("jogos", [])
            jogos = [j.get("marca", "?") for j in js][:8] + ["?"]*(8-len(js))
            ein += resp.usage.input_tokens; eout += resp.usage.output_tokens
            hits = sum(1 for a, b in zip(GT[n], jogos) if a == b)
            certo += hits; tot += 8
            det.append(f"   cartela {n}: IA={''.join(jogos)} GT={''.join(GT[n])} -> {hits}/8")
        pin, pout = PRECOS[modelo]
        usd = ein/1e6*pin + eout/1e6*pout; total += usd
        brl_folha = usd/len(imgs)*24*BRL
        print(f"{modelo:22s} {certo:2d}/{tot}  R$/folha={brl_folha:.2f}  lote={brl_folha/2:.2f}")
        for d in det: print(d)
    print(f"\nCusto deste teste: US$ {total:.4f} (~R$ {total*BRL:.2f})")

main()
