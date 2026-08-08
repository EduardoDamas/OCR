"""Ponto de entrada da NUVEM (link fixo 24h) — servido por gunicorn: `wsgi:app`.

Config por variáveis de ambiente (definidas no painel do host):
  BOLAO_ADMIN_KEY  -> senha do /admin e do /api/publish (OBRIGATÓRIA em produção)
  BOLAO_DB         -> caminho do sqlite persistente (ex.: /var/data/bolao.db)
  PORT             -> porta (o host injeta; o Procfile usa $PORT)

A nuvem começa VAZIA; o desktop publica a rodada via /api/publish. Este processo
NÃO faz OCR — só serve o ranking (deps enxutas: Flask + gunicorn).
"""
import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get("BOLAO_DB", "data/bolao.db"))
DB.parent.mkdir(parents=True, exist_ok=True)

from src import database as db_mod
db_mod.init_db(DB)

from src.web import app as webapp


def _latest_session():
    try:
        con = sqlite3.connect(str(DB))
        row = con.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


webapp.configure(_latest_session(), DB)   # serve a última rodada já publicada
app = webapp.app                            # gunicorn procura por isto
