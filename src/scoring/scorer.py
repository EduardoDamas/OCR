"""
Scoring engine: compare each card's marks against official results,
compute points, and rebuild the ranking.
"""

from typing import Callable, Dict, List, Optional
from .. import database as db_mod
from pathlib import Path

# Official-result codes: 0=Casa, 1=Empate, 2=Fora.
# A game can also be voided — it then counts for nobody and is dropped from the
# maximum possible score (e.g. a cancelled match → the round is worth 7 instead
# of 8).
ANNULLED = 3


def score_card(marks: List[Dict], official: Dict[int, int]) -> int:
    """
    Count how many games the card got right.
    marks: list of game_marks rows (dicts with 'game' and 'choice').
    official: {game_index: correct_result}.  A game whose result is ANNULLED is
    skipped — nobody scores it.
    """
    points = 0
    for m in marks:
        game = m["game"]
        choice = m["choice"]
        result = official.get(game)
        if result is None or result == ANNULLED:
            continue
        if choice is not None and choice == result:
            points += 1
    return points


def valid_games_count(official: Dict[int, int]) -> int:
    """Number of resolved games that actually count (entered and not annulled)."""
    return sum(1 for r in official.values() if r != ANNULLED)


def score_distribution(session_id: int, db_path: Path = None) -> Dict:
    """
    Live "parcial": how many cards have each score, plus the current maximum
    possible (= resolved, non-annulled games).  Used by the Parcial button.
    """
    ranking = db_mod.get_ranking(session_id, db_path)
    official = db_mod.get_official_results(session_id, db_path)
    valid = valid_games_count(official)

    dist: Dict[int, int] = {}
    for r in ranking:
        s = r["score"]
        dist[s] = dist.get(s, 0) + 1

    return {
        "total_cards": len(ranking),
        "valid_games": valid,
        "distribution": [{"score": s, "count": dist[s]}
                         for s in sorted(dist, reverse=True)],
    }


def simulate(session_id: int, db_path: Path = None) -> Dict:
    """
    "E se…?" simulation for the games not yet decided.

    Looks at the current leaders (cards tied at the highest score among the
    already-resolved games) and, for EACH still-open game, counts how many of
    them marked Casa / Empate / Fora.  When a single game is left, those counts
    ARE the winner counts for each possible result.

    Returns: {pending, resolved, max_score, n_leaders, per_game, leaders}.
    """
    official = db_mod.get_official_results(session_id, db_path)
    resolved = {g: r for g, r in official.items()
                if r is not None and r != ANNULLED}
    annulled = {g for g, r in official.items() if r == ANNULLED}
    pending = [g for g in range(8) if g not in resolved and g not in annulled]

    cards = db_mod.get_cards(session_id, db_path)
    marks_by_card = db_mod.get_marks_for_session(session_id, db_path)

    rows = []
    for card in cards:
        mbg = {m["game"]: m["choice"] for m in marks_by_card.get(card["id"], [])}
        score = sum(1 for g, r in resolved.items() if mbg.get(g) == r)
        label = card.get("participant") or f"Pág {card['page']} #{card['card_index'] + 1}"
        rows.append({"label": label, "score": score, "marks": mbg})

    max_score = max((r["score"] for r in rows), default=0)
    leaders = [r for r in rows if r["score"] == max_score]

    per_game: Dict[int, Dict] = {}
    for g in pending:
        counts = {0: 0, 1: 0, 2: 0, None: 0}
        for r in leaders:
            c = r["marks"].get(g)
            counts[c if c in (0, 1, 2) else None] += 1
        per_game[g] = counts

    return {
        "pending": pending,
        "resolved": len(resolved),
        "max_score": max_score,
        "n_leaders": len(leaders),
        "per_game": per_game,
        "leaders": leaders,
    }


