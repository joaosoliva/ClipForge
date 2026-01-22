import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import re
from PIL import Image, ImageTk
from baixar_imagens_google import download_google_images

# ---------------- CONFIG ----------------
DEFAULT_ROOT = "batches"
PYTHON_EXEC = sys.executable
SCRIPT_NAME = "main.py"
ICON_FILE = "clipforge.ico"

GUIDE_MODES = ["text-only", "image-only", "image-with-text"]
TEXT_ANCHOR_OPTIONS = ["", "top", "bottom"]
LAYOUT_OPTIONS = [
    "image_center_only",
    "legacy_single",
    "stickman_center_only",
    "two_images_center",
    "stickman_left_3img",
]
STICKMAN_ANIM_OPTIONS = ["", "walk_to_final", "pop_to_final", "slide_to_final"]
STICKMAN_ANIM_DIRECTIONS = ["left", "right"]
STICKMAN_POSITION_OPTIONS = ["left", "right"]

PROG_RE = re.compile(r"^\[(\d+)/(\d+)\s*\|\s*(\d+)%\]")

env = os.environ.copy()
env["PYTHONUNBUFFERED"] = "1"

SRT_EDIT_FILENAME = "srt_edit.json"


# ---------------- SRT HELPERS (soft dependency) ----------------
try:
    import pysrt  # type: ignore
    PYSRT_OK = True
except Exception:
    pysrt = None
    PYSRT_OK = False


def _find_srt_file(batch_dir: str) -> str | None:
    """Prioriza audio.srt; senão, primeiro .srt encontrado."""
    preferred = os.path.join(batch_dir, "audio.srt")
    if os.path.exists(preferred):
        return preferred
    try:
        for f in os.listdir(batch_dir):
            if f.lower().endswith(".srt"):
                return os.path.join(batch_dir, f)
    except Exception:
        pass
    return None


def _srt_time_to_sec(t) -> float:
    # pysrt.SubRipTime tem .ordinal em ms
    return t.ordinal / 1000.0


