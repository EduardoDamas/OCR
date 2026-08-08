"""Tests for the site publisher: Excel/CSV conferido → classificação HTML."""

import csv
from src.web import publisher


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerows(rows)


def test_build_ranking_sorts_and_handles_ties(tmp_path):
    p = tmp_path / "conf.csv"
    _write_csv(p, [
        ["Participante", "Página", "Card", "Revisão", "Total"],
        ["João", 1, 1, "", 5],
        ["Maria", 1, 2, "", 8],
        ["Pedro", 1, 3, "", 8],   # empate com Maria em 8
        ["Ana", 1, 4, "", 3],
    ])
    r = publisher.build_ranking(str(p))
    assert [x["participante"] for x in r] == ["Maria", "Pedro", "João", "Ana"]
    assert [x["acertos"] for x in r] == [8, 8, 5, 3]
    # empate: dois em 1º, próximo é 3º (não 2º)
    assert [x["pos"] for x in r] == [1, 1, 3, 4]


def test_build_ranking_no_header_uses_last_numeric_col(tmp_path):
    p = tmp_path / "simples.csv"
    _write_csv(p, [["Fulano", 7], ["Beltrano", 9], ["Cicrano", 2]])
    r = publisher.build_ranking(str(p))
    assert [x["participante"] for x in r] == ["Beltrano", "Fulano", "Cicrano"]
    assert r[0]["pos"] == 1 and r[0]["acertos"] == 9


def test_publish_writes_selfcontained_html(tmp_path):
    src = tmp_path / "conf.csv"
    _write_csv(src, [["Participante", "Total"], ["João <b>", 4], ["Maria", 6]])
    out = tmp_path / "site.html"
    path, n = publisher.publish(str(src), str(out), titulo="Meu Bolão")
    assert n == 2
    html = out.read_text(encoding="utf-8")
    # autossuficiente: sem link externo, CSS embutido, dados embutidos
    assert "<style>" in html and "http://" not in html and "https://" not in html
    assert "Meu Bolão" in html
    assert "Maria" in html and html.index("Maria") < html.index("João")  # ordenado
    # nome com HTML é escapado (não vira tag)
    assert "João &lt;b&gt;" in html
