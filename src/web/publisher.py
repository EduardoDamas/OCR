"""
Publicador da CLASSIFICAÇÃO GERAL — pega o Excel/CSV JÁ CONFERIDO e gera uma
PÁGINA pronta (HTML único, autossuficiente) pra subir no site do cliente.

Fluxo do cliente (o que ele pediu):
    confere o Excel  →  gera a página aqui  →  sobe no site  →  todo mundo vê.

A página é um arquivo .html SOZINHO (CSS embutido, dados embutidos, sem servidor,
sem login): abre em qualquer celular, funciona offline e pode ser hospedado em
qualquer lugar (ou aberto direto). Ordena pela coluna de pontos (Total/Acertos).
"""

import html as _html


def _rows(path):
    from ..omr.trainer import _load_rows
    return [list(r) for r in _load_rows(path)]


def build_ranking(path):
    """Lê o Excel/CSV conferido e devolve a classificação ordenada:
    [{'pos', 'participante', 'acertos'}], já com empates tratados.

    Aceita tanto o export do app (cabeçalho 'Participante ... Total') quanto um
    CSV simples 'nome;pontos'. Ordena por pontos (maior → menor)."""
    rows = _rows(path)
    if not rows:
        return []

    # 1) acha a linha de cabeçalho (tem 'participante' e 'total'/'acertos')
    p_col = t_col = header_i = None
    for i, r in enumerate(rows[:5]):
        low = [str(c).strip().lower() for c in r]
        if "participante" in low and ("total" in low or "acertos" in low):
            p_col = low.index("participante")
            t_col = low.index("total") if "total" in low else low.index("acertos")
            header_i = i
            break

    data = []
    if header_i is not None:
        for r in rows[header_i + 1:]:
            if len(r) <= max(p_col, t_col):
                continue
            name = str(r[p_col]).strip()
            if not name or name.lower() == "participante":
                continue
            data.append((name, _to_int(r[t_col])))
    else:
        # sem cabeçalho: assume 'nome ; pontos' (última coluna numérica = pontos)
        for r in rows:
            cells = [str(c).strip() for c in r if str(c).strip() != ""]
            if len(cells) < 2:
                continue
            name = cells[0]
            if name.lower() == "participante":
                continue
            data.append((name, _to_int(cells[-1])))

    data.sort(key=lambda x: -x[1])
    out, pos, prev = [], 0, None
    for i, (name, pts) in enumerate(data):
        if pts != prev:
            pos = i + 1
            prev = pts
        out.append({"pos": pos, "participante": name, "acertos": pts})
    return out


def _to_int(v):
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except Exception:
        return 0


def publish(path, out_path, titulo="Classificação Geral do Bolão",
            subtitulo="", data_txt=""):
    """Gera a página .html da classificação a partir do Excel/CSV conferido.
    Retorna (out_path, nº_participantes)."""
    ranking = build_ranking(path)
    html_str = render_html(ranking, titulo, subtitulo, data_txt)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return out_path, len(ranking)


_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


