"""Import parser (merge.parse_cards) — foco: um jogo EM BRANCO tem que ficar
NAQUELA posição, não escorregar pro fim. Regressão do bug em que células vazias
eram descartadas antes de indexar, subindo os jogos seguintes uma casa."""

from src.merge import parse_cards


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_blank_middle_game_stays_in_position_with_header(tmp_path):
    path = _write(
        tmp_path, "hdr.csv",
        "Participante,Pagina,Card,Jogo 1,Jogo 2,Jogo 3,Jogo 4,Jogo 5,Jogo 6,Jogo 7,Jogo 8,Revisao,Total\n"
        "Pg 1 #4,1,4,Casa,Fora,Casa,,Casa,Fora,Empate,Casa,SIM,0\n")
    card = parse_cards(path)[0]
    # jogo 4 (índice 3) em branco; jogo 8 continua Casa (não escorregou)
    assert card["choices"] == [0, 2, 0, None, 0, 2, 1, 0]


def test_blank_middle_game_stays_in_position_without_header(tmp_path):
    path = _write(tmp_path, "nohdr.csv",
                  "Pg 1 #4,1,4,Casa,Fora,Casa,,Casa,Fora,Empate,Casa\n")
    card = parse_cards(path)[0]
    assert card["choices"] == [0, 2, 0, None, 0, 2, 1, 0]


def test_full_row_all_eight_games(tmp_path):
    path = _write(tmp_path, "full.csv",
                  "Pg 1 #1,1,1,C,E,F,C,E,F,C,E\n")
    card = parse_cards(path)[0]
    assert card["choices"] == [0, 1, 2, 0, 1, 2, 0, 1]


def test_sparse_cartela_not_dropped(tmp_path):
    """Cartela com poucos jogos marcados (ou nenhum) NÃO pode sumir do ranking
    nem bagunçar a numeração das outras (regressão do corte '< 4 marcas')."""
    path = _write(
        tmp_path, "sparse.csv",
        "Participante,Pagina,Card,Jogo 1,Jogo 2,Jogo 3,Jogo 4,Jogo 5,Jogo 6,Jogo 7,Jogo 8,Revisao,Total\n"
        "Pg 1 #1,1,1,Casa,Casa,Casa,Casa,Casa,Casa,Casa,Casa,,0\n"
        "Pg 1 #2,1,2,Casa,Casa,Casa,,,,,,SIM,0\n"      # só 3 marcados
        "Pg 1 #3,1,3,Fora,Fora,Fora,Fora,Fora,Fora,Fora,Fora,,0\n")
    cards = parse_cards(path)
    assert [c["participant"] for c in cards] == ["Pg 1 #1", "Pg 1 #2", "Pg 1 #3"]
    assert cards[1]["choices"] == [0, 0, 0, None, None, None, None, None]


def test_header_row_still_skipped(tmp_path):
    """A linha de cabeçalho continua sendo pulada (não vira cartela)."""
    path = _write(
        tmp_path, "hdr2.csv",
        "Participante,Pagina,Card,Jogo 1,Jogo 2,Jogo 3,Jogo 4,Jogo 5,Jogo 6,Jogo 7,Jogo 8\n"
        "Pg 1 #1,1,1,Casa,Casa,Casa,Casa,Casa,Casa,Casa,Casa\n")
    cards = parse_cards(path)
    assert len(cards) == 1 and cards[0]["participant"] == "Pg 1 #1"


def _hdr(pages, cpp):
    rows = ["Participante,Pagina,Card,Jogo 1,Jogo 2,Jogo 3,Jogo 4,Jogo 5,Jogo 6,Jogo 7,Jogo 8"]
    for pg in pages:
        for c in range(1, cpp + 1):
            rows.append(f"Pag {pg} #{c},{pg},{c},Casa,Fora,Casa,Casa,Casa,Fora,Casa,Fora")
    return "\n".join(rows) + "\n"


def test_merge_renumbers_continuously(tmp_path):
    """Ao JUNTAR arquivos, o 2º continua a numeração logo após o 1º (41, 42...),
    não SOMA os números de página (bug: arquivo até 40 + outro que começa na 54
    dava 94). O 1º arquivo mantém a própria numeração."""
    from src import database as db
    from src import merge

    dbp = tmp_path / "m.db"
    db.init_db(dbp)
    sid = db.create_session("merge", None, 8, dbp)

    f1 = _write(tmp_path, "f1.csv", _hdr(range(31, 41), 3))   # páginas 31-40
    f2 = _write(tmp_path, "f2.csv", _hdr(range(54, 59), 3))   # começa na 54
    merge.import_file(sid, f1, db_path=dbp)
    merge.import_file(sid, f2, db_path=dbp)

    pages = sorted({c["page"] for c in db.get_cards(sid, dbp)})
    assert pages == list(range(31, 46))          # 31-40 (arq1) + 41-45 (arq2)
    assert min(p for p in pages if p > 40) == 41  # depois do 40 vem 41, não 94