def _fmt_sec(sec: float) -> str:
    # só pra preview no painel
    if sec < 0:
        sec = 0
    msec = int(round(sec * 1000))
    ms = msec % 1000
    s = (msec // 1000) % 60
    m = (msec // 60000) % 60
    h = (msec // 3600000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _norm_text(text: str) -> str:
    return text.lower().strip()


def trigger_in_text(trigger: str, text: str) -> bool:
    """
    Verifica se o trigger ocorre no texto.
    - Case-insensitive
    - Se trigger tiver espaço, faz substring simples
    - Caso contrário, usa word-boundary
    """
    if not trigger or not text:
        return False

    t = _norm_text(trigger)
    s = _norm_text(text)

    if " " in t:
        return t in s

    return re.search(rf"\b{re.escape(t)}\b", s) is not None

def _safe_json_load(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _safe_json_save(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _split_text_by_trigger(full_text: str, trigger: str):
    """
    Split preservando caixa do texto original:
    acha ocorrência case-insensitive, mas recorta no texto original.
    Retorna (before, match, after) onde match é o trecho correspondente ao trigger no texto original.
    """
    if not trigger.strip():
        return None

    ft = full_text
    tl = trigger.strip()

    idx = ft.lower().find(tl.lower())
    if idx < 0:
        return None

    match = ft[idx:idx + len(tl)]
    before = ft[:idx].strip()
    after = ft[idx + len(tl):].strip()

    # Reconstroi segunda linha como "match + after", mantendo o match original
    second = (match + (" " + after if after else "")).strip()
    return before, second


def _proportional_split_times(t0: float, t1: float, full_text: str, before_text: str) -> float:
    """
    split_time = t0 + (t1-t0) * ratio, ratio baseado em chars.
    """
    dur = max(0.001, t1 - t0)
    total = max(1, len(full_text))
    ratio = len(before_text) / total
    # clamp
    ratio = max(0.05, min(0.95, ratio))
    return t0 + dur * ratio


# ---------------- MAIN GUI ----------------
class App(tk.Tk):
    """
    ClipForge - GUI Tkinter com abas
    - Aba Render: renderização de vídeos
    - Aba Edit: edição visual do guia.json + (agora) SRT-aware via srt_edit.json
    """

    def __init__(self):
        super().__init__()

        self.title("ClipForge")
        self.geometry("800x700")
        self.configure(bg="#c0c0c0")
        self.resizable(False, False)

        # Ícone (opcional)
        try:
            if os.path.exists(ICON_FILE):
                self.iconbitmap(ICON_FILE)
        except Exception:
            pass

        self.root_dir = tk.StringVar(value=DEFAULT_ROOT)

        # Criar notebook (sistema de abas)
        self.notebook = ttk.Notebook(self)
        self.notebook.place(x=10, y=10, width=780, height=680)

        # Criar abas
        self.render_tab = RenderTab(self.notebook, self.root_dir)
        self.edit_tab = EditTab(self.notebook, self.root_dir)
        self.tools_tab = ToolsTab(self.notebook, self.root_dir)

        self.notebook.add(self.render_tab, text="  Render  ")
        self.notebook.add(self.edit_tab, text="  Edit  ")
        self.notebook.add(self.tools_tab, text="  Tools  ")


# ---------------- ABA RENDER ----------------
class RenderTab(tk.Frame):
    """Aba de renderização (código original)"""

    def __init__(self, parent, root_dir_var):
        super().__init__(parent, bg="#c0c0c0")
        self.root_dir = root_dir_var
        self.status = tk.StringVar(value="Pronto.")
        self.is_running = False
        self.use_stickman = tk.BooleanVar(value=True)
        self.convert_png_to_jpg = tk.BooleanVar(value=False)
        self.output_dir = tk.StringVar(value="output")

        self._build_ui()
        self._refresh_jobs()

    def _build_ui(self):
        # Frame topo
        frm = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        frm.place(x=10, y=10, width=740, height=260)

        tk.Checkbutton(
            frm,
            text="Usar personagem (stickman)",
            variable=self.use_stickman,
            bg="#c0c0c0",
            activebackground="#c0c0c0",
        ).place(x=350, y=45)
        tk.Checkbutton(
            frm,
            text="Converter PNG para JPG (batches)",
            variable=self.convert_png_to_jpg,
            bg="#c0c0c0",
            activebackground="#c0c0c0",
        ).place(x=350, y=68)

        tk.Label(frm, text="Pasta raiz:", bg="#c0c0c0").place(x=10, y=12)
        tk.Entry(frm, textvariable=self.root_dir, width=60).place(x=90, y=12)
        tk.Button(frm, text="Procurar...", command=self._browse_root, width=14).place(x=600, y=8)

        tk.Label(frm, text="Pasta output:", bg="#c0c0c0").place(x=10, y=35)
        tk.Entry(frm, textvariable=self.output_dir, width=60).place(x=90, y=35)
        self.btn_browse_output = tk.Button(
            frm, text="Procurar...", command=self._browse_output, width=14
        )
        self.btn_browse_output.place(x=600, y=31)

        tk.Label(frm, text="Batches encontrados:", bg="#c0c0c0").place(x=10, y=60)
        self.listbox = tk.Listbox(frm, height=10, width=40)
        self.listbox.place(x=10, y=85)

        self.btn_refresh = tk.Button(frm, text="Atualizar", width=22, command=self._refresh_jobs)
        self.btn_refresh.place(x=350, y=85)

        self.btn_run_sel = tk.Button(frm, text="Renderizar selecionado", width=22, command=self._run_selected)
        self.btn_run_sel.place(x=350, y=125)

        self.btn_run_all = tk.Button(frm, text="Renderizar todos", width=22, command=self._run_all)
        self.btn_run_all.place(x=350, y=165)

        self.btn_open_out = tk.Button(frm, text="Abrir output", width=22, command=self._open_output)
        self.btn_open_out.place(x=350, y=205)

        self.btn_keep_log = tk.Button(frm, text="Limpar log", width=22, command=self._clear_log)
        self.btn_keep_log.place(x=350, y=245)

        # Barra de progresso
        prog_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="sunken")
        prog_frame.place(x=10, y=280, width=740, height=35)

        self.prog_canvas = tk.Canvas(prog_frame, width=710, height=16, bg="white", highlightthickness=0)
        self.prog_canvas.place(x=12, y=9)
        self._set_progress(0)

        # Status bar
        status_bar = tk.Frame(self, bg="#808080", bd=1, relief="sunken")
        status_bar.place(x=10, y=320, width=740, height=25)
        tk.Label(status_bar, textvariable=self.status, bg="#808080", fg="white").place(x=6, y=3)

        # Log
        log_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="sunken")
        log_frame.place(x=10, y=355, width=740, height=265)

        self.log = tk.Text(
            log_frame,
            bg="black",
            fg="white",
            insertbackground="white",
            state="disabled",
            font=("Courier New", 9),
            wrap="none"
        )
        self.log.place(x=6, y=6, width=708, height=230)

        self.scroll = tk.Scrollbar(log_frame, command=self.log.yview)
        self.scroll.place(x=714, y=6, width=18, height=230)
        self.log.configure(yscrollcommand=self.scroll.set)

        tk.Button(log_frame, text="Sair", width=12, command=self.quit).place(x=640, y=238)

    def _append_log(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_progress(self, pct: int):
        pct = max(0, min(100, int(pct)))
        self.prog_canvas.delete("all")
        w = int((pct / 100) * 710)
        if w > 0:
            self.prog_canvas.create_rectangle(0, 0, w, 16, fill="#000080", outline="#000080")
        self.prog_canvas.create_rectangle(0, 0, 710, 16, outline="#808080")

    def _set_running(self, running: bool):
        self.is_running = running
        state = "disabled" if running else "normal"
        for b in [
            self.btn_refresh,
            self.btn_run_sel,
            self.btn_run_all,
            self.btn_open_out,
            self.btn_keep_log,
            self.btn_browse_output,
        ]:
            b.configure(state=state)

    def _browse_root(self):
        path = filedialog.askdirectory()
        if path:
            self.root_dir.set(path)
            self._refresh_jobs()

    def _browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir.set(path)

    def _refresh_jobs(self):
        root = self.root_dir.get()
        self.listbox.delete(0, tk.END)

        if not os.path.isdir(root):
            self.status.set("Pasta raiz inválida.")
            return

        jobs = [d for d in sorted(os.listdir(root)) if d.isdigit() and os.path.isdir(os.path.join(root, d))]
        for j in jobs:
            self.listbox.insert(tk.END, j)

        self.status.set(f"{len(jobs)} batch(es) encontrados.")
        self._validate_jobs(jobs)

    def _validate_jobs(self, jobs):
        root = self.root_dir.get()
        self.listbox.delete(0, tk.END)

        for j in jobs:
            ok, _ = self._validate_single_job(j, root)
            self.listbox.insert(tk.END, j)
            if not ok:
                idx = self.listbox.size() - 1
                self.listbox.itemconfig(idx, bg="#ffb0b0")

    def _validate_single_job(self, job, root, show_dialog=False):
        base = os.path.join(root, job)

        required_paths = [
            ("guia.json", os.path.join(base, "guia.json")),
            ("imagens/", os.path.join(base, "imagens")),
        ]

        if self.use_stickman.get():
            required_paths.insert(1, ("stickman.json", os.path.join(base, "stickman.json")))

        missing = []
        for label, p in required_paths:
            if label.endswith("/") and not os.path.isdir(p):
                missing.append(label)
            elif not label.endswith("/") and not os.path.exists(p):
                missing.append(label)

        audio_ok = False
        srt_ok = False
        try:
            for f in os.listdir(base):
                fl = f.lower()
                if fl.startswith("audio") and fl.endswith((".mp3", ".wav", ".m4a", ".aac", ".flac")):
                    audio_ok = True
                if fl.endswith(".srt"):
                    srt_ok = True
        except FileNotFoundError:
            missing.append("pasta do batch")
            audio_ok = True
            srt_ok = True

        if not audio_ok:
            missing.append("audio.*")
        if not srt_ok:
            missing.append("*.srt")

        ok = (len(missing) == 0)

        if (not ok) and show_dialog:
            messagebox.showerror(
                "Batch inválido",
                f"Batch {job} está incompleto.\n\nFaltando:\n" + "\n".join(missing)
            )

        return ok, missing

    def _run_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um batch.")
            return

        job = self.listbox.get(sel[0])
        ok, _ = self._validate_single_job(job, self.root_dir.get(), show_dialog=True)
        if not ok:
            return

        self._run_process(job)

    def _run_all(self):
        jobs = list(self.listbox.get(0, tk.END))
        root = self.root_dir.get()

        invalid = []
        for j in jobs:
            ok, _ = self._validate_single_job(j, root)
            if not ok:
                invalid.append(j)

        if invalid:
            messagebox.showerror(
                "Validação",
                "Os seguintes batches estão inválidos:\n\n" + "\n".join(invalid)
            )
            return

        self._run_process(None)

    def _run_process(self, job):
        if self.is_running:
            return

        script_path = os.path.join(os.path.dirname(__file__), SCRIPT_NAME)
        if not os.path.exists(script_path):
            messagebox.showerror("Erro", f"Não encontrei {SCRIPT_NAME} ao lado do gui.py")
            return

        root = self.root_dir.get()
        output_root = self.output_dir.get()

        def worker():
            self._set_running(True)
            self._set_progress(0)
            self.status.set("Processando...")
            self._append_log("C:\\> Iniciando...")

            cmd = [PYTHON_EXEC, "-u", script_path, "--root", root, "--output", output_root]
            if job:
                cmd += ["--job", job]
            if not self.use_stickman.get():
                cmd += ["--no-stickman"]
            if self.convert_png_to_jpg.get():
                cmd += ["--convert-png-to-jpg"]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )

                for line in proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue

                    self.after(0, self._append_log, line)

                    if line.startswith("[") and "|" in line and "%" in line:
                        try:
                            m = PROG_RE.match(line)
                            if m:
                                pct = int(m.group(3))
                                self.after(0, self._set_progress, pct)
                        except Exception:
                            pass

                proc.wait()

                if proc.returncode == 0:
                    self.after(0, self._set_progress, 100)
                    self.after(0, self.status.set, "Concluído com sucesso.")
                    self.after(0, self._append_log, "C:\\> Finalizado.")
                else:
                    self.after(0, self.status.set, "Erro durante o processamento.")
                    self.after(0, self._append_log, f"C:\\> ERRO (code={proc.returncode}).")

            except Exception as e:
                self.after(0, self.status.set, "Erro durante o processamento.")
                self.after(0, self._append_log, f"C:\\> EXCEÇÃO: {e}")

            finally:
                self.after(0, self._set_running, False)

        threading.Thread(target=worker, daemon=True).start()

    def _open_output(self):
        out = os.path.abspath(self.output_dir.get())
        if os.path.isdir(out):
            os.startfile(out)
        else:
            messagebox.showinfo("Info", "Pasta output ainda não existe.")


# ---------------- ABA EDIT ----------------
class EditTab(tk.Frame):
    """Aba de edição do guia.json + edição SRT intermediária (srt_edit.json)"""

    def __init__(self, parent, root_dir_var):
        super().__init__(parent, bg="#c0c0c0")
        self.root_dir = root_dir_var
        self.current_batch = None
        self.guide_data = []
        self.guide_path = None
        self.current_photo = None  # Para manter referência da imagem

        # SRT state
        self.srt_path = None
        self.subs = None  # pysrt.SubRipFile
        self.srt_edit_path = None
        self.srt_edits = []  # list[dict]
        self._preview_segments = None
        self._current_sub = None
        
        # Stickman state
        self.stickman_path = None
        self.stickman_data = []
        self.srt_phrases = []
        self.current_phrase = None

        self._autosave_after_id = None
        self._last_selected_index = None
        self.guide_status = tk.StringVar(value="guia.json: não carregado")

        self._build_ui()
        
    def _on_root_dir_changed(self, *args):
        if not hasattr(self, "trigger_listbox"):
            return
        """
        Chamado automaticamente quando a pasta raiz muda.
        Atualiza batches e limpa estado antigo.
        """
        self.current_batch = None
        self.guide_data = []
        self.guide_path = None

        # Limpar UI
        self.trigger_listbox.delete(0, tk.END)
        self.batch_status.config(text="Selecione um batch")
        self._set_guide_status("guia.json: não carregado", "#666")

        self._srt_clear_boxes()

        # Atualizar lista de batches
        self._refresh_batches()
        self.root_dir.trace_add("write", self._on_root_dir_changed)

    def _build_ui(self):
        # Frame seleção de batch
        select_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        select_frame.place(x=10, y=10, width=740, height=80)

        tk.Label(select_frame, text="Batch:", bg="#c0c0c0").place(x=10, y=12)

        self.batch_combo = ttk.Combobox(select_frame, state="readonly", width=15)
        self.batch_combo.place(x=60, y=12)
        self.batch_combo.bind("<<ComboboxSelected>>", self._on_batch_selected)

        tk.Button(select_frame, text="Atualizar lista", width=15, command=self._refresh_batches).place(x=200, y=8)
        tk.Button(select_frame, text="Salvar guia.json", width=15, command=self._save_guide).place(x=340, y=8)
        tk.Button(select_frame, text="Recarregar", width=15, command=self._reload_guide).place(x=480, y=8)

        self.batch_status = tk.Label(select_frame, text="Selecione um batch", bg="#c0c0c0", fg="#666")
        self.batch_status.place(x=10, y=45)
        self.guide_status_label = tk.Label(
            select_frame,
            textvariable=self.guide_status,
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 8),
        )
        self.guide_status_label.place(x=300, y=45)

        # Frame lista de triggers
        list_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        list_frame.place(x=10, y=100, width=300, height=530)

        tk.Label(list_frame, text="Itens do guia (triggers):", bg="#c0c0c0").place(x=10, y=10)

        self.trigger_listbox = tk.Listbox(
            list_frame,
            height=25,
            width=35,
            selectmode=tk.EXTENDED,
            exportselection=False
        )
        self.trigger_listbox.place(x=10, y=35)
        self.trigger_listbox.bind("<<ListboxSelect>>", self._on_trigger_selected)

        list_scroll = tk.Scrollbar(list_frame, command=self.trigger_listbox.yview)
        list_scroll.place(x=270, y=35, height=420)
        self.trigger_listbox.configure(yscrollcommand=list_scroll.set)

        tk.Label(list_frame, text="Shift/Ctrl para múltipla seleção", bg="#c0c0c0", fg="#666", font=("Arial", 8)).place(x=10, y=460)

        tk.Button(list_frame, text="Remover selecionado(s)", width=20, command=self._remove_trigger).place(x=80, y=485)

        # Frame direita com sub-abas (Guia / SRT)
        right_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        right_frame.place(x=320, y=100, width=430, height=530)

        self.edit_notebook = ttk.Notebook(right_frame)
        self.edit_notebook.place(x=5, y=5, width=420, height=520)

        self.tab_guia = tk.Frame(self.edit_notebook, bg="#c0c0c0")
        self.tab_srt = tk.Frame(self.edit_notebook, bg="#c0c0c0")
        self.tab_stickman = tk.Frame(self.edit_notebook, bg="#c0c0c0")

        self.edit_notebook.add(self.tab_guia, text="  Guia  ")
        self.edit_notebook.add(self.tab_srt, text="  SRT  ")
        self.edit_notebook.add(self.tab_stickman, text="  Stickman  ")

        self._build_guia_tab()
        self._build_srt_tab()
        self._build_stickman_tab()

        self._refresh_batches()

    def _shift_image_id(self, delta: int):
        sel = self.trigger_listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um ou mais triggers.")
            return

        changed = 0
        skipped = 0

        for idx in sel:
            item = self.guide_data[idx]
            image_ids = item.get("image_ids")
            if isinstance(image_ids, list) and image_ids:
                updated = []
                for img_id in image_ids:
                    img_id = str(img_id).strip()
                    if not img_id.isdigit():
                        skipped += 1
                        updated.append(img_id)
                        continue
                    num = int(img_id)
                    new_num = num + delta
                    if new_num < 0:
                        skipped += 1
                        updated.append(img_id)
                        continue
                    width = len(img_id)
                    updated.append(str(new_num).zfill(width))
                    changed += 1
                item["image_ids"] = updated
            else:
                img_id = str(item.get("image_id", "")).strip()
                if not img_id.isdigit():
                    skipped += 1
                    continue
                num = int(img_id)
                new_num = num + delta
                if new_num < 0:
                    skipped += 1
                    continue
                width = len(img_id)
                item["image_id"] = str(new_num).zfill(width)
                changed += 1

        self._refresh_trigger_list()

        for idx in sel:
            self.trigger_listbox.selection_set(idx)

        self._set_guide_status("guia.json: alterações pendentes", "#b36b00")
        self._save_guide(show_messages=False)

        messagebox.showinfo(
            "Image ID atualizado",
            f"Alterados: {changed}\nIgnorados: {skipped}"
        )

    # ---------------- GUI TAB (original, intact) ----------------

    def _build_guia_tab(self):
        canvas = tk.Canvas(self.tab_guia, bg="#c0c0c0", highlightthickness=0)
        scrollbar = tk.Scrollbar(self.tab_guia, orient="vertical", command=canvas.yview)

        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        content = tk.Frame(canvas, bg="#c0c0c0")

        window_id = canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
            width=canvas.winfo_reqwidth()
        )

        def _on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        inner = tk.Frame(content, bg="#c0c0c0", width=400, height=700)
        inner.pack(anchor="nw", fill="both", expand=True, padx=10, pady=10)
        inner.pack_propagate(False)

        edit_frame = inner

        self.selection_label = tk.Label(
            edit_frame,
            text="Nenhum item selecionado. Selecione um trigger à esquerda.",
            bg="#c0c0c0",
            font=("Arial", 10, "bold")
        )
        self.selection_label.place(x=10, y=10)

        # Trigger
        tk.Label(edit_frame, text="Trigger (ativação):", bg="#c0c0c0").place(x=10, y=45)
        self.trigger_entry = tk.Entry(edit_frame, width=50)
        self.trigger_entry.place(x=140, y=45)
        tk.Label(
            edit_frame,
            text="Palavra/frase que ativa o item na legenda.",
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 8)
        ).place(x=140, y=65)

        # Text (opcional)
        tk.Label(edit_frame, text="Texto (opcional):", bg="#c0c0c0").place(x=10, y=90)
        self.text_entry = tk.Entry(edit_frame, width=50)
        self.text_entry.place(x=140, y=90)

        # Image IDs
        tk.Label(edit_frame, text="IDs de imagem:", bg="#c0c0c0").place(x=10, y=125)
        self.image_id_entry = tk.Entry(edit_frame, width=50)
        self.image_id_entry.place(x=140, y=125)
        tk.Label(
            edit_frame,
            text="Ex.: 12 ou 12,13,14",
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 8)
        ).place(x=140, y=145)

        tk.Button(
            edit_frame,
            text="ID -1",
            width=20,
            command=lambda: self._shift_image_id(-1)
        ).place(x=10, y=165)

        tk.Button(
            edit_frame,
            text="ID +1",
            width=20,
            command=lambda: self._shift_image_id(+1)
        ).place(x=200, y=165)

        # Preview da imagem
        tk.Label(edit_frame, text="Preview:", bg="#c0c0c0", font=("Arial", 9, "bold")).place(x=10, y=200)

        preview_container = tk.Frame(edit_frame, bg="#e0e0e0", relief="sunken", bd=2)
        preview_container.place(x=10, y=220, width=400, height=60)

        self.preview_label = tk.Label(
            preview_container,
            text="Selecione um item para ver a imagem",
            bg="#e0e0e0",
            fg="#666"
        )
        self.preview_label.pack(expand=True, fill="both")

        # Mode + Layout
        tk.Label(edit_frame, text="Modo:", bg="#c0c0c0").place(x=10, y=270)
        self.mode_combo = ttk.Combobox(edit_frame, values=GUIDE_MODES, state="readonly", width=18)
        self.mode_combo.place(x=90, y=270)
        self.mode_combo.set(GUIDE_MODES[1])
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mode_changed())

        tk.Label(edit_frame, text="Layout:", bg="#c0c0c0").place(x=250, y=270)
        self.layout_combo = ttk.Combobox(edit_frame, values=LAYOUT_OPTIONS, state="readonly", width=18)
        self.layout_combo.place(x=310, y=270)
        self.layout_combo.set("legacy_single")
        tk.Label(
            edit_frame,
            text="Define como imagens/texto aparecem.",
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 8)
        ).place(x=90, y=292)

        # Text anchor/margin
        tk.Label(edit_frame, text="Âncora do texto:", bg="#c0c0c0").place(x=10, y=310)
        self.text_anchor_combo = ttk.Combobox(
            edit_frame, values=TEXT_ANCHOR_OPTIONS, state="readonly", width=10
        )
        self.text_anchor_combo.place(x=140, y=310)
        self.text_anchor_combo.set(TEXT_ANCHOR_OPTIONS[0])

        tk.Label(edit_frame, text="Margem (px):", bg="#c0c0c0").place(x=250, y=310)
        self.text_margin_entry = tk.Entry(edit_frame, width=8)
        self.text_margin_entry.place(x=330, y=310)

        # Stickman animation
        tk.Label(edit_frame, text="Animação do Stickman:", bg="#c0c0c0").place(x=10, y=345)
        self.stickman_anim_combo = ttk.Combobox(
            edit_frame, values=STICKMAN_ANIM_OPTIONS, state="readonly", width=18
        )
        self.stickman_anim_combo.place(x=170, y=345)
        self.stickman_anim_combo.set("")

        tk.Label(edit_frame, text="Direção:", bg="#c0c0c0").place(x=250, y=345)
        self.stickman_anim_dir_combo = ttk.Combobox(
            edit_frame, values=STICKMAN_ANIM_DIRECTIONS, state="readonly", width=8
        )
        self.stickman_anim_dir_combo.place(x=310, y=345)
        tk.Label(edit_frame, text="Posição:", bg="#c0c0c0").place(x=400, y=345)
        self.stickman_position_combo = ttk.Combobox(
            edit_frame, values=STICKMAN_POSITION_OPTIONS, state="readonly", width=8
        )
        self.stickman_position_combo.place(x=460, y=345)
        self.stickman_position_combo.set("left")

        # Effects
        tk.Label(edit_frame, text="Efeitos:", bg="#c0c0c0", font=("Arial", 9, "bold")).place(x=10, y=375)

        # Zoom
        self.zoom_var = tk.BooleanVar()
        tk.Checkbutton(
            edit_frame,
            text="Zoom",
            variable=self.zoom_var,
            bg="#c0c0c0",
            command=self._schedule_auto_save,
        ).place(x=10, y=400)

        # Slide
        tk.Label(edit_frame, text="Slide:", bg="#c0c0c0").place(x=10, y=425)
        self.slide_var = tk.StringVar(value="none")
        slide_options = ["none", "left", "right", "up", "down"]

        for i, opt in enumerate(slide_options):
            tk.Radiobutton(
                edit_frame,
                text=opt,
                variable=self.slide_var,
                value=opt,
                bg="#c0c0c0",
                command=self._schedule_auto_save,
            ).place(x=10 + (i * 70), y=450)

        # Botões de ação
        tk.Button(
            edit_frame,
            text="Salvar item",
            width=20,
            command=lambda: self._apply_changes(autosave=True),
        ).place(x=10, y=520)
        tk.Button(edit_frame, text="Aplicar efeitos no batch", width=20, command=self._apply_batch_effects).place(x=200, y=520)
        tk.Button(edit_frame, text="Novo item", width=20, command=self._add_new_trigger).place(x=10, y=555)
        tk.Button(edit_frame, text="Remover zoom do batch", width=20, command=self._disable_batch_zoom).place(x=200, y=555)

        self._bind_autosave_events()

    def _bind_autosave_events(self):
        entry_widgets = [
            self.trigger_entry,
            self.text_entry,
            self.image_id_entry,
            self.text_margin_entry,
        ]
        for widget in entry_widgets:
            widget.bind("<KeyRelease>", self._schedule_auto_save)
            widget.bind("<FocusOut>", self._schedule_auto_save)

        combo_widgets = [
            self.layout_combo,
            self.text_anchor_combo,
            self.stickman_anim_combo,
            self.stickman_anim_dir_combo,
            self.stickman_position_combo,
        ]
        for widget in combo_widgets:
            widget.bind("<<ComboboxSelected>>", self._schedule_auto_save)

    def _on_mode_changed(self):
        self._sync_mode_fields()
        self._schedule_auto_save()

    def _schedule_auto_save(self, event=None):
        if not self.current_batch or not self.guide_path:
            return
        sel = self.trigger_listbox.curselection()
        if len(sel) != 1:
            return
        self._set_guide_status("guia.json: alterações pendentes", "#b36b00")
        if self._autosave_after_id:
            self.after_cancel(self._autosave_after_id)
        self._autosave_after_id = self.after(400, self._auto_apply_changes)

    def _auto_apply_changes(self):
        self._autosave_after_id = None
        self._apply_changes(show_messages=False, autosave=True)

    # ---------------- SRT TAB (new) ----------------

    def _build_srt_tab(self):
        frm = self.tab_srt

        self.srt_status = tk.Label(
            frm,
            text="Selecione um batch para carregar a legenda e editar splits.",
            bg="#c0c0c0",
            fg="#333"
        )
        self.srt_status.place(x=10, y=10)

        # Informações da legenda associada
        tk.Label(frm, text="Legenda original (SRT):", bg="#c0c0c0", font=("Arial", 9, "bold")).place(x=10, y=35)

        self.srt_orig_box = tk.Text(frm, height=5, width=52, state="disabled", wrap="word")
        self.srt_orig_box.place(x=10, y=60)

        tk.Label(frm, text="Trigger para split (texto exato):", bg="#c0c0c0").place(x=10, y=155)
        self.srt_trigger_entry = tk.Entry(frm, width=45)
        self.srt_trigger_entry.place(x=10, y=175)
        tk.Label(
            frm,
            text="Use o trigger selecionado ou ajuste se necessário.",
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 8)
        ).place(x=10, y=195)

        # Preview
        tk.Label(frm, text="Preview do split (não altera o .srt):", bg="#c0c0c0", font=("Arial", 9, "bold")).place(x=10, y=220)

        self.srt_preview_box = tk.Text(frm, height=6, width=52, state="disabled", wrap="word")
        self.srt_preview_box.place(x=10, y=245)

        # Botões
        self.btn_srt_recalc = tk.Button(frm, text="Ver preview", width=18, command=self._srt_recalc_preview)
        self.btn_srt_recalc.place(x=10, y=365)

        self.btn_srt_apply = tk.Button(frm, text="Aplicar split", width=22, command=self._srt_apply_edit)
        self.btn_srt_apply.place(x=165, y=365)

        self.btn_srt_save = tk.Button(frm, text="Salvar edição SRT", width=18, command=self._srt_save_file)
        self.btn_srt_save.place(x=10, y=405)

        self.btn_srt_revert = tk.Button(frm, text="Desfazer split", width=18, command=self._srt_revert_current)
        self.btn_srt_revert.place(x=165, y=405)

        # Desabilitar se não tem pysrt
        if not PYSRT_OK:
            self.srt_status.config(text="SRT: pysrt não está instalado (sub-aba desativada).", fg="#a00")
            for b in [self.btn_srt_recalc, self.btn_srt_apply, self.btn_srt_save, self.btn_srt_revert]:
                b.config(state="disabled")

    #--stickman-tab
    def _build_stickman_tab(self):
        # Canvas + Scrollbar
        canvas = tk.Canvas(self.tab_stickman, bg="#c0c0c0", highlightthickness=0)
        scrollbar = tk.Scrollbar(self.tab_stickman, orient="vertical", command=canvas.yview)

        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Frame interno (conteúdo real)
        content = tk.Frame(canvas, bg="#c0c0c0")

        window_id = canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
            width=canvas.winfo_reqwidth()
        )
        
        def _on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)

        # Atualizar scrollregion automaticamente
        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        # =========================
        # AGORA coloque TODO o layout
        # DENTRO de `content`
        # =========================

        # Container interno com padding
        inner = tk.Frame(content, bg="#c0c0c0")
        inner.pack(anchor="nw", fill="x", padx=10, pady=10)

        tk.Label(inner, text="Frases do SRT:", bg="#c0c0c0").pack(anchor="w")

        self.stickman_list = tk.Listbox(
            inner,
            width=45,
            height=18,
            exportselection=False
        )
        self.stickman_list.pack(anchor="w", pady=(5, 20))
        self.stickman_list.bind("<<ListboxSelect>>", self._on_stickman_phrase)

        tk.Label(inner, text="Trigger:", bg="#c0c0c0").pack(anchor="w")
        self.sm_trigger_entry = tk.Entry(inner, width=45)
        self.sm_trigger_entry.pack(anchor="w", pady=5)

        tk.Label(inner, text="Expressão:", bg="#c0c0c0").pack(anchor="w")
        self.sm_expression = ttk.Combobox(
            inner,
            values=[
                "neutral", "apontando_alto", "apontando_meio",
                "joinha_feliz", "preocupado", "negando_cansado", "prancheta", "assustado", "negando_feliz"
            ],
            state="readonly",
            width=30
        )
        self.sm_expression.pack(anchor="w", pady=5)
        self.sm_expression.set("neutral")

        tk.Label(inner, text="Speech (opcional):", bg="#c0c0c0").pack(anchor="w")
        self.sm_speech = tk.Entry(inner, width=45)
        self.sm_speech.pack(anchor="w", pady=5)

        btns = tk.Frame(inner, bg="#c0c0c0")
        btns.pack(anchor="w", pady=10)

        tk.Button(btns, text="Adicionar / Atualizar", width=22,
                  command=self._save_stickman_entry).pack(side="left", padx=5)

        tk.Button(btns, text="Remover", width=22,
                  command=self._remove_stickman_entry).pack(side="left", padx=5)

    # ---------------- BATCH LOAD ----------------

    def _refresh_batches(self):
        root = self.root_dir.get()
        if not os.path.isdir(root):
            return

        batches = [d for d in sorted(os.listdir(root)) if d.isdigit() and os.path.isdir(os.path.join(root, d))]
        self.batch_combo['values'] = batches

        if not batches:
            self.batch_combo.set("")
            return

        # Se batch atual não existe mais, seleciona o primeiro
        if self.current_batch not in batches:
            self.batch_combo.current(0)
            self._on_batch_selected(None)

    def _on_batch_selected(self, event):
        batch = self.batch_combo.get()
        if not batch:
            return

        self.current_batch = batch
        base = os.path.join(self.root_dir.get(), batch)

        guide_file = os.path.join(base, "guia.json")
        if not os.path.exists(guide_file):
            messagebox.showerror("Erro", f"guia.json não encontrado no batch {batch}")
            self._set_guide_status("guia.json: não carregado", "#666")
            return

        self.guide_path = guide_file
        self._load_guide()

        # SRT load
        self._load_srt_context()
        
        #stickman LOAD
        self._load_stickman()
        self._build_srt_phrases()
        self._refresh_stickman_list()


    def _load_guide(self):
        try:
            with open(self.guide_path, 'r', encoding='utf-8') as f:
                self.guide_data = json.load(f)

            self._refresh_trigger_list()
            self.batch_status.config(text=f"Batch {self.current_batch}: {len(self.guide_data)} triggers carregados")
            self._set_guide_status("guia.json carregado", "#666")
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao carregar guia.json:\n{e}")
            self._set_guide_status("guia.json: erro ao carregar", "#a00")

    def _load_srt_context(self):
        """Carrega .srt + srt_edit.json (se houver)."""
        self.srt_path = None
        self.subs = None
        self.srt_edits = []
        self.srt_edit_path = None
        self._current_sub = None
        self._preview_segments = None

        if not PYSRT_OK:
            return

        if not self.current_batch:
            return

        base = os.path.join(self.root_dir.get(), self.current_batch)

        srt_path = _find_srt_file(base)
        if not srt_path:
            self.srt_status.config(text="SRT: nenhum .srt encontrado no batch", fg="#a00")
            return

        self.srt_path = srt_path
        self.srt_edit_path = os.path.join(base, SRT_EDIT_FILENAME)
        self.srt_edits = _safe_json_load(self.srt_edit_path, [])

        try:
            self.subs = pysrt.open(self.srt_path, encoding="utf-8")
            self.srt_status.config(
                text=f"SRT: {os.path.basename(self.srt_path)} | edits: {len(self.srt_edits)}",
                fg="#333"
            )
        except Exception as e:
            self.srt_status.config(text=f"SRT: erro ao carregar ({e})", fg="#a00")
            self.subs = None

        # Atualiza painel SRT com a seleção atual (se houver)
        self._srt_sync_from_current_selection()

    def _load_stickman(self):
        base = os.path.join(self.root_dir.get(), self.current_batch)
        self.stickman_path = os.path.join(base, "stickman.json")

        if os.path.exists(self.stickman_path):
            self.stickman_data = _safe_json_load(self.stickman_path, [])
        else:
            self.stickman_data = []

    def _build_srt_phrases(self):
        self.srt_phrases = []

        if not self.subs:
            return

        for sub in self.subs:
            text = sub.text.strip().replace("\n", " ")
            if not text:
                continue

            entry = {
                "text": text,
                "sub": sub,
                "stickman": None
            }

            for sm in self.stickman_data:
                if trigger_in_text(sm["trigger"], text):
                    entry["stickman"] = sm
                    break

            self.srt_phrases.append(entry)

    def _refresh_stickman_list(self):
        self.stickman_list.delete(0, tk.END)

        for p in self.srt_phrases:
            prefix = "[x] " if p["stickman"] else "[ ] "
            label = prefix + p["text"][:60]
            self.stickman_list.insert(tk.END, label)

    def _on_stickman_phrase(self, event):
        sel = self.stickman_list.curselection()
        if not sel:
            return

        p = self.srt_phrases[sel[0]]
        self.current_phrase = p

        self.sm_trigger_entry.delete(0, tk.END)
        self.sm_trigger_entry.insert(0, p["stickman"]["trigger"] if p["stickman"] else p["text"])

        self.sm_expression.set(
            p["stickman"].get("expression", "neutral") if p["stickman"] else "neutral"
        )

        self.sm_speech.delete(0, tk.END)
        if p["stickman"]:
            self.sm_speech.insert(0, p["stickman"].get("speech", ""))

    def _save_stickman_entry(self):
        if not self.current_phrase:
            return

        trigger = self.sm_trigger_entry.get().strip()
        if not trigger:
            return

        entry = {
            "trigger": trigger,
            "expression": self.sm_expression.get()
        }

        speech = self.sm_speech.get().strip()
        if speech:
            entry["speech"] = speech

        # remove antigo se existir
        self.stickman_data = [
            sm for sm in self.stickman_data
            if sm is not self.current_phrase["stickman"]
        ]

        self.stickman_data.append(entry)
        self.current_phrase["stickman"] = entry

        _safe_json_save(self.stickman_path, self.stickman_data)
        self._refresh_stickman_list()

    def _remove_stickman_entry(self):
        if not self.current_phrase or not self.current_phrase["stickman"]:
            return

        self.stickman_data.remove(self.current_phrase["stickman"])
        self.current_phrase["stickman"] = None

        _safe_json_save(self.stickman_path, self.stickman_data)
        self._refresh_stickman_list()

    def _get_item_image_ids(self, item):
        image_ids = item.get("image_ids")
        if isinstance(image_ids, list) and image_ids:
            return [str(i).strip() for i in image_ids if str(i).strip()]
        image_id = str(item.get("image_id", "")).strip()
        return [image_id] if image_id else []

    def _format_image_ids(self, item):
        image_ids = self._get_item_image_ids(item)
        return ",".join(image_ids)

    def _parse_image_ids_entry(self, entry_value: str):
        raw = [part.strip() for part in entry_value.split(",")]
        return [part for part in raw if part]

    def _normalize_mode(self, mode_value: str):
        mode_value = (mode_value or "").strip().lower().replace("_", "-")
        return mode_value if mode_value in GUIDE_MODES else GUIDE_MODES[1]

    def _sync_mode_fields(self):
        mode = self.mode_combo.get()
        if mode == "text-only":
            self.image_id_entry.config(state="disabled")
            self.text_anchor_combo.config(state="disabled")
            self.text_margin_entry.config(state="disabled")
        else:
            self.image_id_entry.config(state="normal")
            self.text_anchor_combo.config(state="readonly")
            self.text_margin_entry.config(state="normal")

    # ---------------- TRIGGER LIST ----------------

    def _refresh_trigger_list(self, restore_view=False):
        yview = self.trigger_listbox.yview() if restore_view else None
        self.trigger_listbox.delete(0, tk.END)
        for i, item in enumerate(self.guide_data):
            trigger = item.get("trigger", "")
            mode = self._normalize_mode(item.get("mode", "image-only"))
            layout = item.get("layout", "legacy_single")
            stickman_position = item.get("stickman_position", "left")
            image_label = self._format_image_ids(item)
            self.trigger_listbox.insert(
                tk.END, f"{i+1}. {trigger} | {mode} | {layout} | {stickman_position} → {image_label}"
            )
        if yview is not None:
            self.trigger_listbox.yview_moveto(yview[0])

    def _on_trigger_selected(self, event):
        if self._autosave_after_id:
            self.after_cancel(self._autosave_after_id)
            self._autosave_after_id = None

        prev_index = self._last_selected_index
        sel = self.trigger_listbox.curselection()
        if not sel:
            self.selection_label.config(text="Nenhum item selecionado. Selecione um trigger à esquerda.")
            self.preview_label.config(image='', text="Selecione um item para ver a imagem")
            self.current_photo = None
            self._srt_clear_boxes()
            self._last_selected_index = None
            return

        # Atualizar label de seleção
        if len(sel) == 1:
            if prev_index is not None and prev_index != sel[0]:
                self._apply_changes(show_messages=False, autosave=True, target_index=prev_index)
            self.selection_label.config(text="Editar item selecionado:")
            idx = sel[0]
            item = self.guide_data[idx]

            # Preencher campos com dados do item único
            self.trigger_entry.delete(0, tk.END)
            self.trigger_entry.insert(0, item.get("trigger", ""))

            self.image_id_entry.delete(0, tk.END)
            self.image_id_entry.insert(0, self._format_image_ids(item))

            self.mode_combo.set(self._normalize_mode(item.get("mode", GUIDE_MODES[1])))
            self.layout_combo.set(item.get("layout", "legacy_single"))
            self.stickman_position_combo.set(item.get("stickman_position", "left"))
            self.mode_combo.config(state="readonly")
            self.layout_combo.config(state="readonly")
            self.stickman_position_combo.config(state="readonly")
            self.stickman_anim_combo.config(state="readonly")
            self.stickman_anim_dir_combo.config(state="readonly")

            self.text_entry.delete(0, tk.END)
            self.text_entry.insert(0, item.get("text", ""))

            self.text_anchor_combo.set((item.get("text_anchor") or "").strip())
            self.text_margin_entry.delete(0, tk.END)
            text_margin = item.get("text_margin")
            if text_margin is not None:
                self.text_margin_entry.insert(0, str(text_margin))

            # Effects
            effects = item.get("effects", {})
            self.zoom_var.set(effects.get("zoom", False))
            self.slide_var.set(effects.get("slide", "none") or "none")

            stickman_anim = item.get("stickman_anim") or {}
            anim_name = stickman_anim.get("name", "") if isinstance(stickman_anim, dict) else ""
            anim_dir = stickman_anim.get("direction", "") if isinstance(stickman_anim, dict) else ""
            self.stickman_anim_combo.set(anim_name)
            self.stickman_anim_dir_combo.set(anim_dir)

            self._sync_mode_fields()

            # Preview
            image_ids = self._get_item_image_ids(item)
            self._update_preview(image_ids[0] if image_ids else "")

            # Sync SRT tab
            self._srt_sync_from_current_selection()
            self._last_selected_index = idx
        else:
            # Múltipla seleção
            self.selection_label.config(text=f"{len(sel)} itens selecionados (edição em lote)")

            # Limpar campos individuais
            self.trigger_entry.delete(0, tk.END)
            self.trigger_entry.insert(0, "[múltiplos valores]")
            self.trigger_entry.config(state="disabled")

            self.image_id_entry.delete(0, tk.END)
            self.image_id_entry.insert(0, "[múltiplos valores]")
            self.image_id_entry.config(state="disabled")

            self.mode_combo.set(GUIDE_MODES[1])
            self.layout_combo.set("legacy_single")
            self.stickman_position_combo.set("left")
            self.stickman_anim_combo.set("")
            self.stickman_anim_dir_combo.set("")
            self.mode_combo.config(state="disabled")
            self.layout_combo.config(state="disabled")
            self.stickman_position_combo.config(state="disabled")
            self.stickman_anim_combo.config(state="disabled")
            self.stickman_anim_dir_combo.config(state="disabled")

            self.text_entry.delete(0, tk.END)
            self.text_entry.insert(0, "[múltiplos valores]")
            self.text_entry.config(state="disabled")

            self.text_anchor_combo.set(TEXT_ANCHOR_OPTIONS[0])
            self.text_anchor_combo.config(state="disabled")
            self.text_margin_entry.delete(0, tk.END)
            self.text_margin_entry.insert(0, "[múltiplos valores]")
            self.text_margin_entry.config(state="disabled")

            # Effects mantém habilitado para edição em lote
            zoom_states = [self.guide_data[i].get("effects", {}).get("zoom", False) for i in sel]
            if all(zoom_states):
                self.zoom_var.set(True)
            elif not any(zoom_states):
                self.zoom_var.set(False)
            else:
                self.zoom_var.set(False)

            self.slide_var.set("none")

            # Preview mostra primeira imagem
            first_item = self.guide_data[sel[0]]
            image_ids = self._get_item_image_ids(first_item)
            self._update_preview(image_ids[0] if image_ids else "")

            # SRT tab: não tenta editar (múltiplo)
            self._srt_clear_boxes()
            self._srt_set_text(self.srt_orig_box, "Seleção múltipla: SRT editor funciona apenas com 1 trigger.")
            self._srt_set_text(self.srt_preview_box, "")

            # Habilitar campos novamente para próxima seleção única
            self.after(100, lambda: self.trigger_entry.config(state="normal"))
            self.after(100, lambda: self.image_id_entry.config(state="normal"))
            self.after(100, lambda: self.text_entry.config(state="normal"))
            self.after(100, lambda: self.text_anchor_combo.config(state="readonly"))
            self.after(100, lambda: self.text_margin_entry.config(state="normal"))
            self.after(100, lambda: self.mode_combo.config(state="readonly"))
            self.after(100, lambda: self.layout_combo.config(state="readonly"))
            self.after(100, lambda: self.stickman_position_combo.config(state="readonly"))
            self.after(100, lambda: self.stickman_anim_combo.config(state="readonly"))
            self.after(100, lambda: self.stickman_anim_dir_combo.config(state="readonly"))
            self._last_selected_index = None

    # ---------------- IMAGE PREVIEW ----------------

    def _update_preview(self, image_id):
        """Carrega e exibe preview da imagem com PIL"""
        if not self.current_batch or not image_id:
            sel = self.trigger_listbox.curselection()
            if sel and len(sel) == 1:
                item = self.guide_data[sel[0]]
                mode = self._normalize_mode(item.get("mode", GUIDE_MODES[1]))
                if mode == "text-only":
                    self.preview_label.config(image='', text="Modo text-only (sem imagem)")
                    self.current_photo = None
                    return
            self.preview_label.config(image='', text="Selecione um item para ver a imagem")
            self.current_photo = None
            return

        images_dir = os.path.join(self.root_dir.get(), self.current_batch, "imagens")

        if not os.path.isdir(images_dir):
            self.preview_label.config(image='', text=f"Pasta imagens/ não encontrada")
            self.current_photo = None
            return

        # Procurar arquivo de imagem
        image_path = None
        valid_exts = ('.jpg', '.jpeg', '.png', '.gif')

        try:
            for f in os.listdir(images_dir):
                if f.lower().endswith(valid_exts):
                    if f.startswith(f"{image_id}_"):
                        image_path = os.path.join(images_dir, f)
                        break
        except Exception as e:
            self.preview_label.config(image='', text=f"Erro ao acessar imagens/:\n{e}")
            self.current_photo = None
            return

        if not image_path:
            self.preview_label.config(image='', text=f"Imagem '{image_id}' não encontrada")
            self.current_photo = None
            return

        # Carregar e redimensionar imagem
        try:
            img = Image.open(image_path)

            max_width = 390
            max_height = 150
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            self.current_photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.current_photo, text='')

        except Exception as e:
            self.preview_label.config(image='', text=f"Erro ao carregar imagem:\n{str(e)[:50]}")
            self.current_photo = None

    # ---------------- GUIDE EDIT (original logic) ----------------

    def _apply_changes(self, show_messages=True, autosave=False, target_index=None):
        selected_indices = list(self.trigger_listbox.curselection())
        if target_index is None:
            if not selected_indices:
                if show_messages:
                    messagebox.showwarning("Aviso", "Selecione um trigger para editar")
                return False

            if len(selected_indices) > 1:
                if show_messages:
                    messagebox.showinfo("Info", "Para edição em lote, use 'Aplicar effects em lote'")
                return False

            idx = selected_indices[0]
        else:
            if target_index < 0 or target_index >= len(self.guide_data):
                return False
            idx = target_index

        self.guide_data[idx]["trigger"] = self.trigger_entry.get()
        mode = self._normalize_mode(self.mode_combo.get())
        self.guide_data[idx]["mode"] = mode

        layout = self.layout_combo.get() or "legacy_single"
        self.guide_data[idx]["layout"] = layout

        stickman_position = (self.stickman_position_combo.get() or "").strip().lower()
        if stickman_position in STICKMAN_POSITION_OPTIONS:
            self.guide_data[idx]["stickman_position"] = stickman_position
        else:
            self.guide_data[idx].pop("stickman_position", None)

        image_ids = self._parse_image_ids_entry(self.image_id_entry.get())
        if mode == "text-only":
            self.guide_data[idx].pop("image_id", None)
            self.guide_data[idx].pop("image_ids", None)
        else:
            if not image_ids:
                if show_messages:
                    messagebox.showwarning("Aviso", "Informe pelo menos um Image ID para este modo.")
                return False
            if len(image_ids) == 1:
                self.guide_data[idx]["image_id"] = image_ids[0]
                self.guide_data[idx].pop("image_ids", None)
            else:
                self.guide_data[idx]["image_ids"] = image_ids
                self.guide_data[idx].pop("image_id", None)

        text = self.text_entry.get()
        if text:
            self.guide_data[idx]["text"] = text
        elif "text" in self.guide_data[idx]:
            del self.guide_data[idx]["text"]

        if mode == "text-only":
            self.guide_data[idx].pop("text_anchor", None)
            self.guide_data[idx].pop("text_margin", None)
        else:
            anchor_value = self.text_anchor_combo.get().strip().lower()
            if anchor_value:
                self.guide_data[idx]["text_anchor"] = anchor_value
            else:
                self.guide_data[idx].pop("text_anchor", None)

            margin_value = self.text_margin_entry.get().strip()
            if margin_value:
                try:
                    self.guide_data[idx]["text_margin"] = int(margin_value)
                except ValueError:
                    if show_messages:
                        messagebox.showwarning("Aviso", "Margem inválida. Use um número inteiro.")
                    return False
            else:
                self.guide_data[idx].pop("text_margin", None)

        effects = {}
        if self.zoom_var.get():
            effects["zoom"] = True

        slide = self.slide_var.get()
        if slide and slide != "none":
            effects["slide"] = slide

        if effects:
            self.guide_data[idx]["effects"] = effects
        elif "effects" in self.guide_data[idx]:
            del self.guide_data[idx]["effects"]

        anim_name = self.stickman_anim_combo.get().strip()
        anim_direction = self.stickman_anim_dir_combo.get().strip()
        if anim_name:
            stickman_anim = {"name": anim_name}
            if anim_direction:
                stickman_anim["direction"] = anim_direction
            self.guide_data[idx]["stickman_anim"] = stickman_anim
        else:
            self.guide_data[idx].pop("stickman_anim", None)

        self._refresh_trigger_list(restore_view=True)
        if selected_indices:
            for selected in selected_indices:
                self.trigger_listbox.selection_set(selected)
            active_index = selected_indices[-1]
        else:
            self.trigger_listbox.selection_set(idx)
            active_index = idx

        self.trigger_listbox.activate(active_index)
        self.trigger_listbox.selection_anchor(active_index)

        if autosave:
            self._save_guide(show_messages=False)

        if show_messages:
            messagebox.showinfo("Sucesso", "Alterações aplicadas")

        # Atualiza SRT tab também (trigger mudou)
        self._srt_sync_from_current_selection()
        return True

    def _apply_batch_effects(self):
        sel = self.trigger_listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um ou mais triggers")
            return

        if len(sel) == 1:
            messagebox.showinfo("Info", "Para item único, use 'Aplicar alterações'")
            return

        if not messagebox.askyesno(
            "Confirmar",
            f"Aplicar effects em {len(sel)} itens selecionados?\n\n"
            f"Zoom: {'SIM' if self.zoom_var.get() else 'NÃO'}\n"
            f"Slide: {self.slide_var.get().upper()}"
        ):
            return

        anchor_value = self.text_anchor_combo.get().strip().lower()
        margin_value = self.text_margin_entry.get().strip()
        margin_int = None
        if margin_value:
            try:
                margin_int = int(margin_value)
            except ValueError:
                messagebox.showwarning("Aviso", "Margem inválida. Use um número inteiro.")
                return

        for idx in sel:
            effects = {}

            if self.zoom_var.get():
                effects["zoom"] = True

            slide = self.slide_var.get()
            if slide and slide != "none":
                effects["slide"] = slide

            if effects:
                if "effects" not in self.guide_data[idx]:
                    self.guide_data[idx]["effects"] = {}
                self.guide_data[idx]["effects"].update(effects)
            else:
                if "effects" in self.guide_data[idx]:
                    del self.guide_data[idx]["effects"]

            mode = self._normalize_mode(self.guide_data[idx].get("mode", GUIDE_MODES[1]))
            if mode != "text-only":
                if anchor_value:
                    self.guide_data[idx]["text_anchor"] = anchor_value
                else:
                    self.guide_data[idx].pop("text_anchor", None)

                if margin_value:
                    self.guide_data[idx]["text_margin"] = margin_int
                else:
                    self.guide_data[idx].pop("text_margin", None)

        self._refresh_trigger_list()
        for idx in sel:
            self.trigger_listbox.selection_set(idx)

        self._save_guide(show_messages=False)
        messagebox.showinfo("Sucesso", f"Effects aplicados em {len(sel)} itens")

    def _disable_batch_zoom(self):
        if not self.current_batch or not self.guide_data:
            messagebox.showwarning("Aviso", "Batch não carregado.")
            return

        if not messagebox.askyesno(
            "Confirmar",
            "Desabilitar o zoom de todas as triggers deste batch?\n\n"
            "Isso remove o efeito de zoom de todos os itens."
        ):
            return

        for item in self.guide_data:
            effects = item.get("effects")
            if not effects or "zoom" not in effects:
                continue
            del effects["zoom"]
            if not effects:
                item.pop("effects", None)

        self.zoom_var.set(False)
        self._refresh_trigger_list()
        self._srt_sync_from_current_selection()
        self._save_guide(show_messages=False)
        messagebox.showinfo("Sucesso", "Zoom desabilitado em todo o batch")

    def _add_new_trigger(self):
        new_item = {
            "trigger": "novo trigger",
            "mode": "image-only",
            "layout": "legacy_single",
            "stickman_position": "left",
            "image_id": "01"
        }

        self.guide_data.append(new_item)
        self._refresh_trigger_list()
        self._save_guide(show_messages=False)

        self.trigger_listbox.selection_clear(0, tk.END)
        self.trigger_listbox.selection_set(tk.END)
        self.trigger_listbox.see(tk.END)
        self._on_trigger_selected(None)

    def _remove_trigger(self):
        sel = self.trigger_listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um ou mais triggers para remover")
            return

        if len(sel) == 1:
            idx = sel[0]
            trigger = self.guide_data[idx].get("trigger", "")

            if messagebox.askyesno("Confirmar", f"Remover trigger '{trigger}'?"):
                del self.guide_data[idx]
                self._refresh_trigger_list()
                if self._last_selected_index == idx:
                    self._last_selected_index = None
                self._save_guide(show_messages=False)
                messagebox.showinfo("Sucesso", "Trigger removido")
        else:
            if messagebox.askyesno("Confirmar", f"Remover {len(sel)} triggers selecionados?"):
                for idx in reversed(sorted(sel)):
                    del self.guide_data[idx]
                self._refresh_trigger_list()
                if self._last_selected_index is not None:
                    self._last_selected_index = None
                self._save_guide(show_messages=False)
                messagebox.showinfo("Sucesso", f"{len(sel)} triggers removidos")

    def _set_guide_status(self, text: str, color: str):
        self.guide_status.set(text)
        if hasattr(self, "guide_status_label"):
            self.guide_status_label.config(fg=color)

    def _save_guide(self, show_messages=True):
        if not self.guide_path:
            if show_messages:
                messagebox.showwarning("Aviso", "Nenhum guia carregado")
            self._set_guide_status("guia.json: não carregado", "#666")
            return

        try:
            with open(self.guide_path, 'w', encoding='utf-8') as f:
                json.dump(self.guide_data, f, indent=2, ensure_ascii=False)

            self._set_guide_status("guia.json atualizado", "#2e7d32")
            if show_messages:
                messagebox.showinfo("Sucesso", f"guia.json salvo em:\n{self.guide_path}")
        except Exception as e:
            if show_messages:
                messagebox.showerror("Erro", f"Erro ao salvar:\n{e}")
            self._set_guide_status("guia.json: erro ao salvar", "#a00")

    def _reload_guide(self):
        if self.guide_path:
            self._load_guide()
            messagebox.showinfo("Recarregado", "guia.json recarregado do disco")
            self._srt_sync_from_current_selection()

    # ---------------- SRT UI helpers ----------------

    def _srt_set_text(self, widget: tk.Text, text: str):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.config(state="disabled")

    def _srt_clear_boxes(self):
        self._srt_set_text(self.srt_orig_box, "")
        self._srt_set_text(self.srt_preview_box, "")
        if hasattr(self, "srt_trigger_entry"):
            self.srt_trigger_entry.delete(0, "end")
        self._current_sub = None
        self._preview_segments = None

    def _srt_sync_from_current_selection(self):
        """Atualiza painel SRT com base no trigger selecionado no guia."""
        if not PYSRT_OK:
            return
        if not self.subs:
            self._srt_clear_boxes()
            return

        sel = self.trigger_listbox.curselection()
        if not sel or len(sel) != 1:
            return

        idx = sel[0]
        trigger = (self.guide_data[idx].get("trigger", "") or "").strip()

        # preenche trigger no campo da aba SRT como default
        self.srt_trigger_entry.delete(0, "end")
        self.srt_trigger_entry.insert(0, trigger)

        # encontra a legenda correspondente (mesma lógica do main: primeiro match)
        sub = self._find_sub_for_trigger(trigger)
        self._current_sub = sub
        self._preview_segments = None

        if not sub:
            self._srt_set_text(self.srt_orig_box, "Trigger não encontrado no SRT.")
            self._srt_set_text(self.srt_preview_box, "")
            return

        # mostra original
        t0 = _srt_time_to_sec(sub.start)
        t1 = _srt_time_to_sec(sub.end)
        header = f"#{sub.index}  {_fmt_sec(t0)} → {_fmt_sec(t1)}\n"
        body = sub.text.strip()
        self._srt_set_text(self.srt_orig_box, header + body)

        # se já existe edit pra esse index, mostra também no preview box
        existing = self._srt_get_edit_for_index(sub.index)
        if existing:
            lines = ["(Já existe edição em srt_edit.json)"]
            for seg in existing.get("segments", []):
                lines.append(f"{_fmt_sec(seg['start'])} → {_fmt_sec(seg['end'])}  |  {seg['text']}")
            self._srt_set_text(self.srt_preview_box, "\n".join(lines))
        else:
            self._srt_set_text(self.srt_preview_box, "")

    def _find_sub_for_trigger(self, trigger: str):
        # Igual ao main.py em espírito: primeiro sub que contém o trigger
        if not self.subs:
            return None
        trig = trigger.strip()
        if not trig:
            return None

        for sub in self.subs:
            if trig.lower() in (sub.text or "").lower():
                return sub
        return None

    def _srt_get_edit_for_index(self, index: int):
        for e in self.srt_edits:
            if int(e.get("index", -1)) == int(index):
                return e
        return None

    # ---------------- SRT actions ----------------

    def _srt_recalc_preview(self):
        if not PYSRT_OK:
            return
        if not self._current_sub:
            messagebox.showwarning("Aviso", "Nenhuma legenda selecionada (selecione 1 trigger no Guia).")
            return

        trigger = self.srt_trigger_entry.get().strip()
        if not trigger:
            messagebox.showwarning("Aviso", "Digite um trigger para split.")
            return

        full_text = (self._current_sub.text or "").strip()
        if not full_text:
            messagebox.showwarning("Aviso", "Legenda vazia.")
            return

        parts = _split_text_by_trigger(full_text, trigger)
        if not parts:
            messagebox.showwarning("Aviso", "Esse trigger não foi encontrado dentro da legenda (case-insensitive).")
            return

        before, second = parts
        if not before or not second:
            messagebox.showwarning("Aviso", "Split inválido (uma das partes ficou vazia).")
            return

        t0 = _srt_time_to_sec(self._current_sub.start)
        t1 = _srt_time_to_sec(self._current_sub.end)
        split_t = _proportional_split_times(t0, t1, full_text, before)

        self._preview_segments = [
            {"start": float(t0), "end": float(split_t), "text": before},
            {"start": float(split_t), "end": float(t1), "text": second},
        ]

        lines = []
        for seg in self._preview_segments:
            lines.append(f"{_fmt_sec(seg['start'])} → {_fmt_sec(seg['end'])}  |  {seg['text']}")
        self._srt_set_text(self.srt_preview_box, "\n".join(lines))

    def _srt_apply_edit(self):
        if not PYSRT_OK:
            return
        if not self._current_sub:
            messagebox.showwarning("Aviso", "Nenhuma legenda selecionada (selecione 1 trigger no Guia).")
            return
        if not self._preview_segments:
            messagebox.showwarning("Aviso", "Clique em 'Recalcular preview' antes de aplicar.")
            return

        sub = self._current_sub
        t0 = _srt_time_to_sec(sub.start)
        t1 = _srt_time_to_sec(sub.end)

        entry = {
            "index": int(sub.index),
            "original": {
                "start": float(t0),
                "end": float(t1),
                "text": (sub.text or "").strip()
            },
            "segments": self._preview_segments,
            "note": "split_by_trigger",
            "trigger_used": self.srt_trigger_entry.get().strip()
        }

        # substitui se já existe
        self.srt_edits = [e for e in self.srt_edits if int(e.get("index", -1)) != int(sub.index)]
        self.srt_edits.append(entry)

        self.srt_status.config(
            text=f"SRT: {os.path.basename(self.srt_path)} | edits: {len(self.srt_edits)} (não salvo)",
            fg="#333"
        )
        messagebox.showinfo("OK", "Aplicado em memória. Clique em 'Salvar srt_edit.json' para persistir.")

    def _srt_save_file(self):
        if not PYSRT_OK:
            return
        if not self.srt_edit_path:
            messagebox.showwarning("Aviso", "Batch/SRT não carregado.")
            return

        try:
            _safe_json_save(self.srt_edit_path, self.srt_edits)
            self.srt_status.config(
                text=f"SRT: {os.path.basename(self.srt_path)} | edits: {len(self.srt_edits)} (salvo)",
                fg="#333"
            )
            messagebox.showinfo("Salvo", f"{SRT_EDIT_FILENAME} salvo em:\n{self.srt_edit_path}")
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao salvar {SRT_EDIT_FILENAME}:\n{e}")

    def _srt_revert_current(self):
        if not PYSRT_OK:
            return
        if not self._current_sub:
            messagebox.showwarning("Aviso", "Nenhuma legenda selecionada.")
            return

        idx = int(self._current_sub.index)
        existing = self._srt_get_edit_for_index(idx)
        if not existing:
            messagebox.showinfo("Info", "Não há edição salva para esta legenda.")
            return

        if not messagebox.askyesno("Confirmar", f"Remover edição do índice #{idx} do {SRT_EDIT_FILENAME}?"):
            return

        self.srt_edits = [e for e in self.srt_edits if int(e.get("index", -1)) != idx]
        self._preview_segments = None

        self.srt_status.config(
            text=f"SRT: {os.path.basename(self.srt_path)} | edits: {len(self.srt_edits)} (não salvo)",
            fg="#333"
        )
        self._srt_set_text(self.srt_preview_box, "")
        messagebox.showinfo("OK", "Edição removida em memória. Salve para persistir.")

