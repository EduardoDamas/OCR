"""
Command-line interface for batch processing without the desktop UI.

Usage examples:
  python cli.py process arquivo.pdf --session "Bolão Junho"
  python cli.py results --session-id 1 --game 0 --result 0
  python cli.py export --session-id 1 --out resultado.xlsx
  python cli.py serve --session-id 1
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src import database as db_mod
from src.omr.pipeline import process_file
from src.scoring.scorer import recalculate_ranking, ranking_to_display, set_result_and_rank
from src import exporter


def cmd_process(args):
    db_mod.init_db()
    session_id = db_mod.create_session(args.session, source_file=args.file)
    print(f"Sessão criada: {args.session} (id={session_id})")

    def progress(cur, total, msg):
        print(f"  [{cur}/{total}] {msg}")

    results = process_file(args.file, progress_cb=progress)
    db_mod.save_card_results(session_id, results)
    n_review = sum(1 for r in results if r.has_review_flags)
    print(f"\n✓ {len(results)} cartelas processadas.")
    if n_review:
        print(f"  ⚠ {n_review} cartelas com marcações duvidosas (coluna Revisão no Excel).")
    print(f"  Session ID: {session_id}")


def cmd_results(args):
    db_mod.init_db()
    ranking = set_result_and_rank(args.session_id, args.game, args.result)
    display = ranking_to_display(ranking)
    print(f"Resultado salvo — Jogo {args.game+1}: {['Casa','Empate','Fora'][args.result]}")
    print("\nRanking atualizado:")
    for row in display[:10]:
        print(f"  {row['position']:>2}. {row['label']:<30} {row['score']} acerto(s)")


def cmd_export(args):
    db_mod.init_db()
    fmt = "csv" if args.out.endswith(".csv") else "xlsx"
    if fmt == "xlsx":
        exporter.export_excel(args.session_id, args.out)
    else:
        exporter.export_csv(args.session_id, args.out)
    print(f"Exportado: {args.out}")


def cmd_ranking(args):
    db_mod.init_db()
    ranking = db_mod.get_ranking(args.session_id)
    display = ranking_to_display(ranking)
    print(f"{'#':>3}  {'Participante':<30}  {'Acertos':>7}")
    print("-" * 50)
    for row in display:
        print(f"{row['position']:>3}. {row['label']:<30}  {row['score']:>7}")


def cmd_serve(args):
    db_mod.init_db()
    from src.web.app import start_server
    import time, socket

    url = start_server(session_id=args.session_id, port=args.port)
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "localhost"
    print(f"Servidor rodando em:")
    print(f"  http://localhost:{args.port}         (este computador)")
    print(f"  http://{ip}:{args.port}    (outros dispositivos na rede)")
    print(f"  http://{ip}:{args.port}/admin   (inserir resultados)")
    print("\nPressione Ctrl+C para encerrar.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Servidor encerrado.")


def main():
    parser = argparse.ArgumentParser(prog="bolao-ocr",
                                     description="Bolão OCR — Processamento de cartelas")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("process", help="Processar PDF ou imagem")
    p.add_argument("file", help="Caminho do arquivo PDF ou imagem")
    p.add_argument("--session", default="Bolão", help="Nome da sessão")

    p = sub.add_parser("results", help="Registrar resultado oficial de um jogo")
    p.add_argument("--session-id", type=int, required=True)
    p.add_argument("--game", type=int, required=True, help="Número do jogo (0-7)")
    p.add_argument("--result", type=int, required=True,
                   help="0=Casa, 1=Empate, 2=Fora")

    p = sub.add_parser("export", help="Exportar resultados para Excel ou CSV")
    p.add_argument("--session-id", type=int, required=True)
    p.add_argument("--out", required=True, help="Arquivo de saída (.xlsx ou .csv)")

    p = sub.add_parser("ranking", help="Exibir ranking no terminal")
    p.add_argument("--session-id", type=int, required=True)

    p = sub.add_parser("serve", help="Iniciar servidor web de ranking")
    p.add_argument("--session-id", type=int, required=True)
    p.add_argument("--port", type=int, default=5000)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "process": cmd_process,
        "results": cmd_results,
        "export": cmd_export,
        "ranking": cmd_ranking,
        "serve": cmd_serve,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
