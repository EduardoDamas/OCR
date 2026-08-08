"""Unit tests for the scoring engine."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scoring.scorer import score_card, ranking_to_display


class TestScoreCard:
    def test_perfect_score(self):
        official = {0: 0, 1: 1, 2: 2, 3: 0, 4: 1, 5: 2, 6: 0, 7: 1}
        marks = [{"game": g, "choice": v} for g, v in official.items()]
        assert score_card(marks, official) == 8

    def test_zero_score(self):
        official = {0: 0, 1: 1, 2: 2}
        marks = [{"game": 0, "choice": 1},
                 {"game": 1, "choice": 2},
                 {"game": 2, "choice": 0}]
        assert score_card(marks, official) == 0

    def test_partial_score(self):
        official = {0: 0, 1: 1, 2: 2}
        marks = [{"game": 0, "choice": 0},   # correct
                 {"game": 1, "choice": 2},   # wrong
                 {"game": 2, "choice": None}] # unknown
        assert score_card(marks, official) == 1

    def test_unknown_game_skipped(self):
        official = {0: 0}
        marks = [{"game": 0, "choice": 0}, {"game": 1, "choice": 1}]
        assert score_card(marks, official) == 1


class TestRankingDisplay:
    def test_positions_assigned(self):
        ranking = [
            {"card_id": 1, "score": 8, "participant": "Alice", "page": 1, "card_index": 0},
            {"card_id": 2, "score": 6, "participant": "Bob",   "page": 1, "card_index": 1},
            {"card_id": 3, "score": 6, "participant": None,    "page": 1, "card_index": 2},
        ]
        display = ranking_to_display(ranking)
        assert display[0]["position"] == 1
        assert display[1]["position"] == 2
        assert display[2]["position"] == 2   # tie

    def test_label_fallback(self):
        ranking = [{"card_id": 1, "score": 5, "participant": None,
                    "page": 2, "card_index": 3}]
        display = ranking_to_display(ranking)
        assert "Pág 2" in display[0]["label"]


class TestSimulateOutcome:
    """Simulação interativa: o usuário escolhe o resultado dos jogos que faltam
    e vê quem seria campeão (soma com os já oficiais)."""

    def _setup(self, tmp_path):
        from src import database as db
        from src.omr.recognizer import MarkResult, CardResult
        p = tmp_path / "sim.db"
        db.init_db(p)
        sid = db.create_session("t", db_path=p)

        def card(idx, picks):
            marks = [MarkResult(game=g, choice=picks[g], confidence=1.0,
                                needs_review=False, raw_scores=[0, 0, 0])
                     for g in range(8)]
            return CardResult(card_index=idx, page=1, marks=marks,
                              has_review_flags=False, participant=f"C{idx}")
        # jogos 0-4 iguais; divergem em 5,6,7
        db.save_card_results(sid, [card(0, [0, 0, 0, 0, 0, 0, 1, 2]),
                                   card(1, [0, 0, 0, 0, 0, 0, 1, 0]),
                                   card(2, [0, 0, 0, 0, 0, 2, 2, 2])], p)
        for g in range(5):
            db.set_official_result(sid, g, 0, p)   # 0-4 = Casa (todos 5 pts)
        return sid, p

    def test_chosen_outcome_picks_matching_card(self, tmp_path):
        from src.scoring.scorer import simulate_outcome
        sid, p = self._setup(tmp_path)
        out = simulate_outcome(sid, {5: 0, 6: 1, 7: 2}, p)   # bate com C0
        assert out["max_score"] == 8
        assert [w["label"] for w in out["winners"]] == ["C0"]

    def test_different_outcome_different_winner(self, tmp_path):
        from src.scoring.scorer import simulate_outcome
        sid, p = self._setup(tmp_path)
        out = simulate_outcome(sid, {5: 2, 6: 2, 7: 2}, p)   # bate com C2
        assert [w["label"] for w in out["winners"]] == ["C2"]
        assert out["n_winners"] == 1