class BaseToolLauncher(tk.Frame):
    TOOL_NAME = "Unnamed Tool"
    TOOL_DESC = ""

    def __init__(self, parent, root_dir_var):
        super().__init__(parent, bg="#c0c0c0")
        self.root_dir = root_dir_var
        self._build()

    def _build(self):
        tk.Label(
            self,
            text=self.TOOL_NAME,
            font=("Arial", 13, "bold"),
            bg="#c0c0c0"
        ).pack(anchor="w", padx=12, pady=(12, 6))

        if self.TOOL_DESC:
            tk.Label(
                self,
                text=self.TOOL_DESC,
                fg="#555",
                bg="#c0c0c0",
                wraplength=520,
                justify="left"
            ).pack(anchor="w", padx=12)

        tk.Button(
            self,
            text="Abrir",
            width=22,
            height=2,
            command=self.open
        ).pack(anchor="w", padx=12, pady=18)

    def open(self):
        raise NotImplementedError

class FileOrganizerTool(BaseToolLauncher):
    TOOL_NAME = "Organizador de Arquivos"
    TOOL_DESC = (
        "Renomeia arquivos do batch automaticamente "
        "(stickman.json, guia.json, search_terms.txt, audio.srt)."
    )

    def open(self):
        FileOrganizerWindow(self)  # abre a sua janela Toplevel existente

