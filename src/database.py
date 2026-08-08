"""
SQLite persistence layer.
Tables: sessions, cards, games, results, ranking.
"""

import sqlite3
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

# "prefixo · Pág 5 #20" → separa prefixo / número da página / nº da cartela,
# pra renumerar as páginas mantendo a origem e a posição da cartela.
_PAG_LABEL = re.compile(r"^(?P<prefix>.*?)P[áa]g\.?\s*\d+\s*#\s*(?P<pos>\d+)\s*$",
                        re.IGNORECASE)


def _default_db_path() -> Path:
    """
    Location of the SQLite database.

    When running as a PyInstaller .exe, code lives in a temporary extraction
    dir that is wiped on exit — so the DB must live next to the executable
    instead, making it persistent and portable. In a normal source checkout it
    sits under the project's data/ folder.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent.parent
    return base / "data" / "bolao.db"


DB_PATH = _default_db_path()


def get_db_path() -> Path:
    return DB_PATH


def _connect(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db(db_path: Path = None):
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = None) -> None:
    """Create all tables if they do not exist."""
    with db(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            source_file TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            games_count INTEGER DEFAULT 8
        );

        CREATE TABLE IF NOT EXISTS cards (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            page        INTEGER NOT NULL,
            card_index  INTEGER NOT NULL,
            participant TEXT,
            has_review  INTEGER DEFAULT 0,
            raw_json    TEXT
        );

        CREATE TABLE IF NOT EXISTS game_marks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            game        INTEGER NOT NULL,
            choice      INTEGER,            -- 0=Casa 1=Empate 2=Fora NULL=unknown
            confidence  REAL,
            needs_review INTEGER DEFAULT 0,
            raw_scores  TEXT                -- JSON array
        );

        CREATE TABLE IF NOT EXISTS official_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            game        INTEGER NOT NULL,
            result      INTEGER NOT NULL,   -- 0=Casa 1=Empate 2=Fora
            UNIQUE(session_id, game)
        );

        CREATE TABLE IF NOT EXISTS ranking (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            score       INTEGER DEFAULT 0,
            UNIQUE(session_id, card_id)
        );
        """)
        # migração: confrontos da rodada (nomes dos times) — pra grade do ranking
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "games_json" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN games_json TEXT")


def set_session_games(session_id: int, games, db_path: Path = None) -> None:
    """Guarda os 8 confrontos [[casa,fora],…] da rodada na sessão."""
    import json
    with db(db_path) as conn:
        conn.execute("UPDATE sessions SET games_json=? WHERE id=?",
                     (json.dumps(games), session_id))


def get_session_games(session_id: int, db_path: Path = None):
    """Os 8 confrontos da rodada (ou None)."""
    import json
    with db(db_path) as conn:
        row = conn.execute("SELECT games_json FROM sessions WHERE id=?",
                           (session_id,)).fetchone()
    if row and row["games_json"]:
        try:
            return json.loads(row["games_json"])
        except Exception:
            return None
    return None


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(name: str, source_file: str = None,
                   games_count: int = 8, db_path: Path = None) -> int:
    with db(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO sessions (name, source_file, games_count) VALUES (?,?,?)",
            (name, source_file, games_count)
        )
        return cur.lastrowid


