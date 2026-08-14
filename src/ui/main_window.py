"""
Desktop UI built with Tkinter.
Workflow: New Session → Load File → Process → Review flags → Export → Share link
"""

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from typing import Optional, List
import webbrowser
import socket


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


# Versão VISÍVEL (título + log). O maior problema recorrente em campo foi
# confusão de versão ("qual exe você rodou?") — mostrar o número acaba com isso.
# v60: cartela digital exporta o NOME do apostador em coluna separada ("Nome"),
#      sem mexer no rótulo "Pág X #Y" — o site mostra o nome na Classificação Geral.
APP_VERSION = "v60"

OPTION_LABELS = ["Casa", "Empate", "Fora"]
COLORS = {
    "bg":      "#0f1e38",
    "card":    "#162c50",
    "accent":  "#1a73e8",
    "green":   "#00b050",
    "gold":    "#FFD700",
    "text":    "#e8f0fe",
    "muted":   "#7090b0",
    "danger":  "#e53935",
    "warning": "#f5a623",
}


class BolaoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Bolão OCR System — {APP_VERSION}")
        self.configure(bg=COLORS["bg"])
        self.geometry("960x700")
        self.minsize(800, 600)

        # State
        self.session_id: Optional[int] = None
        self.web_url: Optional[str] = None
        self._process_thread: Optional[threading.Thread] = None
        self._rank_thread: Optional[threading.Thread] = None

        # Lazy imports (avoid slow startup)
        self._db = None
        self._scorer = None
        self._pipeline = None
        self._exporter = None
        self._web = None

        self._build_ui()
        self._init_backend()

    # ── Backend init ──────────────────────────────────────────────────────────

    def _init_backend(self):
        try:
            from .. import database as db_mod
            from ..scoring import scorer
            from ..omr import pipeline
            from .. import exporter
            from ..web import app as web_app

            db_mod.init_db()
            self._db = db_mod
            self._scorer = scorer
            self._pipeline = pipeline
            self._exporter = exporter
            self._web = web_app

            self._log("Sistema iniciado. Crie ou selecione uma sessão.")
        except Exception as e:
            self._log(f"ERRO ao iniciar backend: {e}", error=True)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                        fieldbackground=COLORS["card"], font=("Segoe UI", 10))
        style.configure("TButton", padding=6, relief="flat",
                        background=COLORS["accent"], foreground="white")
        style.map("TButton",
                  background=[("active", "#1558b0"), ("disabled", "#2a4070")])
        style.configure("Green.TButton", background=COLORS["green"])
        style.map("Green.TButton", background=[("active", "#008040")])
        style.configure("TLabelframe", background=COLORS["card"],
                        foreground=COLORS["muted"], relief="flat")
        style.configure("TLabelframe.Label", background=COLORS["bg"],
                        foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("TProgressbar", troughcolor=COLORS["card"],
                        background=COLORS["accent"])

        # Treeview (ranking table) — needs explicit dark styling, otherwise its
        # rows default to a white background with near-white text (invisible).
        style.configure("Treeview",
                        background=COLORS["card"],
                        fieldbackground=COLORS["card"],
                        foreground=COLORS["text"],
                        rowheight=24,
                        borderwidth=0)
        style.configure("Treeview.Heading",
                        background=COLORS["bg"],
                        foreground=COLORS["text"],
                        font=("Segoe UI", 10, "bold"),
                        relief="flat")
        style.map("Treeview.Heading",
                  background=[("active", COLORS["accent"])])
        style.map("Treeview",
                  background=[("selected", COLORS["accent"])],
                  foreground=[("selected", "white")])

        # Top toolbar
        toolbar = tk.Frame(self, bg=COLORS["bg"], pady=8)
        toolbar.pack(fill=tk.X, padx=12)

        tk.Label(toolbar, text="⚽ Bolão OCR",
                 font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side=tk.LEFT)

        self._session_label = tk.Label(toolbar, text="Nenhuma sessão",
                                       font=("Segoe UI", 9),
                                       bg=COLORS["bg"], fg=COLORS["muted"])
        self._session_label.pack(side=tk.LEFT, padx=20)

        ttk.Button(toolbar, text="Nova Sessão",
                   command=self._new_session).pack(side=tk.LEFT, padx=4)

        self._web_btn = ttk.Button(toolbar, text="🌐 Abrir Ranking Web",
                                   command=self._open_web,
                                   style="Green.TButton",
                                   state=tk.DISABLED)
        self._web_btn.pack(side=tk.RIGHT, padx=4)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=12)

        # Main pane: left panel + right panel
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                              bg=COLORS["bg"], sashwidth=4)
        pane.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # ── Left: File + Actions (scrollable — several sections now, so on a
        #     short window the lower ones must not get cut off) ────────────────
        left_container = tk.Frame(pane, bg=COLORS["bg"])
        pane.add(left_container, minsize=300)
        left_canvas = tk.Canvas(left_container, bg=COLORS["bg"],
                                highlightthickness=0, width=300)
        left_scroll = ttk.Scrollbar(left_container, orient="vertical",
                                    command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left = tk.Frame(left_canvas, bg=COLORS["bg"])
        _left_win = left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>",
                  lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>",
                         lambda e: left_canvas.itemconfig(_left_win, width=e.width))

        def _left_wheel(e):
            left_canvas.yview_scroll(int(-e.delta / 120), "units")
        left_container.bind("<Enter>", lambda e: left_canvas.bind_all("<MouseWheel>", _left_wheel))
        left_container.bind("<Leave>", lambda e: left_canvas.unbind_all("<MouseWheel>"))

        file_frame = ttk.LabelFrame(left, text="ARQUIVO", padding=10)
        file_frame.pack(fill=tk.X, pady=(0, 8))

        self._file_var = tk.StringVar(value="Nenhum arquivo selecionado")
        tk.Label(file_frame, textvariable=self._file_var, wraplength=260,
                 bg=COLORS["card"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(fill=tk.X, pady=(0, 6))

        file_btn_row = tk.Frame(file_frame, bg=COLORS["card"])
        file_btn_row.pack(fill=tk.X)
        ttk.Button(file_btn_row, text="📂 Selecionar PDF / Imagem",
                   command=self._pick_file).pack(side=tk.LEFT)

        self._process_btn = ttk.Button(file_frame, text="▶ Processar",
                                       command=self._process_file,
                                       state=tk.DISABLED)
        self._process_btn.pack(fill=tk.X, pady=(8, 0))

        self._progress = ttk.Progressbar(file_frame, mode="determinate", maximum=100)
        self._progress.pack(fill=tk.X, pady=(4, 0))
        self._progress_label = tk.Label(file_frame, text="",
                                        bg=COLORS["card"], fg=COLORS["muted"],
                                        font=("Segoe UI", 8))
        self._progress_label.pack()

        # Export
        export_frame = ttk.LabelFrame(left, text="EXPORTAR", padding=10)
        export_frame.pack(fill=tk.X, pady=(0, 8))

        self._export_xlsx_btn = ttk.Button(export_frame, text="📊 Exportar Excel (.xlsx)",
                                           command=self._export_xlsx,
                                           state=tk.DISABLED)
        self._export_xlsx_btn.pack(fill=tk.X, pady=2)
        self._export_csv_btn = ttk.Button(export_frame, text="📄 Exportar CSV",
                                          command=self._export_csv,
                                          state=tk.DISABLED)
        self._export_csv_btn.pack(fill=tk.X, pady=2)
        ttk.Separator(export_frame, orient="horizontal").pack(fill=tk.X, pady=6)
        tk.Label(export_frame,
                 text="Juntar 2 PCs: importe os CSV/Excel exportados de cada um",
                 bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 8),
                 wraplength=250, justify="left").pack(fill=tk.X, pady=(0, 4))
        ttk.Button(export_frame, text="🔗 Importar / Juntar cartelas",
                   command=self._import_cards).pack(fill=tk.X, pady=2)
        # Renumerar páginas pra bater com o número REAL da folha impressa
        ttk.Button(export_frame, text="🔢 Renumerar páginas (ex.: folha 7 a 20)",
                   command=self._renumber_pages).pack(fill=tk.X, pady=2)
        # Trocar/tirar a etiqueta de origem (o "nome" antes da cartela)
        ttk.Button(export_frame, text="🏷️ Renomear etiqueta (o nome antes da cartela)",
                   command=self._rename_source).pack(fill=tk.X, pady=2)

        # Publicar a classificação no site (Excel conferido → página pronta)
        pub_frame = ttk.LabelFrame(left, text="PUBLICAR NO SITE", padding=10)
        pub_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(pub_frame,
                 text="Gera a página da CLASSIFICAÇÃO GERAL a partir do Excel "
                      "conferido — é só subir no seu site (todo mundo vê, sem login).",
                 bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 8),
                 wraplength=250, justify="left").pack(fill=tk.X, pady=(0, 4))
        ttk.Button(pub_frame, text="🌐 Gerar página de classificação",
                   command=self._publish_site).pack(fill=tk.X, pady=2)
        ttk.Button(pub_frame, text="☁️ Publicar no link fixo (nuvem 24h)",
                   command=self._publish_cloud).pack(fill=tk.X, pady=2)

        # AI reading for pen/photo sheets
        ai_frame = ttk.LabelFrame(left, text="LEITURA POR IA (CANETA)", padding=10)
        ai_frame.pack(fill=tk.X, pady=(0, 8))
        self._ai_status = tk.Label(ai_frame, text=self._ai_status_text(),
                                   bg=COLORS["card"], fg=COLORS["muted"],
                                   font=("Segoe UI", 8), anchor="w",
                                   wraplength=250, justify="left")
        self._ai_status.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(ai_frame, text="🔑 Configurar chave da IA",
                   command=self._config_ai_key).pack(fill=tk.X, pady=2)
        ttk.Button(ai_frame, text="🔍 Testar IA agora",
                   command=self._test_ai).pack(fill=tk.X, pady=2)
        # Modo lote (Batch API): metade do preço, mas assíncrono (minutos)
        self._batch_var = tk.BooleanVar(value=self._batch_enabled())
        ttk.Checkbutton(ai_frame,
                        text="💤 Modo lote — metade do preço (demora alguns min)",
                        variable=self._batch_var,
                        command=self._toggle_batch).pack(fill=tk.X, pady=(4, 0))
        tk.Label(ai_frame,
                 text="Manda tudo de uma vez e busca depois: mesma precisão, "
                      "metade do custo. Ideal pro fim da rodada (não é na hora).",
                 bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 8),
                 wraplength=250, justify="left").pack(fill=tk.X)

        # Training (incremental, in-app)
        train_frame = ttk.LabelFrame(left, text="TREINAR MODELO", padding=10)
        train_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(train_frame,
                 text="Imagem/PDF de uma página + gabarito (CSV ou Excel) → Treinar",
                 bg=COLORS["card"], fg=COLORS["muted"], font=("Segoe UI", 8),
                 wraplength=250, justify="left").pack(fill=tk.X, pady=(0, 4))
        self._train_img = None
        self._train_csv = None
        self._train_img_btn = ttk.Button(train_frame, text="🖼️ Imagem/PDF da página",
                                          command=self._select_train_img)
        self._train_img_btn.pack(fill=tk.X, pady=2)
        self._train_img_lbl = tk.Label(train_frame, text="(nenhum arquivo)",
                                       bg=COLORS["card"], fg=COLORS["muted"],
                                       font=("Segoe UI", 8), anchor="w")
        self._train_img_lbl.pack(fill=tk.X)
        self._train_csv_btn = ttk.Button(train_frame, text="📄 Gabarito (CSV/Excel)",
                                         command=self._select_train_csv)
        self._train_csv_btn.pack(fill=tk.X, pady=2)
        self._train_csv_lbl = tk.Label(train_frame, text="(nenhum arquivo)",
                                       bg=COLORS["card"], fg=COLORS["muted"],
                                       font=("Segoe UI", 8), anchor="w")
        self._train_csv_lbl.pack(fill=tk.X)
        self._train_btn = ttk.Button(train_frame, text="🧠 Treinar",
                                     command=self._train_model,
                                     style="Green.TButton", state=tk.DISABLED)
        self._train_btn.pack(fill=tk.X, pady=(4, 2))
        self._train_status = tk.Label(train_frame, text=self._train_status_text(),
                                      bg=COLORS["card"], fg=COLORS["muted"],
                                      font=("Segoe UI", 8), anchor="w")
        self._train_status.pack(fill=tk.X)

        # Results entry
        results_frame = ttk.LabelFrame(left, text="RESULTADOS OFICIAIS", padding=10)
        results_frame.pack(fill=tk.X, pady=(0, 8))

        self._result_vars: List[tk.IntVar] = []
        self._result_widgets = []
        # Casa / Empate / Fora plus "Anular" (annul the game — counts for no one)
        result_labels = OPTION_LABELS + ["Anular"]
        for g in range(8):
            row = tk.Frame(results_frame, bg=COLORS["card"])
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"Jogo {g+1}:", width=7, anchor="w",
                     bg=COLORS["card"], fg=COLORS["text"],
                     font=("Segoe UI", 9)).pack(side=tk.LEFT)
            var = tk.IntVar(value=-1)
            self._result_vars.append(var)
            btns = []
            for opt, label in enumerate(result_labels):
                b = tk.Button(row, text=label, width=6,
                              bg="#1a3060", fg=COLORS["muted"],
                              activebackground=COLORS["accent"],
                              relief="flat", bd=0, padx=3, pady=2,
                              font=("Segoe UI", 8),
                              command=lambda g=g, opt=opt: self._set_result(g, opt))
                b.pack(side=tk.LEFT, padx=1)
                btns.append(b)
            self._result_widgets.append(btns)

        self._rank_btn = ttk.Button(results_frame, text="Atualizar Ranking",
                                    command=self._refresh_ranking,
                                    style="Green.TButton")
        self._rank_btn.pack(fill=tk.X, pady=(6, 0))

        self._rank_progress = ttk.Progressbar(results_frame, mode="determinate",
                                              maximum=100)
        self._rank_progress.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(results_frame, text="📊 Ver Parcial",
                   command=self._show_parcial).pack(fill=tk.X, pady=(4, 0))
        ttk.Button(results_frame, text="🎲 Simular (E se…?)",
                   command=self._show_simulacao).pack(fill=tk.X, pady=(4, 0))

        # ── Right: Ranking preview + Log ─────────────────────────────────────
        right = tk.Frame(pane, bg=COLORS["bg"])
        pane.add(right, minsize=400)

        rank_frame = ttk.LabelFrame(right, text="RANKING ATUAL", padding=10)
        rank_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("pos", "participante", "acertos")
        self._rank_tree = ttk.Treeview(rank_frame, columns=cols,
                                       show="headings", height=12)
        self._rank_tree.heading("pos", text="#")
        self._rank_tree.heading("participante", text="Participante")
        self._rank_tree.heading("acertos", text="Acertos")
        self._rank_tree.column("pos", width=40, anchor="center")
        self._rank_tree.column("participante", width=260)
        self._rank_tree.column("acertos", width=70, anchor="center")

        sb = ttk.Scrollbar(rank_frame, orient=tk.VERTICAL,
                            command=self._rank_tree.yview)
        self._rank_tree.configure(yscrollcommand=sb.set)
        self._rank_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Log
        log_frame = ttk.LabelFrame(right, text="LOG", padding=8)
        log_frame.pack(fill=tk.X)

        self._log_text = tk.Text(log_frame, height=5, state=tk.DISABLED,
                                 bg=COLORS["card"], fg=COLORS["muted"],
                                 font=("Consolas", 8), relief="flat",
                                 wrap=tk.WORD)
        self._log_text.pack(fill=tk.X)

        # Selected file path (hidden state)
        self._selected_file: Optional[str] = None

    # ── Session management ────────────────────────────────────────────────────

    def _new_session(self):
        if self._db is None:
            return
        name = simpledialog.askstring(
            "Nova Sessão",
            "Nome/dia da rodada:\n"
            "(aparece no TOPO do ranking pros clientes — ex.: Bolão de Quarta — 13/08)",
            initialvalue="Bolão de Quarta", parent=self)
        if not name:
            return
        self.session_id = self._db.create_session(name)
        self._session_label.config(text=f"Sessão: {name} (#{self.session_id})")
        self._log(f"Sessão criada: {name}")
        self._update_process_btn()
        self._start_web_server()

    def _update_process_btn(self):
        """Enable Processar only when both a session and a file are ready."""
        ready = bool(self.session_id) and bool(self._selected_file)
        self._process_btn.config(state=tk.NORMAL if ready else tk.DISABLED)

    def _start_web_server(self):
        if self._web is None or self.session_id is None:
            return
        try:
            url = self._web.start_server(session_id=self.session_id)
            ip = _get_local_ip()
            port = 5000
            self.web_url = f"http://{ip}:{port}"
            self._web_btn.config(state=tk.NORMAL)
            self._log(f"Ranking web disponível em: {self.web_url}")
        except Exception as e:
            self._log(f"Aviso: servidor web não iniciado — {e}", error=True)

    # ── File handling ─────────────────────────────────────────────────────────

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Selecionar arquivo",
            filetypes=[("PDF e Imagens", "*.pdf *.jpg *.jpeg *.png *.tif *.tiff"),
                       ("PDF", "*.pdf"),
                       ("Imagens", "*.jpg *.jpeg *.png *.tif *.tiff"),
                       ("Todos", "*.*")]
        )
        if path:
            self._selected_file = path
            name = Path(path).name
            self._file_var.set(name)
            self._update_process_btn()
            # The loaded page doubles as the training image unless a different
            # one is picked in the TREINAR section — so training "just works".
            if not self._train_img:
                self._train_img_lbl.config(text=f"(usando: {name})")
                self._update_train_btn()
            if not self.session_id:
                self._log("Crie uma sessão antes de processar.")

    def _process_file(self):
        if not self._selected_file or self.session_id is None:
            return
        if self._process_thread and self._process_thread.is_alive():
            messagebox.showinfo("Aguarde", "Processamento em andamento.")
            return

        # Aviso de RESOLUÇÃO BAIXA: entrada mais comum de erro em campo foi um
        # PRINT DE TELA pequeno (744px) no lugar do PDF — a IA não tem pixels
        # pra ler a caneta e o resultado sai ruim mesmo com a IA ligada.
        try:
            from ..omr import ai_reader
            aviso = ai_reader.input_resolution_warning(self._selected_file)
        except Exception:
            aviso = None
        if aviso:
            seguir = messagebox.askyesno(
                "⚠️ Imagem de baixa resolução",
                aviso + "\n\nProcessar mesmo assim (resultado pode ficar ruim)?")
            if not seguir:
                self._log("Processamento cancelado — use o PDF ou foto maior.")
                return

        # O custo da IA é confirmado NO MOMENTO CERTO: depois da leitura grátis,
        # com os números exatos (quantas cartelas, R$ e minutos).
        fmt = "auto"

        self._process_btn.config(state=tk.DISABLED)
        self._progress["value"] = 0
        # carimba versão + status da IA no log — o print do cliente passa a
        # provar qual versão rodou e se a IA estava ligada
        try:
            from ..omr import ai_reader
            ia_on = ai_reader.available()
        except Exception:
            ia_on = False
        self._log(f"Iniciando processamento… [{APP_VERSION}] "
                  f"IA: {'LIGADA ✅' if ia_on else 'DESLIGADA ❌ (só leitura local)'}")

        def ai_confirm(n, brl, mins):
            """Pergunta do custo da IA com números REAIS, feita da thread de
            processamento — bloqueia até o usuário decidir na janela."""
            ev = threading.Event()
            resp = {"ok": False}

            def ask():
                resp["ok"] = messagebox.askyesno(
                    "Conferir com a IA?",
                    f"A leitura grátis terminou.\n\n"
                    f"{n} cartela(s) ficaram duvidosas e podem ser conferidas "
                    f"pela IA agora:\n\n"
                    f"💰 Custo estimado: R$ {brl:,.2f}\n"
                    f"⏱️ Tempo estimado: ~{max(1, round(mins))} min\n\n"
                    f"SIM = conferir com a IA\n"
                    f"NÃO = manter o resultado grátis "
                    f"(duvidosas ficam na coluna Revisão)")
                ev.set()

            self.after(0, ask)
            ev.wait()
            return resp["ok"]

        def run():
            try:
                results = self._pipeline.process_file(
                    self._selected_file,
                    progress_cb=self._on_progress,
                    fmt=fmt,
                    ai_confirm_cb=ai_confirm
                )
                self._db.save_card_results(self.session_id, results)
                # Confrontos da rodada (nomes dos times) — pra grade da cartela no
                # ranking mostrar "GRÊMIO × BOLÍVAR". Só enfeite; se falhar, tudo
                # bem (a grade cai em "Jogo 1..8").
                try:
                    from ..omr import digital_reader
                    conf = digital_reader.extract_confrontos(self._selected_file)
                    if conf:
                        self._db.set_session_games(self.session_id, conf)
                except Exception:
                    pass
                # Populate the ranking right away so the cartelas show up in the
                # web page immediately (even before any official result is set),
                # instead of leaving it on "Aguardando processamento…".
                if self._scorer is not None:
                    try:
                        self._scorer.recalculate_ranking(self.session_id)
                    except Exception:
                        pass
                n_review = sum(1 for r in results if r.has_review_flags)
                self.after(0, self._on_process_done, len(results), n_review)
            except Exception as e:
                self.after(0, self._log, f"ERRO: {e}", True)
                self.after(0, lambda: self._process_btn.config(state=tk.NORMAL))

        self._process_thread = threading.Thread(target=run, daemon=True)
        self._process_thread.start()

    def _ai_cost_pages(self, path):
        """Nº de páginas que iriam pra IA paga (0 = nem pergunta). Num PDF
        misto conta SÓ as páginas de caneta — as digitais são grátis (ex.:
        448 págs com 397 digitais → pergunta pelas ~51 de caneta)."""
        try:
            from ..omr import ai_reader, digital_reader
            if not ai_reader.available():
                return 0
            if not str(path).lower().endswith(".pdf"):
                return 0
            import fitz
            doc = fitz.open(path)
            n = doc.page_count
            doc.close()
            n_fotos = n - len(digital_reader.digital_page_numbers(path))
            return n_fotos if n_fotos >= 2 else 0
        except Exception:
            return 0

    def _on_progress(self, current: int, total: int, msg: str):
        pct = int(current / max(total, 1) * 100)
        self.after(0, lambda: self._progress.config(value=pct))
        self.after(0, lambda: self._progress_label.config(text=msg))
        # Mensagens importantes da IA/roteamento ficam GRAVADAS no log — antes
        # só passavam na barra de progresso e o usuário não conseguia nos dizer
        # POR QUE a IA não rodou ("apareceu indisponível e sumiu").
        low = msg.lower()
        if (("ia" in low and "cartela" not in low) or "digitais" in low
                or "custo" in low or "parou" in low or "indisponível" in low):
            self.after(0, lambda: self._log(msg))

    def _on_process_done(self, n_cards: int, n_review: int):
        self._process_btn.config(state=tk.NORMAL)
        self._progress["value"] = 100
        self._export_xlsx_btn.config(state=tk.NORMAL)
        self._export_csv_btn.config(state=tk.NORMAL)
        msg = f"Processamento concluído: {n_cards} cartelas."
        if n_review:
            msg += f" {n_review} cartelas com marcações duvidosas (coluna Revisão)."
        self._log(msg)
        if self._web:
            self._web.notify_update()

    # ── Results entry ─────────────────────────────────────────────────────────

    def _set_result(self, game: int, opt: int):
        if self.session_id is None:
            messagebox.showwarning("Sessão", "Crie uma sessão primeiro.")
            return
        self._result_vars[game].set(opt)
        # Update button colours — Casa/Empate/Fora/Anular
        for i, btn in enumerate(self._result_widgets[game]):
            active = (i == opt)
            colors_map = [COLORS["green"], COLORS["warning"],
                          COLORS["danger"], COLORS["muted"]]
            btn.config(bg=colors_map[i] if active else "#1a3060",
                       fg="white" if active else COLORS["muted"],
                       font=("Segoe UI", 8, "bold" if active else "normal"))

    def _refresh_ranking(self):
        if self.session_id is None or self._scorer is None:
            return
        if self._rank_thread and self._rank_thread.is_alive():
            return

        # Collect entered results (fast — on UI thread). 0/1/2 = Casa/Empate/Fora,
        # 3 = Anulado (game voided — counts for nobody).
        for g, var in enumerate(self._result_vars):
            v = var.get()
            if v in (0, 1, 2, 3):
                self._db.set_official_result(self.session_id, g, v)

        # Scoring can touch many cards — run off the UI thread with a progress bar
        self._rank_btn.config(state=tk.DISABLED)
        self._rank_progress["value"] = 0
        self._log("Atualizando ranking…")

        def on_progress(cur, total):
            pct = int(cur / max(total, 1) * 100)
            self.after(0, lambda: self._rank_progress.config(value=pct))

        def run():
            try:
                ranking = self._scorer.recalculate_ranking(
                    self.session_id, progress_cb=on_progress)
                display = self._scorer.ranking_to_display(ranking)
                self.after(0, self._on_rank_done, display)
            except Exception as e:
                self.after(0, self._on_rank_error, str(e))

        self._rank_thread = threading.Thread(target=run, daemon=True)
        self._rank_thread.start()

    def _on_rank_done(self, display: list):
        self._update_rank_tree(display)
        self._rank_progress["value"] = 100
        self._rank_btn.config(state=tk.NORMAL)
        if self._web:
            self._web.notify_update()
        self._log(f"Ranking atualizado: {len(display)} cartelas.")

    def _on_rank_error(self, msg: str):
        self._rank_progress["value"] = 0
        self._rank_btn.config(state=tk.NORMAL)
        messagebox.showerror("Erro ao atualizar ranking", msg)
        self._log(f"ERRO no ranking: {msg}", error=True)

    def _show_parcial(self):
        """Popup with the live score distribution (how many cards have 8, 7, 6…)."""
        if self.session_id is None or self._scorer is None:
            messagebox.showwarning("Sessão", "Crie uma sessão primeiro.")
            return
        data = self._scorer.score_distribution(self.session_id)
        dist = data["distribution"]
        valid = data["valid_games"]
        total = data["total_cards"]
        if not dist:
            messagebox.showinfo("Parcial",
                                "Sem dados ainda. Processe as cartelas e clique "
                                "em 'Atualizar Ranking' primeiro.")
            return

        win = tk.Toplevel(self)
        win.title("Parcial do Bolão")
        win.configure(bg=COLORS["bg"])
        win.geometry("340x440")
        win.transient(self)

        tk.Label(win, text="📊 Parcial do Momento", font=("Segoe UI", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(pady=(14, 2))
        tk.Label(win, text=f"{total} cartelas · {valid} jogo(s) apurado(s)",
                 font=("Segoe UI", 9), bg=COLORS["bg"],
                 fg=COLORS["muted"]).pack(pady=(0, 10))

        body = tk.Frame(win, bg=COLORS["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=16)
        for item in dist:
            s, c = item["score"], item["count"]
            top = (valid > 0 and s == valid)   # got every valid game right
            fg = COLORS["gold"] if top else COLORS["text"]
            weight = "bold" if top else "normal"
            rowf = tk.Frame(body, bg=COLORS["card"])
            rowf.pack(fill=tk.X, pady=2)
            tk.Label(rowf, text=f"{s} ponto(s)", width=12, anchor="w",
                     bg=COLORS["card"], fg=fg,
                     font=("Segoe UI", 11, weight)).pack(side=tk.LEFT, padx=8, pady=6)
            tk.Label(rowf, text=f"{c} jogador(es)", anchor="e",
                     bg=COLORS["card"], fg=fg,
                     font=("Segoe UI", 11, weight)).pack(side=tk.RIGHT, padx=8)

    def _show_simulacao(self):
        """Popup: for the games still open, how many leaders win under each result."""
        if self.session_id is None or self._scorer is None:
            messagebox.showwarning("Sessão", "Crie uma sessão primeiro.")
            return
        data = self._scorer.simulate(self.session_id)
        if data["n_leaders"] == 0:
            messagebox.showinfo("Simular",
                                "Sem dados ainda. Processe as cartelas e clique "
                                "em 'Atualizar Ranking' primeiro.")
            return
        pending = data["pending"]
        if not pending:
            messagebox.showinfo("Simular",
                                "Todos os 8 jogos já foram apurados — "
                                "o ranking já é o final. 🏆")
            return

        CH = {0: "Casa", 1: "Empate", 2: "Fora"}
        win = tk.Toplevel(self)
        win.title("Simulação — E se…?")
        win.configure(bg=COLORS["bg"])
        win.geometry("430x560")
        win.transient(self)

        tk.Label(win, text="🎲 Simulação do Resultado", font=("Segoe UI", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(pady=(14, 2))
        one = len(pending) == 1
        tk.Label(win,
                 text=f"{data['n_leaders']} cartela(s) na frente com "
                      f"{data['max_score']} acerto(s) · falta(m) {len(pending)} jogo(s)",
                 font=("Segoe UI", 9), bg=COLORS["bg"],
                 fg=COLORS["muted"]).pack(pady=(0, 10))

        body = tk.Frame(win, bg=COLORS["bg"])
        body.pack(fill=tk.X, padx=16)
        for g in pending:
            counts = data["per_game"][g]
            best = max((0, 1, 2), key=lambda o: counts[o])
            card = tk.Frame(body, bg=COLORS["card"])
            card.pack(fill=tk.X, pady=3)
            head = f"Jogo {g + 1}" + ("  (define os ganhadores)" if one else "")
            tk.Label(card, text=head, anchor="w", bg=COLORS["card"],
                     fg=COLORS["text"], font=("Segoe UI", 10, "bold")
                     ).pack(fill=tk.X, padx=8, pady=(6, 0))
            line = tk.Frame(card, bg=COLORS["card"])
            line.pack(fill=tk.X, padx=8, pady=(0, 6))
            for o in (0, 1, 2):
                top = (o == best and counts[o] > 0)
                tk.Label(line, text=f"{CH[o]}: {counts[o]}",
                         bg=COLORS["card"],
                         fg=COLORS["gold"] if top else COLORS["text"],
                         font=("Segoe UI", 10, "bold" if top else "normal")
                         ).pack(side=tk.LEFT, padx=(0, 14))
            if counts[None]:
                tk.Label(line, text=f"(em branco: {counts[None]})",
                         bg=COLORS["card"], fg=COLORS["muted"],
                         font=("Segoe UI", 8)).pack(side=tk.LEFT)

        tk.Label(win, text="Cartelas na frente:", anchor="w",
                 bg=COLORS["bg"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(fill=tk.X, padx=16, pady=(10, 2))
        txt = tk.Text(win, height=10, bg=COLORS["card"], fg=COLORS["text"],
                      font=("Consolas", 9), relief="flat", wrap="none")
        txt.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))
        for r in data["leaders"]:
            picks = "  ".join(f"J{g+1}:{CH.get(r['marks'].get(g), '—')}" for g in pending)
            txt.insert(tk.END, f"{r['label'][:22]:22}  {picks}\n")
        txt.config(state=tk.DISABLED)

    def _update_rank_tree(self, display: list):
        self._rank_tree.delete(*self._rank_tree.get_children())
        self._rank_tree.tag_configure("odd", background=COLORS["card"])
        self._rank_tree.tag_configure("even", background="#1b3358")
        for i, row in enumerate(display):
            self._rank_tree.insert("", tk.END,
                                   values=(row["position"], row["label"], row["score"]),
                                   tags=("even" if i % 2 else "odd",))

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_xlsx(self):
        self._export("xlsx")

    def _export_csv(self):
        self._export("csv")

    def _export(self, fmt: str):
        if self.session_id is None or self._exporter is None:
            return
        ext = f".{fmt}"
        ftypes = [("Excel", "*.xlsx")] if fmt == "xlsx" else [("CSV", "*.csv")]
        path = filedialog.asksaveasfilename(
            defaultextension=ext, filetypes=ftypes,
            initialfile=f"resultado_bolao{ext}"
        )
        if not path:
            return

        # Export can touch thousands of rows — run off the UI thread so the
        # window stays responsive instead of appearing frozen.
        self._export_xlsx_btn.config(state=tk.DISABLED)
        self._export_csv_btn.config(state=tk.DISABLED)
        self._log(f"Exportando {Path(path).name}…")

        def run():
            try:
                if fmt == "xlsx":
                    self._exporter.export_excel(self.session_id, path)
                else:
                    self._exporter.export_csv(self.session_id, path)
                self.after(0, self._on_export_done, path)
            except Exception as e:
                self.after(0, self._on_export_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_export_done(self, path: str):
        self._export_xlsx_btn.config(state=tk.NORMAL)
        self._export_csv_btn.config(state=tk.NORMAL)
        self._log(f"Exportado: {Path(path).name}")
        if messagebox.askyesno("Abrir arquivo?",
                               f"Deseja abrir {Path(path).name}?"):
            import os
            os.startfile(path)

    def _on_export_error(self, msg: str):
        self._export_xlsx_btn.config(state=tk.NORMAL)
        self._export_csv_btn.config(state=tk.NORMAL)
        messagebox.showerror("Erro ao exportar", msg)
        self._log(f"ERRO exportando: {msg}", error=True)

    # ── Import / merge (combine cartelas from other PCs) ───────────────────────

    def _import_cards(self):
        if self.session_id is None:
            messagebox.showwarning("Sessão", "Crie uma sessão primeiro.")
            return
        paths = filedialog.askopenfilenames(
            title="Importar cartelas (CSV/Excel exportados)",
            filetypes=[("CSV/Excel", "*.csv *.xlsx *.xls"), ("Todos", "*.*")])
        if not paths:
            return

        # Etiqueta de cada arquivo: aparece ANTES do nome na classificação
        # (ex.: "PontoCentro · Pág 7 #1"). O cliente escolhe — vazio = sem
        # etiqueta (fica só "Pág 7 #1", mais limpo).
        labels = {}
        for p in paths:
            lbl = simpledialog.askstring(
                "Etiqueta do arquivo",
                f"Nome/etiqueta pra identificar estas cartelas:\n({Path(p).name})\n\n"
                "Aparece antes de cada cartela. Deixe VAZIO pra não usar etiqueta.",
                initialvalue=Path(p).stem, parent=self)
            if lbl is None:              # cancelou → aborta tudo
                return
            labels[p] = lbl.strip()
        self._log(f"Importando {len(paths)} arquivo(s)…")

        def run():
            try:
                from .. import merge
                total = 0
                for p in paths:
                    n = merge.import_file(self.session_id, p,
                                          source_label=labels[p])
                    total += n
                if self._scorer is not None:
                    self._scorer.recalculate_ranking(self.session_id)
                self.after(0, self._on_import_done, total, len(paths))
            except Exception as e:
                self.after(0, self._on_import_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_import_done(self, total: int, n_files: int):
        self._export_xlsx_btn.config(state=tk.NORMAL)
        self._export_csv_btn.config(state=tk.NORMAL)
        self._log(f"Importadas {total} cartelas de {n_files} arquivo(s). "
                  "Ranking atualizado.")
        if self._web:
            self._web.notify_update()
        messagebox.showinfo(
            "Importação concluída",
            f"{total} cartelas adicionadas de {n_files} arquivo(s).\n"
            "O ranking já inclui todas juntas.")

    def _on_import_error(self, msg: str):
        messagebox.showerror("Erro ao importar", msg)
        self._log(f"ERRO importando: {msg}", error=True)

    def _rename_source(self):
        """Troca ou remove a etiqueta de origem (o 'teste1a5 ·' antes da cartela)."""
        if self.session_id is None:
            messagebox.showwarning("Sessão", "Crie/processe uma sessão primeiro.")
            return
        from .. import database as db
        etiquetas = db.source_labels(self.session_id)
        if not etiquetas:
            messagebox.showinfo(
                "Sem etiqueta",
                "Não há etiqueta de origem pra trocar — os rótulos já estão limpos "
                "(só 'Pág X #Y').")
            return
        old = simpledialog.askstring(
            "Renomear etiqueta",
            "Etiquetas em uso: " + ", ".join(etiquetas) + "\n\n"
            "Qual você quer trocar? (copie exatamente uma da lista)",
            initialvalue=etiquetas[0], parent=self)
        if old is None:
            return
        old = old.strip()
        if old not in etiquetas:
            messagebox.showwarning("Etiqueta não encontrada",
                                   f"'{old}' não está na lista. Tente de novo.")
            return
        new = simpledialog.askstring(
            "Novo nome",
            f"Trocar '{old}' por qual nome?\n\n"
            "Deixe VAZIO para REMOVER a etiqueta (fica só 'Pág X #Y').",
            initialvalue=old, parent=self)
        if new is None:
            return
        try:
            n = db.rename_source(self.session_id, old, new)
            if self._scorer is not None:
                self._scorer.recalculate_ranking(self.session_id)
        except Exception as e:
            messagebox.showerror("Erro ao renomear", str(e))
            self._log(f"ERRO renomeando etiqueta: {e}", error=True)
            return
        virou = (new.strip() or "(sem etiqueta)")
        self._log(f"Etiqueta '{old}' → '{virou}' em {n} cartela(s).")
        if self._web:
            self._web.notify_update()
        messagebox.showinfo("Etiqueta atualizada ✅",
                            f"'{old}' virou '{virou}' em {n} cartela(s).\n\n"
                            "Reexporte o Excel pra ver.")

    def _renumber_pages(self):
        """Renumera as páginas da sessão pra bater com o número REAL da folha
        (ex.: 'essas folhas são da 7 até a 20'). Cada folha mantém suas 24."""
        if self.session_id is None:
            messagebox.showwarning("Sessão", "Crie/processe uma sessão primeiro.")
            return
        from .. import database as db
        cards = db.get_cards(self.session_id)
        if not cards:
            messagebox.showinfo("Sem cartelas",
                                "Processe ou importe cartelas antes de renumerar.")
            return
        n_pag = len(set(c["page"] for c in cards))
        start = simpledialog.askinteger(
            "Renumerar páginas",
            f"Tem {n_pag} folha(s) carregada(s).\n\n"
            f"A partir de qual NÚMERO de folha elas começam?\n"
            f"(ex.: digite 7 → vira folha 7, 8, 9… até {6 + n_pag})",
            initialvalue=1, minvalue=1, maxvalue=99999, parent=self)
        if start is None:
            return
        try:
            n = db.renumber_pages(self.session_id, start)
            if self._scorer is not None:
                self._scorer.recalculate_ranking(self.session_id)
        except Exception as e:
            messagebox.showerror("Erro ao renumerar", str(e))
            self._log(f"ERRO renumerando: {e}", error=True)
            return
        self._log(f"Renumeradas {n_pag} folha(s) começando na folha {start} "
                  f"({n} cartelas). Reexporte o Excel pra ver.")
        if self._web:
            self._web.notify_update()
        messagebox.showinfo(
            "Páginas renumeradas ✅",
            f"As folhas agora vão de {start} a {start + n_pag - 1}, "
            f"cada uma com suas cartelas.\n\n"
            "Exporte o Excel de novo pra ver a numeração nova.")

    # ── Publicar classificação no site ─────────────────────────────────────────

    def _publish_site(self):
        """Excel conferido → página .html da classificação geral, pronta pro site."""
        excel = filedialog.askopenfilename(
            title="Selecione o Excel/CSV JÁ CONFERIDO da classificação",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"), ("Todos", "*.*")])
        if not excel:
            return

        titulo = simpledialog.askstring(
            "Nome do bolão",
            "Título que aparece no site\n(ex.: Bolão do Zé — Classificação Geral):",
            initialvalue="Classificação Geral do Bolão", parent=self)
        if titulo is None:
            return
        titulo = titulo.strip() or "Classificação Geral do Bolão"

        out = filedialog.asksaveasfilename(
            title="Salvar página do site", defaultextension=".html",
            initialfile="classificacao.html",
            filetypes=[("Página web", "*.html")])
        if not out:
            return

        try:
            from ..web import publisher
            _, n = publisher.publish(excel, out, titulo=titulo,
                                     subtitulo="", data_txt="")
        except Exception as e:
            messagebox.showerror("Erro ao gerar página", str(e))
            self._log(f"ERRO ao publicar: {e}", error=True)
            return

        if n == 0:
            messagebox.showwarning(
                "Nada encontrado",
                "Não achei a classificação nesse arquivo.\n\n"
                "Use o Excel exportado pelo programa (com a coluna 'Total' "
                "preenchida) — lance os resultados oficiais antes de exportar.")
            return

        self._log(f"Página de classificação gerada: {out} ({n} participantes).")
        webbrowser.open(f"file:///{out.replace(chr(92), '/')}")
        messagebox.showinfo(
            "Página pronta! 🌐",
            f"Classificação com {n} participantes gerada.\n\n"
            f"Arquivo: {Path(out).name}\n\n"
            "Abri no navegador pra você conferir. É só enviar esse arquivo "
            ".html pro seu site (hospedagem) que a classificação aparece "
            "pra todo mundo — sem login.")

    def _publish_cloud(self):
        """Manda a sessão ATUAL pro servidor fixo da nuvem (link 24h) — a rodada
        aparece no link que não muda, sem depender deste PC ligado."""
        if self.session_id is None:
            messagebox.showwarning("Sem sessão",
                                   "Processe uma rodada antes de publicar na nuvem.")
            return
        from ..web import publish as pub
        cfg = pub.get_cloud_config()

        url = simpledialog.askstring(
            "Link fixo (nuvem)",
            "Endereço do seu servidor fixo\n(ex.: https://bolao.seudominio.com):",
            initialvalue=cfg["url"], parent=self)
        if not url or not url.strip():
            return
        key = simpledialog.askstring(
            "Senha do painel",
            "Senha do /admin do servidor (a mesma que lança resultado):",
            initialvalue=cfg["key"] or "bolao4729", parent=self)
        if key is None:
            return
        pub.set_cloud_config(url, key)      # lembra pra próxima vez
        self._log(f"Publicando a sessão {self.session_id} no link fixo…")

        def run():
            try:
                r = pub.publish_to_cloud(url, key, self.session_id)
                msg = (f"Publicado! {r.get('cartelas', '?')} cartelas no ar.\n\n"
                       f"Link pros clientes:\n{url.strip().rstrip('/')}/")
                def ok():
                    self._log(f"Publicado na nuvem: {r}")
                    if messagebox.askyesno("Publicado no link fixo! ☁️",
                                           msg + "\n\nAbrir o link agora?"):
                        webbrowser.open(url.strip().rstrip('/') + "/")
                self.after(0, ok)
            except Exception as e:
                self.after(0, lambda: (
                    self._log(f"ERRO ao publicar na nuvem: {e}", error=True),
                    messagebox.showerror("Erro ao publicar na nuvem", str(e))))

        threading.Thread(target=run, daemon=True).start()

    # ── AI reading (pen/photo sheets) ──────────────────────────────────────────

    def _ai_status_text(self) -> str:
        try:
            from ..omr import ai_reader
            if ai_reader.available():
                return ("✅ ATIVADA — folhas de caneta serão lidas pela IA "
                        "(digitais continuam grátis).")
        except Exception:
            pass
        return ("Desativada — folhas de caneta usam a leitura local. "
                "Configure a chave para ativar.")

    def _batch_enabled(self) -> bool:
        try:
            from ..omr import ai_reader
            return ai_reader.use_batch()
        except Exception:
            return False

    def _toggle_batch(self):
        try:
            from ..omr import ai_reader
            ai_reader.set_use_batch(self._batch_var.get())
            self._log("Modo lote " + ("LIGADO (metade do preço, resultado em "
                      "alguns minutos)." if self._batch_var.get()
                      else "desligado (leitura na hora, preço cheio)."))
        except Exception as e:
            self._log(f"Não consegui mudar o modo lote: {e}", error=True)

    def _config_ai_key(self):
        from tkinter import simpledialog
        key = simpledialog.askstring(
            "Chave da IA",
            "Cole a chave da API (sk-ant-...):\n"
            "(deixe vazio e OK para DESATIVAR a IA)",
            parent=self)
        if key is None:
            return
        try:
            from ..omr import ai_reader
            ai_reader.set_api_key(key)
            self._ai_status.config(text=self._ai_status_text())
            if key.strip():
                self._log("Leitura por IA ativada para folhas de caneta.")
            else:
                self._log("Leitura por IA desativada.")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _test_ai(self):
        """Diagnóstico da IA na máquina do usuário: chave, conexão e crédito —
        com o motivo exato em linguagem simples (faz 1 chamada mínima real)."""
        self._log("Testando a IA (chave, conexão, crédito)…")

        def run():
            try:
                from ..omr import ai_reader
                laudo = ai_reader.test_connection()
            except Exception as e:
                laudo = f"Erro ao testar: {type(e).__name__}: {e}"
            def show():
                self._ai_status.config(text=self._ai_status_text())
                for linha in laudo.splitlines():
                    if linha.strip():
                        self._log(linha)
                messagebox.showinfo("Teste da IA", laudo)
            self.after(0, show)

        threading.Thread(target=run, daemon=True).start()

    # ── Training (incremental, in-app) ─────────────────────────────────────────

    def _train_status_text(self) -> str:
        try:
            from ..omr import trainer
            n = trainer.sample_count()
        except Exception:
            n = 0
        return f"Amostras acumuladas: {n}"

    def _select_train_img(self):
        path = filedialog.askopenfilename(
            filetypes=[("Imagem/PDF", "*.pdf *.png *.jpg *.jpeg"), ("Todos", "*.*")])
        if path:
            self._train_img = path
            self._train_img_lbl.config(text=Path(path).name)
            self._update_train_btn()

    def _select_train_csv(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV/Excel", "*.csv *.xlsx *.xls"), ("Todos", "*.*")])
        if path:
            self._train_csv = path
            self._train_csv_lbl.config(text=Path(path).name)
            self._update_train_btn()

    def _update_train_btn(self):
        img = self._train_img or self._selected_file
        ready = bool(img) and bool(self._train_csv)
        self._train_btn.config(state=tk.NORMAL if ready else tk.DISABLED)

    def _train_model(self):
        img = self._train_img or self._selected_file
        if not (img and self._train_csv):
            return
        self._train_btn.config(state=tk.DISABLED)
        self._log("Treinando modelo…")

        def run():
            try:
                from ..omr import trainer
                stats = trainer.train_from_file(img, self._train_csv)
                self.after(0, self._on_train_done, stats)
            except Exception as e:
                self.after(0, self._on_train_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_train_done(self, stats: dict):
        self._train_btn.config(state=tk.NORMAL)
        self._train_status.config(text=self._train_status_text())
        acc = stats.get("train_accuracy", 0.0)
        self._log(f"Treino concluído: {stats.get('cards_matched', 0)} cartelas usadas, "
                  f"{stats.get('total_samples', 0)} amostras no total "
                  f"(precisão de treino {acc*100:.0f}%).")
        messagebox.showinfo(
            "Treino concluído",
            f"Cartelas usadas: {stats.get('cards_matched', 0)}\n"
            f"Amostras no total: {stats.get('total_samples', 0)}\n"
            f"Precisão de treino: {acc*100:.0f}%\n\n"
            "O modelo já vale para os próximos processamentos.")

    def _on_train_error(self, msg: str):
        self._train_btn.config(state=tk.NORMAL)
        messagebox.showerror("Erro ao treinar", msg)
        self._log(f"ERRO no treino: {msg}", error=True)

    # ── Web ───────────────────────────────────────────────────────────────────

    def _open_web(self):
        if self.web_url:
            webbrowser.open(self.web_url)
            ip = _get_local_ip()
            messagebox.showinfo(
                "Ranking Web",
                f"Ranking disponível em:\n\n"
                f"  {self.web_url}\n\n"
                f"Compartilhe este endereço com os participantes\n"
                f"(todos precisam estar na mesma rede Wi-Fi).\n\n"
                f"Página de administração:\n"
                f"  http://{ip}:5000/admin"
            )

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str, error: bool = False):
        self._log_text.config(state=tk.NORMAL)
        tag = "err" if error else ""
        self._log_text.tag_config("err", foreground=COLORS["danger"])
        self._log_text.insert(tk.END, msg + "\n", tag)
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)


def run():
    app = BolaoApp()
    app.mainloop()