class ImageDownloaderTool(BaseToolLauncher):
    TOOL_NAME = "Downloader de Imagens"
    TOOL_DESC = (
        "Baixa imagens do Google a partir de um search_terms.txt.\n"
        "Suporta múltiplos termos, safe-stop, resume e numeração XX_XX_termo."
    )

    def open(self):
        ImageDownloaderWindow(self)


# ---------------- ABA TOOLS ----------------
class ToolsTab(tk.Frame):
    """Aba de ferramentas diversas"""

    def __init__(self, parent, root_dir_var):
        super().__init__(parent, bg="#c0c0c0")
        self.root_dir = root_dir_var
        self._build_ui()

    def _build_ui(self):
    # Layout base
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

    # Sidebar (esquerda)
        sidebar = tk.Frame(self, bg="#d0d0d0", bd=2, relief="groove")
        sidebar.grid(row=0, column=0, sticky="ns", padx=10, pady=10)

        tk.Label(
            sidebar,
            text="Ferramentas",
            bg="#d0d0d0",
            font=("Arial", 12, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 6))

        self.tools_list = tk.Listbox(sidebar, height=26, width=22, exportselection=False)
        self.tools_list.pack(padx=10, pady=(0, 10))

        # Área da ferramenta (direita)
        self.tool_area = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        self.tool_area.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)

        # Registro de ferramentas (é aqui que você adiciona novas no futuro)
        self.tools_registry = {
            "Organizador": FileOrganizerTool,
            "Downloader de Imagens": ImageDownloaderTool, 
            # "Validador": BatchValidatorTool,
            # "Limpar output": OutputCleanerTool,
        }

        for name in self.tools_registry.keys():
            self.tools_list.insert(tk.END, name)

        self.tools_list.bind("<<ListboxSelect>>", self._on_tool_selected)

        # Placeholder inicial
        self._show_placeholder()
    
    def _show_placeholder(self):
        for w in self.tool_area.winfo_children():
            w.destroy()

        tk.Label(
            self.tool_area,
            text="Selecione uma ferramenta à esquerda",
            bg="#c0c0c0",
            fg="#666",
            font=("Arial", 11)
        ).pack(expand=True)

    def _on_tool_selected(self, event):
        sel = self.tools_list.curselection()
        if not sel:
            return

        tool_name = self.tools_list.get(sel[0])
        tool_cls = self.tools_registry[tool_name]

        # Limpa área direita
        for w in self.tool_area.winfo_children():
            w.destroy()

        # Monta o launcher (que terá o botão Abrir -> Toplevel)
        panel = tool_cls(self.tool_area, self.root_dir)
        panel.pack(fill="both", expand=True)


