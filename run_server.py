"""Serve the Bolao ranking app for a live tunnel (Cloudflare quick tunnel).

Publico (clientes veem): a classificacao (/ , /api/ranking, /stream).
Com senha (voce lanca resultado): /admin e o POST de resultados.

Uso:
    python run_server.py            -> serve a sessao MAIS RECENTE do banco
    python run_server.py 7          -> serve a sessao 7
Depois, em outra janela:
    cloudflared.exe tunnel --url http://localhost:5000
e compartilhe a URL https://xxxx.trycloudflare.com/ com os clientes.
"""
import os
import sys
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault('BOLAO_ADMIN_KEY', 'bolao4729')   # senha do /admin

DB = HERE / 'data' / 'bolao.db'


def _latest_session():
    con = sqlite3.connect(str(DB))
    try:
        row = con.execute('SELECT id FROM sessions ORDER BY id DESC LIMIT 1').fetchone()
        return row[0] if row else None
    finally:
        con.close()


session_id = int(sys.argv[1]) if len(sys.argv) > 1 else _latest_session()

from src.web import app as webapp
webapp.configure(session_id, DB)
print(f'Servindo sessao {session_id} em http://localhost:5000')
print(f'  ranking (publico):  http://localhost:5000/')
print(f'  admin (senha {os.environ["BOLAO_ADMIN_KEY"]}): http://localhost:5000/admin')
webapp.app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
