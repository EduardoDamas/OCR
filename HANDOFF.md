# Restaurar o projeto em outra máquina (VPS/PC) — Bolão OCR

Guia pra continuar o trabalho num computador novo. **Este arquivo NÃO contém
segredos** (o repo é público). As chaves ficam em 3 arquivos separados, que você
copia à parte — ver a seção "Segredos" no fim.

## 1. Código

```bash
git clone https://github.com/EduardoDamas/OCR.git
cd OCR
```

## 2. Rodar o PROGRAMA (desktop / OCR) — precisa de Windows + Python 3.12

```bash
pip install -r requirements.txt          # deps completas (opencv, pymupdf, etc.)
python main.py                           # abre a interface
```
Build do .exe: `python -m PyInstaller BolaoOCR.spec --noconfirm` → `dist/BolaoOCR.exe`.
Publicar o .exe pra download: cria um GitHub Release (ver DEPLOY/notas) — link fica
`https://github.com/EduardoDamas/OCR/releases/download/vXX/BolaoOCR_vXX.exe`.

## 3. Rodar o SITE de ranking (nuvem 24h)

O site NÃO faz OCR — só serve o ranking. Deps enxutas:
```bash
pip install -r requirements-web.txt      # Flask + gunicorn
```
Deploy detalhado em **`DEPLOY.md`**. Resumo do que já está no ar:
- Provedor: **Render** · Serviço: `bolao-ranking` · id `srv-d9rlie2fngtc73dltnfg`
- URL: **https://bolao-ranking.onrender.com** · painel: `.../admin`
- Plano: **free** (hiberna; mantido acordado pelo GitHub Action `.github/workflows/keepalive.yml`)
- Variáveis de ambiente no Render (definidas no painel, NÃO no código):
  - `BOLAO_ADMIN_KEY` — senha do `/admin` e do `/api/publish`
  - `BOLAO_DB` — caminho do sqlite (hoje `data/bolao.db`)
- Deploy é **manual** (webhook GitHub→Render não conectado): após dar push,
  disparar `POST https://api.render.com/v1/services/srv-d9rlie2fngtc73dltnfg/deploys`
  (com a chave do Render) e depois **republicar** a sessão pelo botão do programa.

## 4. Como a rodada chega no site
Programa (desktop) processa a rodada → botão **"☁️ Publicar no link fixo"** →
manda pro `/api/publish` da nuvem (Basic Auth com a `BOLAO_ADMIN_KEY`). A cada
redeploy o disco do plano free zera → é só publicar de novo.

## 5. Testes
```bash
python -m pytest tests/ -q               # deve dar ~33 passed
```

## 6. Segredos (NÃO estão neste repo — transferir à parte, com segurança)
Estes arquivos são **gitignored** de propósito. Copie-os direto pra máquina nova
(pendrive, scp, gerenciador de senhas) — **nunca** faça commit deles:

| Arquivo | O que é | Onde regerar se precisar |
|---|---|---|
| `.gh-token.txt` | Token do GitHub (push/releases) | github.com → Settings → Developer settings → Personal access tokens |
| `.render-key.txt` | Chave de API do Render (deploy) | dashboard.render.com → Account Settings → API Keys |
| `data/ai_config.json` | Chave da IA (Anthropic, leitura de caneta) | console.anthropic.com → API Keys |
| `data/bolao.db` | Banco (rodadas + NOMES dos apostadores) | recriado ao processar/publicar |

O mapa de contas/e-mails/senha fica no arquivo LOCAL `_CREDENCIAIS_LOCAL.md`
(também gitignored) — não vai pro GitHub.

> ⚠️ A chave da Anthropic (`sk-ant-…`) foi exposta num chat — **revogue e gere uma
> nova** no console.anthropic.com antes de usar na máquina nova.