# ---------------- JANELA DO ORGANIZADOR ----------------
class FileOrganizerWindow(tk.Toplevel):
    """Janela para organizar arquivos de batch"""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Organizador de Arquivos")
        self.geometry("700x550")
        self.configure(bg="#c0c0c0")
        self.resizable(False, False)

        # Tentar usar ícone do app principal
        try:
            if os.path.exists(ICON_FILE):
                self.iconbitmap(ICON_FILE)
        except Exception:
            pass

        self.pasta_var = tk.StringVar()
        self.arquivo_pendente = None
        self.arquivo_pendente_config = None

        self._build_ui()

    def _build_ui(self):
        # Frame de seleção de pasta
        select_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        select_frame.place(x=10, y=10, width=680, height=80)

        tk.Label(
            select_frame,
            text="Pasta do batch:",
            bg="#c0c0c0",
            font=("Arial", 10, "bold")
        ).place(x=10, y=12)

        tk.Entry(
            select_frame,
            textvariable=self.pasta_var,
            width=60
        ).place(x=10, y=40)

        tk.Button(
            select_frame,
            text="Procurar...",
            width=15,
            command=self._browse_folder
        ).place(x=550, y=36)

        # Frame de log
        log_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        log_frame.place(x=10, y=100, width=680, height=350)

        tk.Label(
            log_frame,
            text="Log de execução:",
            bg="#c0c0c0",
            font=("Arial", 10, "bold")
        ).place(x=10, y=10)

        self.log_text = tk.Text(
            log_frame,
            bg="black",
            fg="#00ff00",
            insertbackground="white",
            state="disabled",
            font=("Courier New", 9),
            wrap="word"
        )
        self.log_text.place(x=10, y=40, width=650, height=290)

        log_scroll = tk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.place(x=660, y=40, width=18, height=290)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        # Frame de ações
        action_frame = tk.Frame(self, bg="#c0c0c0")
        action_frame.place(x=10, y=460, width=680, height=80)

        self.btn_iniciar = tk.Button(
            action_frame,
            text="Iniciar Organização",
            width=20,
            height=2,
            command=self._iniciar_organizacao,
            bg="#2196F3",
            fg="white",
            font=("Arial", 10, "bold"),
            activebackground="#1976D2"
        )
        self.btn_iniciar.place(x=20, y=10)

        self.status_label = tk.Label(
            action_frame,
            text="Aguardando...",
            bg="#c0c0c0",
            font=("Arial", 10)
        )
        self.status_label.place(x=220, y=25)

        tk.Button(
            action_frame,
            text="Fechar",
            width=15,
            command=self.destroy
        ).place(x=560, y=20)

    def _browse_folder(self):
        """Permite escolher pasta"""
        path = filedialog.askdirectory()
        if path:
            self.pasta_var.set(path)

    def _log(self, msg):
        """Adiciona mensagem ao log"""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.update_idletasks()

    def _escolher_arquivo_manual(self, extensao, destino):
        """Callback para escolha manual de arquivo"""
        self.arquivo_pendente = None
        self.arquivo_pendente_config = (extensao, destino)

        # Variável para controlar a escolha
        escolha_feita = {"path": None}

        # Criar diálogo
        dialog = tk.Toplevel(self)
        dialog.title("Arquivo não encontrado")
        dialog.geometry("500x200")
        dialog.configure(bg="#c0c0c0")
        dialog.transient(self)
        dialog.grab_set()
        
        # Centralizar diálogo
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        tk.Label(
            dialog,
            text=f"Arquivo não encontrado: {destino}",
            bg="#c0c0c0",
            font=("Arial", 11, "bold")
        ).pack(pady=20)

        tk.Label(
            dialog,
            text=f"Deseja escolher um arquivo .{extensao} manualmente?",
            bg="#c0c0c0"
        ).pack(pady=10)

        btn_frame = tk.Frame(dialog, bg="#c0c0c0")
        btn_frame.pack(pady=20)

        def escolher():
            # Temporariamente liberar o grab para permitir filedialog
            dialog.grab_release()
            
            path = filedialog.askopenfilename(
                parent=dialog,
                title=f"Escolher {destino}",
                filetypes=[(f"{extensao.upper()} files", f"*.{extensao}"), ("Todos", "*.*")]
            )
            
            if path:
                escolha_feita["path"] = path
                self._log(f"  → Arquivo escolhido: {os.path.basename(path)}")
            
            dialog.destroy()

        def pular():
            self._log(f"  → Pulado")
            dialog.destroy()

        tk.Button(
            btn_frame,
            text="Escolher arquivo",
            width=15,
            command=escolher,
            bg="#4CAF50",
            fg="white"
        ).pack(side="left", padx=10)

        tk.Button(
            btn_frame,
            text="Pular",
            width=15,
            command=pular
        ).pack(side="left", padx=10)

        # Aguardar fechamento do diálogo
        self.wait_window(dialog)

        return escolha_feita["path"]

    def _iniciar_organizacao(self):
        """Inicia processo de organização"""
        pasta = self.pasta_var.get().strip()

        if not pasta:
            messagebox.showwarning("Aviso", "Selecione uma pasta primeiro.")
            return

        if not os.path.isdir(pasta):
            messagebox.showerror("Erro", "Pasta inválida.")
            return

        # Limpar log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.status_label.config(text="Processando...", fg="blue")
        self.btn_iniciar.config(state="disabled")
        self.update_idletasks()

        # Importar função (assumindo que file_organizer.py está no mesmo diretório)
        try:
            import sys
            script_dir = os.path.dirname(os.path.abspath(__file__))
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)

            from file_organizer import renomear_arquivos

            # Executar
            sucesso, msg = renomear_arquivos(
                pasta,
                callback_log=self._log,
                callback_escolha=self._escolher_arquivo_manual
            )

            # Atualizar status
            if sucesso:
                self.status_label.config(text="✓ Concluído com sucesso!", fg="green")
                messagebox.showinfo("Sucesso", msg)
            else:
                self.status_label.config(text="⚠ Concluído com avisos", fg="orange")
                messagebox.showwarning("Atenção", msg)

        except ImportError:
            self.status_label.config(text="✗ Erro: file_organizer.py não encontrado", fg="red")
            messagebox.showerror(
                "Erro",
                "Não foi possível importar file_organizer.py.\n"
                "Certifique-se de que o arquivo está no mesmo diretório que gui.py"
            )
        except Exception as e:
            self.status_label.config(text="✗ Erro durante execução", fg="red")
            messagebox.showerror("Erro", f"Erro durante execução:\n{e}")

        finally:
            self.btn_iniciar.config(state="normal")
            
 
class ImageDownloaderWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Downloader de Imagens")
        self.geometry("900x780")
        self.configure(bg="#c0c0c0")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.stop_requested = False
        self.worker = None

        # Lista de arquivos para processar
        self.files_list = []  # [{txt_path, topic_name}, ...]

        # ---------------- Vars ----------------
        self.dest_path = tk.StringVar()
        self.images_per_term = tk.IntVar(value=3)
        self.extra_tag_wikipedia_cc = tk.BooleanVar(value=False)
        self.extra_tag_freepik = tk.BooleanVar(value=False)

        self._build_ui()

    # ----------------------------------------------------
    # UI
    # ----------------------------------------------------
    def _build_ui(self):
        y = 10

        # === LISTA DE ARQUIVOS ===
        tk.Label(
            self, 
            text="Arquivos para processar:", 
            bg="#c0c0c0",
            font=("Arial", 10, "bold")
        ).place(x=10, y=y)
        y += 25

        # Frame com lista + scrollbar
        list_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="sunken")
        list_frame.place(x=10, y=y, width=860, height=200)

        # Listbox com 3 colunas: #, Arquivo, Tópico
        self.files_listbox = tk.Listbox(
            list_frame,
            height=10,
            font=("Courier New", 9),
            selectmode=tk.SINGLE
        )
        self.files_listbox.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        list_scroll = tk.Scrollbar(list_frame, command=self.files_listbox.yview)
        list_scroll.pack(side="right", fill="y")
        self.files_listbox.configure(yscrollcommand=list_scroll.set)

        y += 210

        # Botões de gerenciamento
        btn_frame = tk.Frame(self, bg="#c0c0c0")
        btn_frame.place(x=10, y=y, width=860, height=35)

        tk.Button(
            btn_frame,
            text="➕ Adicionar arquivo",
            width=18,
            command=self._add_file
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="✏️ Editar tópico",
            width=18,
            command=self._edit_topic
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="🗑️ Remover selecionado",
            width=18,
            command=self._remove_file
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="🧹 Limpar lista",
            width=18,
            command=self._clear_list
        ).pack(side="left", padx=5)

        y += 45

        # === CONFIGURAÇÕES GERAIS ===
        config_frame = tk.Frame(self, bg="#c0c0c0", bd=2, relief="groove")
        config_frame.place(x=10, y=y, width=860, height=115)

        # Destino
        tk.Label(config_frame, text="Pasta destino:", bg="#c0c0c0").place(x=10, y=10)
        tk.Entry(config_frame, textvariable=self.dest_path, width=80).place(x=10, y=32)
        tk.Button(config_frame, text="Procurar...", command=self._browse_dest).place(x=730, y=28)

        # Imagens por termo
        tk.Label(config_frame, text="Imagens por termo:", bg="#c0c0c0").place(x=10, y=60)
        tk.Spinbox(
            config_frame, 
            from_=1, to=100, 
            textvariable=self.images_per_term, 
            width=6
        ).place(x=140, y=60)

        # Tags extras
        tk.Label(config_frame, text="Tags extras:", bg="#c0c0c0").place(x=240, y=60)
        tk.Checkbutton(
            config_frame,
            text="Creative Commons (Wikipedia)",
            variable=self.extra_tag_wikipedia_cc,
            bg="#c0c0c0",
        ).place(x=320, y=58)
        tk.Checkbutton(
            config_frame,
            text="Freepik",
            variable=self.extra_tag_freepik,
            bg="#c0c0c0",
        ).place(x=560, y=58)

        y += 125

        # === PROGRESSO ===
        tk.Label(self, text="Progresso do arquivo atual:", bg="#c0c0c0").place(x=10, y=y)
        self.file_prog = ttk.Progressbar(self, length=860)
        self.file_prog.place(x=10, y=y+22)
        y += 50

        tk.Label(self, text="Progresso do termo atual:", bg="#c0c0c0").place(x=10, y=y)
        self.term_prog = ttk.Progressbar(self, length=860)
        self.term_prog.place(x=10, y=y+22)
        y += 50

        tk.Label(self, text="Progresso geral:", bg="#c0c0c0").place(x=10, y=y)
        self.total_prog = ttk.Progressbar(self, length=860)
        self.total_prog.place(x=10, y=y+22)
        y += 50

        # === LOG ===
        tk.Label(self, text="Log:", bg="#c0c0c0").place(x=10, y=y)
        self.log = tk.Text(self, bg="black", fg="#00ff00", height=8, state="disabled")
        self.log.place(x=10, y=y+22, width=860, height=140)
        y += 170

        # === BOTÕES DE CONTROLE ===
        self.btn_start = tk.Button(self, text="▶️ Start", width=15, command=self._start)
        self.btn_stop = tk.Button(self, text="⏹️ Stop", width=15, command=self._stop, state="disabled")
        self.btn_resume = tk.Button(self, text="⏯️ Resume", width=15, command=self._resume)

        self.btn_start.place(x=250, y=y)
        self.btn_stop.place(x=400, y=y)
        self.btn_resume.place(x=550, y=y)

    # ----------------------------------------------------
    # Gerenciamento de arquivos
    # ----------------------------------------------------
    def _add_file(self):
        """Adiciona um arquivo à lista"""
        path = filedialog.askopenfilename(
            title="Escolher search_terms.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        if not path:
            return

        # Verificar se já existe na lista
        for item in self.files_list:
            if item["txt_path"] == path:
                messagebox.showwarning("Aviso", "Este arquivo já está na lista.")
                return

        # Extrair tópico do arquivo (se existir)
        topic = self._extract_topic_from_file(path)
        
        if not topic:
            # Se não tem tópico no arquivo, pedir ao usuário
            topic = self._ask_topic_name(os.path.basename(path))
            if not topic:
                return

        # Adicionar à lista
        self.files_list.append({
            "txt_path": path,
            "topic_name": topic
        })

        self._refresh_files_list()

    def _extract_topic_from_file(self, path):
        """Tenta extrair tópico do arquivo"""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    m = TOPIC_RE.search(line)
                    if m:
                        return m.group(1).strip()
        except Exception:
            pass
        return None

    def _ask_topic_name(self, filename):
        """Diálogo para pedir nome do tópico"""
        dialog = tk.Toplevel(self)
        dialog.title("Nome do Tópico")
        dialog.geometry("400x150")
        dialog.configure(bg="#c0c0c0")
        dialog.transient(self)
        dialog.grab_set()

        # Centralizar
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        result = {"topic": None}

        tk.Label(
            dialog,
            text=f"Arquivo: {filename}",
            bg="#c0c0c0",
            font=("Arial", 10, "bold")
        ).pack(pady=10)

        tk.Label(dialog, text="Nome do tópico (pasta):", bg="#c0c0c0").pack()

        topic_entry = tk.Entry(dialog, width=40)
        topic_entry.pack(pady=5)
        topic_entry.focus()

        def confirm():
            topic = topic_entry.get().strip()
            if topic:
                result["topic"] = topic
                dialog.destroy()
            else:
                messagebox.showwarning("Aviso", "Digite um nome para o tópico.")

        def cancel():
            dialog.destroy()

        # Bind Enter
        topic_entry.bind("<Return>", lambda e: confirm())

        btn_frame = tk.Frame(dialog, bg="#c0c0c0")
        btn_frame.pack(pady=15)

        tk.Button(btn_frame, text="OK", width=12, command=confirm).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Cancelar", width=12, command=cancel).pack(side="left", padx=5)

        self.wait_window(dialog)
        return result["topic"]

    def _edit_topic(self):
        """Edita o tópico do arquivo selecionado"""
        sel = self.files_listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um arquivo para editar.")
            return

        idx = sel[0]
        item = self.files_list[idx]

        new_topic = self._ask_topic_name(os.path.basename(item["txt_path"]))
        if new_topic:
            item["topic_name"] = new_topic
            self._refresh_files_list()

    def _remove_file(self):
        """Remove arquivo selecionado"""
        sel = self.files_listbox.curselection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um arquivo para remover.")
            return

        idx = sel[0]
        del self.files_list[idx]
        self._refresh_files_list()

    def _clear_list(self):
        """Limpa toda a lista"""
        if not self.files_list:
            return

        if messagebox.askyesno("Confirmar", "Limpar todos os arquivos da lista?"):
            self.files_list.clear()
            self._refresh_files_list()

    def _refresh_files_list(self):
        """Atualiza a listbox com a lista de arquivos"""
        self.files_listbox.delete(0, tk.END)

        for i, item in enumerate(self.files_list, 1):
            filename = os.path.basename(item["txt_path"])
            topic = item["topic_name"]
            # Formato: #  |  Arquivo  |  Tópico
            line = f"{i:02d}  |  {filename:30s}  →  {topic}"
            self.files_listbox.insert(tk.END, line)

    # ----------------------------------------------------
    # Callbacks básicos
    # ----------------------------------------------------
    def _browse_dest(self):
        path = filedialog.askdirectory(title="Escolher pasta destino")
        if path:
            self.dest_path.set(path)

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_file_progress(self, current, total):
        """Atualiza barra de progresso de arquivo"""
        self.file_prog["maximum"] = total
        self.file_prog["value"] = current

    def _update_term_progress(self, current, total):
        """Atualiza barra de progresso de termo"""
        self.term_prog["maximum"] = total
        self.term_prog["value"] = current

    def _update_total_progress(self, current, total):
        """Atualiza barra de progresso total"""
        self.total_prog["maximum"] = total
        self.total_prog["value"] = current

    def _stop_flag(self):
        return self.stop_requested

    # ----------------------------------------------------
    # Controle de execução
    # ----------------------------------------------------
    def _start(self):
        if not self.files_list:
            messagebox.showwarning("Aviso", "Adicione pelo menos um arquivo search_terms.txt.")
            return

        if not self.dest_path.get():
            messagebox.showwarning("Aviso", "Selecione a pasta destino.")
            return

        self.stop_requested = False
        self.btn_start.config(state="disabled")
        self.btn_resume.config(state="disabled")
        self.btn_stop.config(state="normal")

        self.worker = threading.Thread(target=self._run, args=(False,), daemon=True)
        self.worker.start()

    def _resume(self):
        if not self.files_list:
            messagebox.showwarning("Aviso", "Adicione pelo menos um arquivo search_terms.txt.")
            return

        if not self.dest_path.get():
            messagebox.showwarning("Aviso", "Selecione a pasta destino.")
            return

        self.stop_requested = False
        self.btn_start.config(state="disabled")
        self.btn_resume.config(state="disabled")
        self.btn_stop.config(state="normal")

        self.worker = threading.Thread(target=self._run, args=(True,), daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_requested = True
        self._log("[STOP] Solicitado...")

    def _run(self, resume):
        """Processa todos os arquivos da lista"""
        try:
            total_files = len(self.files_list)

            for file_idx, item in enumerate(self.files_list, 1):
                if self.stop_requested:
                    self.after(0, self._log, f"[PARADO] no arquivo {file_idx}/{total_files}")
                    break

                txt_path = item["txt_path"]
                topic_name = item["topic_name"]

                self.after(0, self._log, f"\n{'='*60}")
                self.after(0, self._log, f"[ARQUIVO {file_idx}/{total_files}] {os.path.basename(txt_path)}")
                self.after(0, self._log, f"[TÓPICO] {topic_name}")
                self.after(0, self._log, f"{'='*60}\n")

                self.after(0, self._update_file_progress, file_idx, total_files)

                # Callback de progresso customizado para cada arquivo
                def on_progress(term_idx, total_terms, img_idx, imgs_per_term, term_label):
                    self.after(0, self._update_term_progress, img_idx, imgs_per_term)
                    
                    # Progresso total = arquivos * termos
                    global_current = (file_idx - 1) * 100 + (term_idx * 100 // total_terms)
                    global_total = total_files * 100
                    self.after(0, self._update_total_progress, global_current, global_total)

                extra_tags = []
                if self.extra_tag_wikipedia_cc.get():
                    extra_tags.append('site:wikipedia.org "creative commons"')
                if self.extra_tag_freepik.get():
                    extra_tags.append("site:freepik.com")

                # Executar download
                download_google_images(
                    search_terms_txt=txt_path,
                    dest_root=self.dest_path.get(),
                    images_per_term=self.images_per_term.get(),
                    manual_topic=topic_name,  # Força uso do tópico personalizado
                    extra_query_tags=extra_tags,
                    resume=resume,
                    on_log=lambda s: self.after(0, self._log, s),
                    on_progress=on_progress,
                    stop_flag=self._stop_flag,
                )

            # Concluído
            if not self.stop_requested:
                self.after(0, self._log, f"\n{'='*60}")
                self.after(0, self._log, "[CONCLUÍDO] Todos os arquivos foram processados!")
                self.after(0, self._log, f"{'='*60}")
                self.after(0, self._update_total_progress, total_files * 100, total_files * 100)

        except Exception as e:
            self.after(0, messagebox.showerror, "Erro", str(e))
            self.after(0, self._log, f"[ERRO] {e}")
        finally:
            self.after(0, self._on_finish)

    def _on_finish(self):
        self.btn_start.config(state="normal")
        self.btn_resume.config(state="normal")
        self.btn_stop.config(state="disabled")
if __name__ == "__main__":
    app = App()
    app.mainloop()