def render_html(ranking, titulo, subtitulo="", data_txt=""):
    linhas = []
    for r in ranking:
        pos = r["pos"]
        medal = _MEDAL.get(pos, "")
        cls = f"top top{pos}" if pos <= 3 else ""
        nome = _html.escape(str(r["participante"]))
        linhas.append(
            f'<tr class="{cls}" data-nome="{nome.lower()}">'
            f'<td class="pos">{medal or pos}</td>'
            f'<td class="nome">{nome}</td>'
            f'<td class="pts">{r["acertos"]}</td></tr>'
        )
    corpo = "\n".join(linhas) or (
        '<tr><td colspan="3" class="vazio">Nenhuma classificação encontrada '
        'no arquivo.</td></tr>')
    sub = f'<p class="sub">{_html.escape(subtitulo)}</p>' if subtitulo else ""
    dt = f'<span class="data">{_html.escape(data_txt)}</span>' if data_txt else ""
    total = len(ranking)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{_html.escape(titulo)}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --bg0:#060b16;--bg1:#0d1a30;--text:#eaf1fd;--muted:#8ba1c4;
    --line:rgba(255,255,255,.08);--glass:rgba(255,255,255,.04);
    --green:#16d488;--gold:#ffcf3f;--silver:#cfd9e8;--bronze:#e0955b;--accent:#3b82f6;
  }}
  html{{-webkit-text-size-adjust:100%}}
  body{{font-family:-apple-system,"Segoe UI",Roboto,system-ui,sans-serif;
    background:radial-gradient(1200px 600px at 50% -10%,#12294c 0%,var(--bg0) 60%);
    color:var(--text);min-height:100vh;padding:20px 12px 60px}}
  .wrap{{max-width:640px;margin:0 auto}}
  header{{text-align:center;padding:18px 0 22px}}
  h1{{font-size:clamp(22px,6vw,32px);font-weight:800;letter-spacing:-.02em}}
  h1 .cup{{margin-right:8px}}
  .sub{{color:var(--muted);margin-top:6px;font-size:15px}}
  .meta{{display:flex;justify-content:center;gap:14px;margin-top:12px;
    color:var(--muted);font-size:13px;flex-wrap:wrap}}
  .meta b{{color:var(--text)}}
  .search{{width:100%;margin:6px 0 16px;padding:13px 16px;border-radius:12px;
    border:1px solid var(--line);background:var(--glass);color:var(--text);
    font-size:16px;outline:none}}
  .search::placeholder{{color:var(--muted)}}
  .card{{background:var(--glass);border:1px solid var(--line);border-radius:16px;
    overflow:hidden;box-shadow:0 18px 50px -12px rgba(0,0,0,.6)}}
  table{{width:100%;border-collapse:collapse}}
  thead th{{background:rgba(255,255,255,.05);color:var(--muted);font-size:12px;
    text-transform:uppercase;letter-spacing:.08em;padding:12px 14px;text-align:left}}
  thead th.c{{text-align:center}} thead th.r{{text-align:right}}
  tbody td{{padding:14px;border-top:1px solid var(--line);font-size:16px}}
  td.pos{{width:56px;text-align:center;font-weight:800;font-size:18px;color:var(--muted)}}
  td.nome{{font-weight:600}}
  td.pts{{width:80px;text-align:right;font-weight:800;color:var(--green);font-size:18px}}
  tr.top td{{background:rgba(255,255,255,.03)}}
  tr.top1 td.pos{{color:var(--gold)}} tr.top1 td.nome{{color:var(--gold)}}
  tr.top2 td.pos{{color:var(--silver)}}
  tr.top3 td.pos{{color:var(--bronze)}}
  td.vazio{{text-align:center;color:var(--muted);padding:40px 14px}}
  .foot{{text-align:center;color:var(--muted);font-size:12px;margin-top:22px;line-height:1.7}}
  tr.hide{{display:none}}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1><span class="cup">🏆</span>{_html.escape(titulo)}</h1>
      {sub}
      <div class="meta"><span><b>{total}</b> participantes</span>{dt}</div>
    </header>

    <input class="search" id="q" type="search" inputmode="search"
           placeholder="🔎 Buscar meu nome..." autocomplete="off"/>

    <div class="card">
      <table>
        <thead><tr>
          <th class="c">#</th><th>Participante</th><th class="r">Acertos</th>
        </tr></thead>
        <tbody id="tb">
{corpo}
        </tbody>
      </table>
    </div>

    <p class="foot">Classificação oficial do bolão · atualizada a cada rodada</p>
  </div>

<script>
  var q=document.getElementById('q'),rows=[].slice.call(document.querySelectorAll('#tb tr'));
  q.addEventListener('input',function(){{
    var t=q.value.trim().toLowerCase();
    rows.forEach(function(r){{
      var n=r.getAttribute('data-nome')||'';
      r.classList.toggle('hide', t && n.indexOf(t)<0);
    }});
  }});
</script>
</body>
</html>"""
