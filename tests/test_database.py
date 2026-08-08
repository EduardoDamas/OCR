"""Integration tests for the database layer using a temp in-memory DB."""

import sys, os, tempfile, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src import database as db_mod


@pytest.fixture
def tmp_db(tmp_path):
    path = tmp_path / "test.db"
    db_mod.init_db(path)
    return path


def _make_fake_card_result(page=1, card_index=0):
    from src.omr.recognizer import CardResult, MarkResult
    marks = [
        MarkResult(game=g, choice=g % 3, confidence=0.9,
                   needs_review=False, raw_scores=[0.8, 0.1, 0.1])
        for g in range(8)
    ]
    return CardResult(card_index=card_index, page=page,
                      marks=marks, has_review_flags=False)


class TestDatabase:
    def test_create_and_get_session(self, tmp_db):
        sid = db_mod.create_session("Teste", db_path=tmp_db)
        s = db_mod.get_session(sid, db_path=tmp_db)
        assert s["name"] == "Teste"
        assert s["id"] == sid

    def test_save_and_retrieve_cards(self, tmp_db):
        sid = db_mod.create_session("Teste", db_path=tmp_db)
        cards = [_make_fake_card_result(page=1, card_index=i) for i in range(3)]
        db_mod.save_card_results(sid, cards, db_path=tmp_db)
        retrieved = db_mod.get_cards(sid, db_path=tmp_db)
        assert len(retrieved) == 3

    def test_official_results_roundtrip(self, tmp_db):
        sid = db_mod.create_session("Teste", db_path=tmp_db)
        db_mod.set_official_result(sid, 0, 0, db_path=tmp_db)
        db_mod.set_official_result(sid, 1, 2, db_path=tmp_db)
        results = db_mod.get_official_results(sid, db_path=tmp_db)
        assert results[0] == 0
        assert results[1] == 2

    def test_upsert_ranking(self, tmp_db):
        sid = db_mod.create_session("Teste", db_path=tmp_db)
        cards = [_make_fake_card_result()]
        db_mod.save_card_results(sid, cards, db_path=tmp_db)
        card_id = db_mod.get_cards(sid, db_path=tmp_db)[0]["id"]

        db_mod.upsert_ranking(sid, card_id, 5, db_path=tmp_db)
        db_mod.upsert_ranking(sid, card_id, 7, db_path=tmp_db)  # update
        ranking = db_mod.get_ranking(sid, db_path=tmp_db)
        assert ranking[0]["score"] == 7