def simulate_outcome(session_id: int, chosen: Dict, db_path: Path = None) -> Dict:
    """"E se…?" com resultado ESCOLHIDO pelo usuário: ele define Casa/Empate/Fora
    pros jogos que faltam (o palpite / o que tem na cartela dele) e a gente diz
    quantas cartelas seriam CAMPEÃS com esse placar hipotético (somado aos jogos
    já oficiais) e QUAIS são.

    `chosen`: {game(0-based): choice 0/1/2}. Jogos anulados e já apurados são
    ignorados (o oficial manda). Retorna {n_winners, max_score, total_games,
    winners:[{label, score}]}.
    """
    official = db_mod.get_official_results(session_id, db_path)
    resolved = {g: r for g, r in official.items()
                if r is not None and r != ANNULLED}
    annulled = {g for g, r in official.items() if r == ANNULLED}

    # placar final hipotético = oficiais resolvidos + a escolha do usuário
    final = dict(resolved)
    for g, c in (chosen or {}).items():
        g = int(g)
        if c in (0, 1, 2) and g not in annulled and g not in resolved:
            final[g] = c

    cards = db_mod.get_cards(session_id, db_path)
    marks_by_card = db_mod.get_marks_for_session(session_id, db_path)
    rows = []
    for card in cards:
        mbg = {m["game"]: m["choice"] for m in marks_by_card.get(card["id"], [])}
        score = sum(1 for g, r in final.items() if mbg.get(g) == r)
        label = card.get("participant") or f"Pág {card['page']} #{card['card_index'] + 1}"
        rows.append({"label": label, "score": score})

    max_score = max((r["score"] for r in rows), default=0)
    winners = sorted((r for r in rows if r["score"] == max_score),
                     key=lambda r: r["label"])
    return {
        "n_winners": len(winners),
        "max_score": max_score,
        "total_games": len(final),
        "winners": winners[:300],       # cap p/ não estourar a tela/JSON
    }


def recalculate_ranking(session_id: int, db_path: Path = None,
                        progress_cb: Optional[Callable[[int, int], None]] = None
                        ) -> List[Dict]:
    """
    Re-score every card in the session and update the ranking table.
    Returns the updated ranking list.

    progress_cb(current, total) is called during scoring if provided.
    Marks are batch-loaded (one query) and scores written in one transaction,
    so this stays fast even for large sessions.
    """
    official = db_mod.get_official_results(session_id, db_path)
    cards = db_mod.get_cards(session_id, db_path)
    marks_by_card = db_mod.get_marks_for_session(session_id, db_path)

    total = len(cards)
    card_scores = []
    for i, card in enumerate(cards):
        marks = marks_by_card.get(card["id"], [])
        card_scores.append((card["id"], score_card(marks, official)))
        if progress_cb and (i % 100 == 0 or i == total - 1):
            progress_cb(i + 1, total)

    db_mod.upsert_rankings_bulk(session_id, card_scores, db_path)
    return db_mod.get_ranking(session_id, db_path)


def set_result_and_rank(session_id: int, game: int, result: int,
                        db_path: Path = None) -> List[Dict]:
    """
    Convenience: persist a single official result then recalculate everything.
    Returns updated ranking.
    """
    db_mod.set_official_result(session_id, game, result, db_path)
    return recalculate_ranking(session_id, db_path)


def ranking_to_display(ranking: List[Dict], marks_by_card: Dict = None) -> List[Dict]:
    """
    Enrich ranking rows with position, a display label and the CHOICES (o que a
    cartela marcou em cada jogo) — usados pela tabela estilo Excel e pela grade
    que abre ao clicar numa cartela. `marks_by_card` = {card_id: [mark,...]}.
    Handles ties (same position for equal scores).
    """
    marks_by_card = marks_by_card or {}
    enriched = []
    pos = 0
    prev_score = None
    for i, row in enumerate(ranking):
        if row["score"] != prev_score:
            pos = i + 1
            prev_score = row["score"]
        label = row.get("participant") or f"Pág {row['page']} #{row['card_index'] + 1}"
        choices = row.get("choices")
        if choices is None and row.get("card_id") in marks_by_card:
            mm = {m["game"]: m["choice"] for m in marks_by_card[row["card_id"]]}
            choices = [mm.get(g) for g in range(8)]
        enriched.append({
            **row,
            "position": pos,
            "label": label,
            "choices": choices,
        })
    return enriched
