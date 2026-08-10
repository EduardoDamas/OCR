"""
Flask web server: serves the real-time ranking page.
Uses Server-Sent Events (SSE) to push updates to browsers.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request, render_template
from flask_cors import CORS

from .. import database as db_mod
from ..scoring.scorer import (recalculate_ranking, ranking_to_display,
                              set_result_and_rank, score_distribution, simulate,
                              simulate_outcome)

# Global SSE subscriber queues
_subscribers: list = []
_subscribers_lock = threading.Lock()

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# Senha SÓ do painel de lançar resultado (/admin e o POST de resultados). O
# ranking (ver a classificação) fica PÚBLICO — é o que os clientes acessam. Com
# o site exposto na internet (túnel), sem isto qualquer um que achasse a URL
# mexeria no placar. Basic Auth: o navegador guarda a senha e reenvia sozinho.
ADMIN_KEY = os.environ.get("BOLAO_ADMIN_KEY", "").strip()


@app.before_request
def _guard_admin():
    if not ADMIN_KEY:
        return None                              # sem senha configurada = aberto
    protegido = (request.path == "/admin"
                 or (request.path in ("/api/results", "/api/publish")
                     and request.method == "POST"))
    if protegido:
        auth = request.authorization
        if not auth or (auth.password or auth.username) != ADMIN_KEY:
            return Response("Painel protegido — informe a senha.", 401,
                            {"WWW-Authenticate": 'Basic realm="Painel do Bolao"'})
    return None


@app.after_request
def _no_cache(resp):
    # ranking é AO VIVO — nunca cachear, senão o celular fica com a versão antiga
    # (o cliente abriu o link e "não atualizou" porque o navegador guardou a página).
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

# Active session ID (set by desktop app before starting server)
_active_session_id: Optional[int] = None
_db_path: Optional[Path] = None


def configure(session_id: int, db_path: Path = None) -> None:
    global _active_session_id, _db_path
    _active_session_id = session_id
    _db_path = db_path


def _broadcast(data: dict) -> None:
    payload = f"data: {json.dumps(data)}\n\n"
    with _subscribers_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _session_name() -> str:
    """Nome/dia da rodada da sessão ativa (ex.: 'Bolão de Quarta — 13/08') —
    mostrado no topo do ranking pra o cliente saber de qual rodada é."""
    if _active_session_id is None:
        return ""
    try:
        s = db_mod.get_session(_active_session_id, _db_path)
        return (s or {}).get("name") or ""
    except Exception:
        return ""


def _push_ranking_update() -> None:
    if _active_session_id is None:
        return
    ranking = db_mod.get_ranking(_active_session_id, _db_path)
    official = db_mod.get_official_results(_active_session_id, _db_path)
    marks = db_mod.get_marks_for_session(_active_session_id, _db_path)
    display = ranking_to_display(ranking, marks)
    _broadcast({
        "type": "ranking",
        "ranking": display,
        "official": official,
        "total_games": 8,
        "games": db_mod.get_session_games(_active_session_id, _db_path),
        "round": _session_name(),
    })


# ── SSE endpoint ──────────────────────────────────────────────────────────────

@app.route("/stream")
def stream():
    """SSE endpoint — each connected browser gets a dedicated queue."""
    q: queue.Queue = queue.Queue(maxsize=50)
    with _subscribers_lock:
        _subscribers.append(q)

    def generate():
        try:
            # Send current state immediately on connect
            if _active_session_id is not None:
                ranking = db_mod.get_ranking(_active_session_id, _db_path)
                official = db_mod.get_official_results(_active_session_id, _db_path)
                marks = db_mod.get_marks_for_session(_active_session_id, _db_path)
                display = ranking_to_display(ranking, marks)
                games = db_mod.get_session_games(_active_session_id, _db_path)
                yield f"data: {json.dumps({'type': 'ranking', 'ranking': display, 'official': official, 'total_games': 8, 'games': games, 'round': _session_name()})}\n\n"

            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _subscribers_lock:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/api/ranking")
def api_ranking():
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    ranking = db_mod.get_ranking(_active_session_id, _db_path)
    official = db_mod.get_official_results(_active_session_id, _db_path)
    marks = db_mod.get_marks_for_session(_active_session_id, _db_path)
    return jsonify({
        "ranking": ranking_to_display(ranking, marks),
        "official": official,
        "games": db_mod.get_session_games(_active_session_id, _db_path),
        "round": _session_name(),
    })


@app.route("/api/parcial")
def api_parcial():
    """Live score distribution: how many cards have each score (8, 7, 6…)."""
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    return jsonify(score_distribution(_active_session_id, _db_path))


@app.route("/api/simular")
def api_simular():
    """"E se…?": for the leaders, how many win under each result of the open games."""
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    sim = simulate(_active_session_id, _db_path)
    per_game = [{"game": g, "casa": sim["per_game"][g][0],
                 "empate": sim["per_game"][g][1], "fora": sim["per_game"][g][2],
                 "blank": sim["per_game"][g][None]} for g in sim["pending"]]
    leaders = [{"label": r["label"],
                "picks": {str(g): r["marks"].get(g) for g in sim["pending"]}}
               for r in sim["leaders"]]
    return jsonify({
        "pending": sim["pending"], "resolved": sim["resolved"],
        "max_score": sim["max_score"], "n_leaders": sim["n_leaders"],
        "per_game": per_game, "leaders": leaders,
    })


@app.route("/api/simular_outcome", methods=["POST"])
def api_simular_outcome():
    """"E se…?" interativo: recebe {chosen:{game:choice}} do usuário e devolve
    quantas cartelas seriam campeãs (e quais) com esse placar hipotético."""
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    data = request.get_json(silent=True) or {}
    chosen = data.get("chosen") or {}
    return jsonify(simulate_outcome(_active_session_id, chosen, _db_path))


@app.route("/api/results", methods=["POST"])
def api_set_result():
    """Set an official game result and push updated ranking."""
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    data = request.get_json(force=True)
    game = int(data["game"])
    result = int(data["result"])
    # 0=Casa 1=Empate 2=Fora 3=Anulado
    if not (0 <= game <= 7) or result not in (0, 1, 2, 3):
        return jsonify({"error": "invalid input"}), 400

    ranking = set_result_and_rank(_active_session_id, game, result, _db_path)
    _push_ranking_update()
    return jsonify({"ok": True, "ranking": ranking_to_display(ranking)})


@app.route("/api/publish", methods=["POST"])
def api_publish():
    """Recebe uma sessão inteira (bundle do desktop) e a publica como a sessão
    ATIVA da nuvem — é assim que a rodada do dia aparece no link fixo 24h.
    Protegido por senha (Basic Auth) no _guard_admin."""
    global _db_path
    bundle = request.get_json(force=True, silent=True)
    if not bundle or not isinstance(bundle, dict):
        return jsonify({"error": "bundle invalido"}), 400
    try:
        sid = db_mod.import_session_bundle(bundle, _db_path)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    configure(sid, _db_path)            # vira a sessão ativa servida no ranking
    _push_ranking_update()              # empurra pros celulares já conectados
    return jsonify({"ok": True, "session_id": sid,
                    "cartelas": len(bundle.get("cards", []))})


@app.route("/api/results", methods=["GET"])
def api_get_results():
    if _active_session_id is None:
        return jsonify({}), 404
    official = db_mod.get_official_results(_active_session_id, _db_path)
    return jsonify(official)


@app.route("/api/session")
def api_session():
    if _active_session_id is None:
        return jsonify({"error": "no session"}), 404
    session = db_mod.get_session(_active_session_id, _db_path)
    return jsonify(session or {})


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("ranking.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


# ── Server start ──────────────────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start_server(host: str = "0.0.0.0", port: int = 5000,
                 session_id: int = None, db_path: Path = None) -> str:
    """Start the Flask server in a background daemon thread. Returns the URL."""
    global _server_thread
    if session_id is not None:
        configure(session_id, db_path)

    if _server_thread and _server_thread.is_alive():
        return f"http://localhost:{port}"

    def _run():
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host=host, port=port, threaded=True, use_reloader=False)

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    time.sleep(0.5)
    return f"http://localhost:{port}"


def notify_update() -> None:
    """Call this whenever the DB changes (e.g., after processing a file)."""
    _push_ranking_update()
