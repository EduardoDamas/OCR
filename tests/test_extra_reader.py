"""Accuracy regression guard for the EXTRA-format reader (ECC registration).

Two layers:
  * fast structural tests (always run) — pin the safety invariants that don't
    need image rendering (constant ordering, marginal-cc flag wiring).
  * a slow end-to-end test (opt-in via --runslow) — render the two labelled
    PDF batches at 300 dpi through the production pipeline and assert accuracy
    and silent-wrong stay at/above the levels measured on 2026-06-27:
        folhas 11-20 (template source)   97.2%  | silent 0.99%
        folhas 21-30 (fully unseen)      93.1%  | silent 0.68%
    Thresholds are set a little below those so normal noise won't flip the
    test, but any real degradation (a bad warp change, a broken flag) trips it.

Fixtures live in tests/fixtures/extra/ (real client sheets + gabaritos); the
test skips cleanly if they are absent.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.omr import extra_reader as ER

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "extra")
BATCHES = [
    # label, pdf, gabarito csv, first folha, min accuracy, max silent-wrong frac
    ("11-20", "pages_11_to_20.pdf", "gabarito_11_20.csv", 11, 0.96, 0.015),
    ("21-30", "pages_21_to_30.pdf", "gabarito_21_30.csv", 21, 0.92, 0.012),
]
_MARK = {"C": 0, "E": 1, "F": 2, "": None}


# ── fast structural invariants (no rendering) ───────────────────────────────

class TestExtraReaderInvariants:
    def test_marginal_threshold_above_lock(self):
        # a marginal lock must be a STRICTER bar than the bare lock/format floor,
        # otherwise the page-level review flag can never trip.
        assert ER.CC_MARGINAL > ER.LOCK_CC

    def test_template_is_bundled(self):
        # the production asset must ship; without it the EXTRA path is dead.
        assert ER.available()
        tpl = ER._load_template()
        assert len(tpl["exemplars"]) >= 1
        assert tpl["ref_win"].shape[0] == ER.N_CARDS

    def test_ink_threshold_sane(self):
        assert 0.0 < ER.INK_THR < 1.0


# ── helpers for the slow end-to-end test ────────────────────────────────────

def _load_gt(path):
    gt = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"pag:\s*(\d+)\s*C\s*(\d+);(.*)", line.strip())
            if not m:
                continue
            vals = [_MARK.get(v.strip().upper(), None) for v in m.group(3).split(";")[:8]]
            while len(vals) < 8:
                vals.append(None)
            gt[(int(m.group(1)), int(m.group(2)))] = vals
    return gt


def _measure(pdf_path, gt):
    """Run the production EXTRA reader over a labelled PDF; return metrics +
    the marginal-flag invariant check."""
    from src.omr.preprocessor import (
        load_source, correct_perspective, deskew, enhance, normalize_width,
    )
    pages = load_source(pdf_path, dpi=300)
    ok = tot = silent = 0
    marginal_pages_all_flagged = True
    for pi, page in enumerate(pages):
        bgr = deskew(correct_perspective(normalize_width(page)))
        gray = enhance(bgr)
        cards, cc = ER.recognize_page_extra(gray, bgr, pi + 1)
        # invariant: a page that locks only marginally must flag EVERY card
        if cc < ER.CC_MARGINAL and not all(c.has_review_flags for c in cards):
            marginal_pages_all_flagged = False
        for cd in cards:
            row = gt.get((pi + 1, cd.card_index + 1), [None] * 8)
            for mk, x in zip(cd.marks, row):
                if x is None:
                    continue
                tot += 1
                if mk.choice == x:
                    ok += 1
                elif not mk.needs_review:
                    silent += 1
    return ok, tot, silent, marginal_pages_all_flagged


@pytest.mark.slow
@pytest.mark.parametrize("label,pdf,csv,base,min_acc,max_silent", BATCHES)
def test_extra_accuracy_regression(label, pdf, csv, base, min_acc, max_silent):
    pdf_path = os.path.join(FIX, pdf)
    csv_path = os.path.join(FIX, csv)
    if not (os.path.exists(pdf_path) and os.path.exists(csv_path)):
        pytest.skip(f"fixtures for {label} not present")
    if not ER.available():
        pytest.skip("extra_template.npz not bundled")

    gt = _load_gt(csv_path)
    ok, tot, silent, marginal_ok = _measure(pdf_path, gt)
    acc = ok / max(1, tot)
    sil = silent / max(1, tot)
    print(f"\n[{label}] acc={acc*100:.1f}% ({ok}/{tot})  silent={sil*100:.2f}% ({silent})")

    assert tot > 1800, f"{label}: expected ~1900 graded marks, got {tot}"
    assert acc >= min_acc, f"{label}: accuracy {acc*100:.1f}% < floor {min_acc*100:.0f}%"
    assert sil <= max_silent, f"{label}: silent-wrong {sil*100:.2f}% > ceiling {max_silent*100:.1f}%"
    assert marginal_ok, f"{label}: a marginal-cc page did not flag all its cards for review"
