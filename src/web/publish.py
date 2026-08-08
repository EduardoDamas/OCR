"""Publicar a sessão atual no servidor FIXO da nuvem (link 24h).

O desktop grava o banco LOCAL; o servidor da nuvem começa vazio. Este módulo
empacota a sessão e a manda pro `/api/publish` da nuvem — só a rodada atual
(KBs), não o arquivo .db inteiro. Usa só a stdlib (urllib) pra não precisar de
`requests` no exe.
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data", "cloud_config.json")


def get_cloud_config() -> dict:
    """{'url': ..., 'key': ...} do servidor fixo (ou vazio)."""
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            c = json.load(f)
            return {"url": (c.get("url") or "").strip(), "key": (c.get("key") or "").strip()}
    except Exception:
        return {"url": "", "key": ""}


def set_cloud_config(url: str, key: str) -> None:
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"url": (url or "").strip(), "key": (key or "").strip()}, f)


def publish_to_cloud(cloud_url: str, admin_key: str, session_id: int,
                     db_path=None, timeout: int = 60) -> dict:
    """Envia a sessão pro servidor fixo. Retorna o JSON de resposta
    (ex.: {ok: True, session_id, cartelas}). Levanta exceção com msg amigável."""
    from .. import database as db_mod

    bundle = db_mod.export_session_bundle(session_id, db_path)
    if bundle is None:
        raise ValueError("Sessão não encontrada para publicar.")

    url = cloud_url.strip().rstrip("/") + "/api/publish"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    data = json.dumps(bundle).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if admin_key:
        token = base64.b64encode(f":{admin_key}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)  # senha do painel

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Senha do painel incorreta (Basic Auth).") from e
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise RuntimeError(f"Servidor recusou (HTTP {e.code}). {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Não consegui falar com o servidor: {e.reason}") from e
