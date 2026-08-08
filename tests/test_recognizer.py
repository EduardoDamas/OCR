"""Unit tests for the OMR mark recognizer (no OpenCV images needed)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from src.omr.recognizer import (
    _fill_density, _cell_score, _scores_to_choice,
    CONFIDENCE_LOW, choice_label,
)


def white_cell(h=40, w=40):
    return np.full((h, w), 255, dtype=np.uint8)


def dark_cell(h=40, w=40, fill_pct=0.8):
    img = np.full((h, w), 255, dtype=np.uint8)
    dark_rows = int(h * fill_pct)
    img[:dark_rows, :] = 0
    return img


class TestFillDensity:
    def test_empty_cell_is_near_zero(self):
        assert _fill_density(white_cell()) < 0.05

    def test_filled_cell_is_high(self):
        assert _fill_density(dark_cell(fill_pct=0.8)) > 0.60

    def test_empty_array_returns_zero(self):
        assert _fill_density(np.array([], dtype=np.uint8).reshape(0, 0)) == 0.0


class TestScoresToChoice:
    def test_clear_winner_no_review(self):
        choice, conf, review = _scores_to_choice([0.8, 0.05, 0.03])
        assert choice == 0
        assert conf > CONFIDENCE_LOW
        assert not review

    def test_no_marks_triggers_review(self):
        choice, conf, review = _scores_to_choice([0.01, 0.01, 0.01])
        assert choice is None
        assert review is True

    def test_ambiguous_marks_triggers_review(self):
        choice, conf, review = _scores_to_choice([0.4, 0.38, 0.05])
        assert review is True

    def test_second_option_chosen(self):
        choice, conf, review = _scores_to_choice([0.02, 0.9, 0.03])
        assert choice == 1

    def test_empty_scores(self):
        choice, conf, review = _scores_to_choice([])
        assert choice is None
        assert review is True


class TestChoiceLabel:
    def test_labels(self):
        assert choice_label(0) == "Casa"
        assert choice_label(1) == "Empate"
        assert choice_label(2) == "Fora"
        assert choice_label(None) == "?"
        assert choice_label(99) == "?"
