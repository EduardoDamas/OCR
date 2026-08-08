# Link FIXO 24h — como colocar o ranking na nuvem

O ranking passa a ter **um endereço único que nunca muda** e fica no ar **mesmo com o PC desligado**. O programa do PC continua fazendo o OCR e, no fim, **publica** a rodada na nuvem (botão `☁️ Publicar no link fixo`).

Como funciona: a nuvem NÃO faz OCR — só mostra o ranking. Ela começa vazia; o desktop manda a rodada atual (≈1 MB por rodada) pro endpoint `/api/publish`. Deps enxutas (`requirements-web.txt`): só Flask + gunicorn.

## Deploy no Render (recomendado — tem disco persistente)

1. Suba este repositório no GitHub (ou conecte a pasta).
2. No [Render](https://render.com): **New → Blueprint** e aponte pro repo. Ele lê o `render.yaml` (serviço web + disco de 1 GB pro sqlite).
   - Ou **New → Web Service** manual:
     - Build: `pip install -r requirements-web.txt`
     - Start: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --threads 8 --timeout 120`
     - Disk: monte um disco em `/var/data` (o sqlite precisa persistir).
3. **Environment** → adicione:
   - `BOLAO_ADMIN_KEY` = uma senha forte (protege `/admin` e `/api/publish`).
   - `BOLAO_DB` = `/var/data/bolao.db`
4. Deploy. Vai sair uma URL tipo `https://bolao-ranking.onrender.com`.
5. **Domínio próprio** (opcional): Render → Settings → Custom Domain → aponte seu domínio (ex.: `bolao.seudominio.com`). Aí o link fixo vira o seu domínio.

## No programa do PC (a cada rodada)

1. Processe a rodada normalmente e lance/confira os resultados.
2. Clique **`☁️ Publicar no link fixo (nuvem 24h)`**.
3. Cole a **URL** da nuvem (ex.: `https://bolao.seudominio.com`) e a **senha** (`BOLAO_ADMIN_KEY`). Fica salvo pra próxima.
4. Pronto — a rodada aparece no link fixo na hora, e você lança/edita resultado pelo celular em `SEU-LINK/admin` (mesma senha).

## Notas

- **Segurança:** o ranking é público (só "Pág X #Y", sem nome do apostador); `/admin` e `/api/publish` exigem a senha. Nunca coloque a `BOLAO_ADMIN_KEY` no código — só na env do host.
- **Outros hosts** (Railway/Fly/VPS): use o `Procfile` (`web: gunicorn wsgi:app ...`) e as mesmas 2 variáveis de ambiente; garanta um **volume persistente** pro `bolao.db`.
- **Alternativa sem nuvem** (Cloudflare Named Tunnel + domínio): URL fixa e grátis, mas **depende do PC ligado**. A nuvem acima é o "fixo de verdade".