def list_sessions(db_path: Path = None) -> List[Dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_session(session_id: int, db_path: Path = None) -> Optional[Dict]:
    with db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Cards ─────────────────────────────────────────────────────────────────────

def save_card_results(session_id: int, card_results,
                      db_path: Path = None) -> None:
    """Persist a list of CardResult objects to the database."""
    with db(db_path) as conn:
        for cr in card_results:
            raw = json.dumps({
                "marks": [
                    {"game": m.game, "choice": m.choice,
                     "confidence": m.confidence, "raw_scores": m.raw_scores}
                    for m in cr.marks
                ]
            })
            cur = conn.execute(
                """INSERT INTO cards (session_id, page, card_index, participant,
                                      has_review, raw_json)
                   VALUES (?,?,?,?,?,?)""",
                (session_id, cr.page, cr.card_index,
                 getattr(cr, "participant", None),
                 int(cr.has_review_flags), raw)
            )
            card_id = cur.lastrowid
            for m in cr.marks:
                conn.execute(
                    """INSERT INTO game_marks
                       (card_id, game, choice, confidence, needs_review, raw_scores)
                       VALUES (?,?,?,?,?,?)""",
                    (card_id, m.game, m.choice, m.confidence,
                     int(m.needs_review), json.dumps(m.raw_scores))
                )


def get_cards(session_id: int, db_path: Path = None) -> List[Dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE session_id=? ORDER BY page, card_index",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_marks_for_card(card_id: int, db_path: Path = None) -> List[Dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM game_marks WHERE card_id=? ORDER BY game",
            (card_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_marks_for_session(session_id: int,
                          db_path: Path = None) -> Dict[int, List[Dict]]:
    """
    Batch-load every game_mark for a session in a single query, grouped by
    card_id.  Avoids the N+1 pattern of calling get_marks_for_card() per card
    (which opens a fresh connection each time).
    Returns {card_id: [mark_dict, ...]} with marks ordered by game.
    """
    with db(db_path) as conn:
        rows = conn.execute(
            """SELECT gm.*
               FROM game_marks gm
               JOIN cards c ON c.id = gm.card_id
               WHERE c.session_id = ?
               ORDER BY gm.card_id, gm.game""",
            (session_id,)
        ).fetchall()
    grouped: Dict[int, List[Dict]] = {}
    for r in rows:
        d = dict(r)
        grouped.setdefault(d["card_id"], []).append(d)
    return grouped


def update_mark(mark_id: int, choice: int, db_path: Path = None) -> None:
    """Manual correction from the review UI."""
    with db(db_path) as conn:
        conn.execute(
            "UPDATE game_marks SET choice=?, needs_review=0, confidence=1.0 WHERE id=?",
            (choice, mark_id)
        )


def set_participant_name(card_id: int, name: str, db_path: Path = None) -> None:
    with db(db_path) as conn:
        conn.execute(
            "UPDATE cards SET participant=? WHERE id=?", (name, card_id)
        )


def renumber_pages(session_id: int, start_page: int, db_path: Path = None) -> int:
    """Renumera as páginas da sessão pra começar em `start_page`, mantendo a
    estrutura (folha × cartela) e a origem. Ex.: páginas 1..14 com start=7 viram
    7..20; o rótulo "Pág 1 #3" vira "Pág 7 #3" (a cartela #3 continua a mesma).
    Serve pra bater com o número REAL da folha impressa. Retorna nº de cartelas."""
    cards = get_cards(session_id, db_path)
    if not cards:
        return 0
    min_page = min(c["page"] for c in cards)
    with db(db_path) as conn:
        for c in cards:
            new_page = start_page + (c["page"] - min_page)
            label = c["participant"] or ""
            m = _PAG_LABEL.match(label)
            if m:                              # "…Pág P #Q" → só troca P por new_page
                new_label = f"{m.group('prefix')}Pág {new_page} #{m.group('pos')}"
            else:                              # nome real: mantém, só muda a página
                new_label = label
            conn.execute("UPDATE cards SET page=?, participant=? WHERE id=?",
                         (new_page, new_label, c["id"]))
    return len(cards)


def source_labels(session_id: int, db_path: Path = None) -> List[str]:
    """Etiquetas de origem distintas em uso (o texto antes de ' · Pág')."""
    labels = []
    for c in get_cards(session_id, db_path):
        lbl = c["participant"] or ""
        if " · " in lbl:
            pref = lbl.split(" · ", 1)[0].strip()
            if pref and pref not in labels:
                labels.append(pref)
    return labels


def rename_source(session_id: int, old: str, new: str, db_path: Path = None) -> int:
    """Troca (ou remove, se `new` vazio) a etiqueta de origem 'old · ' de todas as
    cartelas — deixa o rótulo mais limpo sem mexer em página/cartela."""
    old_pref = f"{old} · "
    new_pref = f"{new.strip()} · " if new and new.strip() else ""
    n = 0
    with db(db_path) as conn:
        for c in get_cards(session_id, db_path):
            lbl = c["participant"] or ""
            if lbl.startswith(old_pref):
                conn.execute("UPDATE cards SET participant=? WHERE id=?",
                             (new_pref + lbl[len(old_pref):], c["id"]))
                n += 1
    return n


# ── Official results ───────────────────────────────────────────────────────────

def set_official_result(session_id: int, game: int, result: int,
                        db_path: Path = None) -> None:
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO official_results (session_id, game, result)
               VALUES (?,?,?)
               ON CONFLICT(session_id, game) DO UPDATE SET result=excluded.result""",
            (session_id, game, result)
        )


def get_official_results(session_id: int,
                         db_path: Path = None) -> Dict[int, int]:
    """Returns {game_index: result_int}."""
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT game, result FROM official_results WHERE session_id=?",
            (session_id,)
        ).fetchall()
        return {r["game"]: r["result"] for r in rows}


# ── Ranking ───────────────────────────────────────────────────────────────────

def upsert_ranking(session_id: int, card_id: int, score: int,
                   db_path: Path = None) -> None:
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO ranking (session_id, card_id, score) VALUES (?,?,?)
               ON CONFLICT(session_id, card_id) DO UPDATE SET score=excluded.score""",
            (session_id, card_id, score)
        )


def upsert_rankings_bulk(session_id: int, card_scores, db_path: Path = None) -> None:
    """
    Upsert many (card_id, score) pairs in a single transaction/connection.
    Avoids the per-card connection overhead of calling upsert_ranking in a loop.
    """
    with db(db_path) as conn:
        conn.executemany(
            """INSERT INTO ranking (session_id, card_id, score) VALUES (?,?,?)
               ON CONFLICT(session_id, card_id) DO UPDATE SET score=excluded.score""",
            [(session_id, cid, sc) for cid, sc in card_scores]
        )


def get_ranking(session_id: int, db_path: Path = None) -> List[Dict]:
    """Returns ranking rows ordered by score desc, including participant name."""
    with db(db_path) as conn:
        rows = conn.execute(
            """SELECT r.card_id, r.score, c.participant, c.page, c.card_index
               FROM ranking r
               JOIN cards c ON c.id = r.card_id
               WHERE r.session_id=?
               ORDER BY r.score DESC, c.page ASC, c.card_index ASC""",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]


# ── Publicar na nuvem: exportar/importar uma sessão inteira ──────────────────────
# O desktop grava o banco LOCAL; o servidor fixo (nuvem 24h) começa VAZIO. Pra o
# ranking aparecer lá, o desktop "publica" a sessão: empacota tudo (cartelas +
# marcas + confrontos + resultados) num JSON e manda pro `/api/publish` da nuvem,
# que recria a sessão e recalcula o ranking. Assim não precisa sincronizar o
# arquivo .db inteiro (208 MB) — só a rodada atual (KBs).

def export_session_bundle(session_id: int, db_path: Path = None) -> Optional[Dict]:
    """Empacota uma sessão inteira num dict serializável (pra mandar pra nuvem)."""
    s = get_session(session_id, db_path)
    if not s:
        return None
    marks = get_marks_for_session(session_id, db_path)   # {card_id: [mark,...]}
    out_cards = []
    for c in get_cards(session_id, db_path):
        cmarks = [{"game": m["game"], "choice": m["choice"],
                   "confidence": m["confidence"], "needs_review": m["needs_review"],
                   "raw_scores": m["raw_scores"]} for m in marks.get(c["id"], [])]
        out_cards.append({
            "page": c["page"], "card_index": c["card_index"],
            "participant": c["participant"], "has_review": c["has_review"],
            "marks": cmarks,
        })
    return {
        "name": s["name"],
        "games_count": s.get("games_count", 8),
        "games_json": get_session_games(session_id, db_path),
        "official_results": get_official_results(session_id, db_path),  # {game: result}
        "cards": out_cards,
    }


def import_session_bundle(bundle: Dict, db_path: Path = None) -> int:
    """Recria uma sessão a partir de um bundle (de export_session_bundle) e devolve
    o novo session_id. Usado pelo servidor da nuvem no /api/publish. Recalcula o
    ranking ao final para a classificação já vir pronta."""
    init_db(db_path)
    sid = create_session(bundle.get("name") or "Sessão", None,
                         int(bundle.get("games_count", 8)), db_path)
    if bundle.get("games_json"):
        set_session_games(sid, bundle["games_json"], db_path)
    with db(db_path) as conn:
        for c in bundle.get("cards", []):
            cur = conn.execute(
                """INSERT INTO cards (session_id, page, card_index, participant,
                                      has_review, raw_json) VALUES (?,?,?,?,?,?)""",
                (sid, c["page"], c["card_index"], c.get("participant"),
                 int(c.get("has_review", 0)), None))
            card_id = cur.lastrowid
            for m in c.get("marks", []):
                rs = m.get("raw_scores")
                if not isinstance(rs, str):
                    rs = json.dumps(rs)
                conn.execute(
                    """INSERT INTO game_marks (card_id, game, choice, confidence,
                                               needs_review, raw_scores)
                       VALUES (?,?,?,?,?,?)""",
                    (card_id, m["game"], m["choice"], m.get("confidence"),
                     int(m.get("needs_review", 0)), rs))
        for game, result in (bundle.get("official_results") or {}).items():
            conn.execute(
                """INSERT INTO official_results (session_id, game, result)
                   VALUES (?,?,?)
                   ON CONFLICT(session_id, game) DO UPDATE SET result=excluded.result""",
                (sid, int(game), int(result)))
    # recalcula o ranking na nuvem (import tardio evita ciclo com scorer)
    try:
        from .scoring.scorer import recalculate_ranking
        recalculate_ranking(sid, db_path)
    except Exception:
        pass
    return sid
