import argparse
import ctypes
from datetime import datetime
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
import winsound
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import pystray
from PIL import Image, ImageDraw, ImageFont
from sync_runtime import Cancelled, Progress, source_inventory, run_motor, upgrade_motor
from paths_config import FIELDS, DESTINATION_KEYS, load_paths, save_paths, validate_paths
from windows_schedule import get_schedule, set_schedule, interval_minutes, remove_schedule
from windows_paths import notification_text
from comparison_runtime import compare
from comparison_ui import show_comparison

TITLE = "Sincronizador de Rede"
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(__file__).resolve().parent
APP = Path(sys.executable).parent if FROZEN else BUNDLE
DATA = Path(os.environ["LOCALAPPDATA"]) / "SincronizadorRede"
STARTUP = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Sincronizador de Rede.lnk"
NO_WINDOW = subprocess.CREATE_NO_WINDOW
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateEventW.restype = wintypes.HANDLE
for method in ("CloseHandle", "SetEvent", "ResetEvent"):
    getattr(kernel, method).argtypes = [wintypes.HANDLE]
    getattr(kernel, method).restype = wintypes.BOOL
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD

def acquire_instance(tag="2", scheduled=False):
    mutex = kernel.CreateMutexW(None, False, "Local\\SincronizadorRede.App." + tag)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    existed = ctypes.get_last_error() == 183
    event = kernel.CreateEventW(None, True, False, "Local\\SincronizadorRede.Show." + tag)
    if not event:
        kernel.CloseHandle(mutex)
        raise ctypes.WinError(ctypes.get_last_error())
    sync_event = kernel.CreateEventW(None, True, False, "Local\\SincronizadorRede.Sync." + tag)
    if not sync_event:
        kernel.CloseHandle(event)
        kernel.CloseHandle(mutex)
        raise ctypes.WinError(ctypes.get_last_error())
    if existed:
        kernel.SetEvent(sync_event if scheduled else event)
        kernel.CloseHandle(sync_event)
        kernel.CloseHandle(event)
        kernel.CloseHandle(mutex)
        return None
    return mutex, event, sync_event

def icon(percent=None, state="running"):
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((6, 6, 58, 58), radius=10, fill="#194e73")
    draw.polygon([(32, 13), (49, 32), (40, 32), (40, 50),
                  (24, 50), (24, 32), (15, 32)], fill="white")
    if percent is not None:
        colors = {"running": "#194e73", "success": "#23683a", "error": "#a32c2c", "stopped": "#555555"}
        draw.rounded_rectangle((2, 2, 62, 62), radius=8, fill=colors[state])
        try:
            font = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts/segoeuib.ttf"), 32)
        except OSError:
            font = ImageFont.load_default(size=30)
        label = "!" if state == "error" else ("II" if state == "stopped" else str(percent))
        draw.text((32, 29), label, font=font, fill="white", anchor="mm")
        if state in ("running", "success"):
            draw.text((32, 52), "%", fill="white", anchor="mm")
    return im

def set_startup(on):
    if not on:
        STARTUP.unlink(missing_ok=True)
        return
    STARTUP.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SC_LINK"] = str(STARTUP)
    env["SC_TARGET"] = sys.executable
    env["SC_ARGS"] = "--tray" if FROZEN else subprocess.list2cmdline([str(BUNDLE / "app.py"), "--tray"])
    env["SC_CWD"] = str(APP)
    ps = ("$ErrorActionPreference='Stop';"
          "$w=New-Object -ComObject WScript.Shell;"
          "$s=$w.CreateShortcut($env:SC_LINK);"
          "$s.TargetPath=$env:SC_TARGET;$s.Arguments=$env:SC_ARGS;"
          "$s.WorkingDirectory=$env:SC_CWD;$s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                    "-Command", ps], env=env, check=True, creationflags=NO_WINDOW)

def migrate_settings(data):
    """Update only directory checks and log location; preserve other settings."""
    motor_path = data / "sincronizador.bat"
    motor = motor_path.read_text(encoding="utf-8-sig")
    log_line = 'set "LOGDIR=%LOCALAPPDATA%\\SincronizadorRede\\logs"'
    updated = re.sub(r'^set "LOGDIR=.*"$', lambda _: log_line, motor, flags=re.MULTILINE)
    override = 'if defined SC_LOGDIR set "LOGDIR=%SC_LOGDIR%"'
    if override not in updated:
        updated = updated.replace(log_line, log_line + "\n" + override)
    updated = updated.replace("\\NUL", "\\.")
    if updated != motor:
        backup = data / "sincronizador.antes-2.1.bat"
        if not backup.exists():
            shutil.copyfile(motor_path, backup)
        motor_path.write_text(updated, encoding="utf-8")
        logging.info("Motor atualizado: verificacao de pastas e logs locais.")
    cfg_path = data / "config.ini"
    config = cfg_path.read_text(encoding="utf-8-sig")
    config_updated = re.sub(r'^LOGDIR=.*$', lambda _: r"LOGDIR=%LOCALAPPDATA%\SincronizadorRede\logs",
                            config, flags=re.MULTILINE)
    if config_updated != config:
        backup = data / "config.antes-2.1.ini"
        if not backup.exists():
            shutil.copyfile(cfg_path, backup)
        cfg_path.write_text(config_updated, encoding="utf-8")


class App:
    def __init__(self, handles, args):
        self.handles = handles
        self.args = args
        self.running = False
        self.comparing = False
        self.comparison_window = None
        self.stop_event = threading.Event()
        self.percent = 0
        self.closed = False
        self.tray_ready = False
        self.jobs = queue.Queue()
        self.schedule_window = None
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(TITLE)
        self.root.geometry("700x615")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.report_callback_exception = self.callback_error
        self.data = Path(args.self_test).parent / "selftest-data" if args.self_test else DATA
        self.data.mkdir(parents=True, exist_ok=True)
        self.bat = self.data / "sincronizador.bat"
        self.cfg = self.data / "config.ini"
        for name in ("sincronizador.bat", "config.ini"):
            target = self.data / name
            if not target.exists():
                shutil.copyfile(BUNDLE / name, target)
        self.logdir = self.data / "logs"
        self.logdir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=self.logdir / "aplicativo.log", level=logging.INFO,
                            encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
        if not (self.logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock").exists():
            migrate_settings(self.data)
            upgrade_motor(self.bat)
        else:
            logging.info("Atualizacao do motor adiada: outra execucao possui o bloqueio.")
        logging.info("Sincronizador de Rede 2.9.0 iniciado. Logs: %s", self.logdir)
        tk.Label(self.root, text="SINCRONIZADOR DE REDE",
                 font=("Segoe UI", 17, "bold")).pack(pady=(18, 4))
        tk.Label(self.root, text="Sincronização manual ou automática • execução em segundo plano").pack()
        frame = tk.Frame(self.root, bd=1, relief="groove")
        frame.pack(fill="x", padx=25, pady=16)
        self.paths_file = self.data / "caminhos.json"
        defaults = {key: "" for _, key in FIELDS}
        self.config_error = None
        try:
            self.saved_paths = load_paths(self.paths_file, defaults)
        except ValueError as exc:
            self.saved_paths = defaults
            self.config_error = str(exc)
            logging.error("%s", exc)
        self.path_vars = {}
        self.path_entries = []
        self.paths_status = tk.StringVar()
        frame.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(FIELDS):
            tk.Label(frame, text=label + ":").grid(row=row, column=0, sticky="w", padx=(12, 8), pady=6)
            variable = tk.StringVar(value=self.saved_paths[key])
            self.path_vars[key] = variable
            entry = ttk.Entry(frame, textvariable=variable, font=("Consolas", 10))
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=6)
            self.path_entries.append(entry)
            variable.trace_add("write", self.paths_changed)
        tk.Label(frame, text="Preencha origem, lixeira e pelo menos um destino. Destinos vazios serão ignorados.",
                 anchor="w", wraplength=620).grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 0))
        self.save_btn = ttk.Button(frame, text="Salvar caminhos", command=self.save_folder_settings)
        self.save_btn.grid(row=6, column=1, sticky="e", padx=12, pady=(6, 4))
        tk.Label(frame, textvariable=self.paths_status, anchor="w", wraplength=620).grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
        self.paths_changed()
        controls = tk.Frame(self.root)
        controls.pack(fill="x", padx=25)
        self.btn = tk.Button(controls, text="INICIAR SINCRONIZAÇÃO",
                             height=2, command=self.sync)
        self.btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.compare_btn = tk.Button(controls, text="COMPARAR", height=2, command=self.compare)
        self.compare_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_btn = tk.Button(controls, text="PARAR SINCRONIZAÇÃO", height=2,
                                  state="disabled", command=self.stop)
        self.stop_btn.pack(side="right", fill="x", expand=True)
        self.progressbar = ttk.Progressbar(self.root, maximum=100, mode="determinate")
        self.progressbar.pack(fill="x", padx=25, pady=(10, 3))
        self.progress_text = tk.StringVar(value="Andamento estimado: 0%")
        tk.Label(self.root, textvariable=self.progress_text).pack()
        self.auto = tk.BooleanVar(value=STARTUP.exists())
        tk.Checkbutton(self.root, text="Iniciar com o Windows (na bandeja)",
                       variable=self.auto, command=self.toggle).pack(pady=8)
        actions = tk.Frame(self.root)
        actions.pack()
        for label, action in (("Abrir logs", self.logs),
                              ("Arquivos do app", lambda: os.startfile(self.data)),
                              ("Editar motor", self.edit_motor)):
            tk.Button(actions, text=label, width=17, command=action).pack(side="left", padx=3)
        self.status = tk.StringVar(value="Pronto. Use o menu da bandeja para sincronizar.")
        if not self.saved_paths["ORIGEM"] or not self.saved_paths["LIXEIRA"]:
            self.status.set("Configure os caminhos antes de iniciar a sincronização.")
        if (self.logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock").exists():
            self.status.set("Existe uma execução anterior. Consulte os logs antes de iniciar.")
        tk.Label(self.root, textvariable=self.status).pack(pady=12)
        self.tray = pystray.Icon("SincronizadorRede", icon(), TITLE, pystray.Menu(
            pystray.MenuItem("Abrir", self.enqueue(self.show), default=True),
            pystray.MenuItem("Comparar pastas", self.enqueue(self.compare), enabled=lambda item: not self.running),
            pystray.MenuItem("Iniciar sincronização", self.enqueue(self.sync)),
            pystray.MenuItem("Parar sincronização", self.enqueue(self.stop),
                             enabled=lambda item: self.running and not self.stop_event.is_set()),
            pystray.MenuItem("Sincronização automática...", self.enqueue(self.configure_schedule)),
            pystray.MenuItem("Abrir logs", self.enqueue(self.logs)),
            pystray.MenuItem("Sair", self.enqueue(self.quit))))
        threading.Thread(target=self.run_tray, daemon=True).start()
        self.root.after(100, self.poll)
        self.root.after(10000, self.check_tray)
        if args.window or (not args.self_test and (not self.saved_paths["ORIGEM"] or not self.saved_paths["LIXEIRA"])):
            self.show()
        if args.self_test:
            self.root.after(1500, self.self_test)
        elif self.config_error:
            self.jobs.put(lambda: (self.show(), messagebox.showerror(TITLE, self.config_error)))
        if getattr(args, "scheduled", False):
            self.root.after(1500, lambda: self.sync(automatic=True))

    def configure_schedule(self):
        if self.schedule_window and self.schedule_window.winfo_exists():
            self.schedule_window.lift()
            return
        window = self.schedule_window = tk.Toplevel(self.root)
        window.title(TITLE + " - sincronização automática")
        window.geometry("540x300")
        window.resizable(False, False)
        enabled = tk.BooleanVar(value=False)
        amount = tk.StringVar(value="60")
        unit = tk.StringVar(value="Minutos")
        status = tk.StringVar(value="Consultando o Agendador do Windows...")
        check = ttk.Checkbutton(window, text="Executar sincronização automaticamente", variable=enabled)
        check.pack(anchor="w", padx=20, pady=(18, 14))
        row = ttk.Frame(window)
        row.pack(fill="x", padx=20)
        ttk.Label(row, text="Executar a cada:").pack(side="left", padx=(0, 10))
        number = ttk.Spinbox(row, from_=1, to=44640, textvariable=amount, width=8)
        number.pack(side="left", padx=(0, 8))
        units = ttk.Combobox(row, values=("Minutos", "Horas", "Dias"), textvariable=unit,
                             state="readonly", width=12)
        units.pack(side="left")
        ttk.Label(window, text="Intervalo: de 1 minuto a 31 dias.\nA primeira execução ocorre após o intervalo escolhido.\nUsa os caminhos salvos e funciona enquanto seu usuário\nestiver conectado ao Windows. Execuções ocupadas são ignoradas.",
                  justify="left", wraplength=490).pack(anchor="w", padx=20, pady=14)
        ttk.Label(window, textvariable=status, wraplength=490).pack(anchor="w", padx=20)
        buttons = ttk.Frame(window)
        buttons.pack(side="bottom", fill="x", padx=20, pady=16)
        save_button = ttk.Button(buttons, text="Salvar agendamento")
        save_button.pack(side="right")
        ttk.Button(buttons, text="Fechar", command=window.destroy).pack(side="right", padx=10)

        def controls(active):
            for widget in (check, number, save_button):
                widget.configure(state="normal" if active else "disabled")
            units.configure(state="readonly" if active else "disabled")

        def received(state):
            if not window.winfo_exists():
                return
            minutes = state["interval_minutes"]
            enabled.set(state["enabled"])
            if minutes % 1440 == 0:
                amount.set(str(minutes // 1440))
                unit.set("Dias")
            elif minutes % 60 == 0:
                amount.set(str(minutes // 60))
                unit.set("Horas")
            else:
                amount.set(str(minutes))
                unit.set("Minutos")
            status.set(f"Ativo: a cada {minutes} minuto(s)." if state["enabled"] else "Sincronização automática desativada.")
            controls(True)

        def failed(text):
            logging.error("Agendamento: %s", text)
            if window.winfo_exists():
                status.set(text)
                # Reopen to query the real task state before another attempt.
                messagebox.showerror(TITLE, text, parent=window)

        def request(operation):
            def work():
                try:
                    state = operation()
                except Exception as exc:
                    self.jobs.put(lambda text=str(exc): failed(text))
                else:
                    self.jobs.put(lambda state=state: received(state))
            threading.Thread(target=work, daemon=True).start()

        def apply():
            on = enabled.get()
            try:
                minutes = interval_minutes(amount.get(), unit.get()) if on else 60
            except ValueError as exc:
                messagebox.showerror(TITLE, str(exc), parent=window)
                return
            if on:
                try:
                    validate_paths(load_paths(self.paths_file, {}))
                except (ValueError, OSError) as exc:
                    messagebox.showerror(TITLE, "Salve os caminhos completos antes de ativar.\n" + str(exc), parent=window)
                    return
            executable = sys.executable
            arguments = "--scheduled --tray" if FROZEN else subprocess.list2cmdline([str(BUNDLE / "app.py"), "--scheduled", "--tray"])
            controls(False)
            status.set("Atualizando a tarefa no Agendador do Windows...")
            request(lambda: set_schedule(on, minutes, executable, arguments))

        save_button.configure(command=apply)
        controls(False)
        request(get_schedule)

    def current_paths(self):
        return {key: variable.get() for key, variable in self.path_vars.items()}

    def paths_changed(self, *_):
        dirty = self.current_paths() != self.saved_paths
        self.paths_status.set("Corrija os caminhos e salve novamente." if self.config_error else
            ("Alterações não salvas." if dirty else
             ("Preencha os campos e clique em Salvar caminhos." if not self.saved_paths["ORIGEM"] or not self.saved_paths["LIXEIRA"] else
              "Caminhos prontos para a próxima sincronização.")))

    def validated_paths(self):
        return validate_paths(self.current_paths())

    def save_folder_settings(self):
        if self.running:
            messagebox.showwarning(TITLE, "Aguarde a sincronização terminar antes de salvar os caminhos.")
            return False
        try:
            paths = self.validated_paths()
            save_paths(self.paths_file, paths)
        except (ValueError, OSError) as exc:
            messagebox.showerror(TITLE, str(exc))
            return False
        self.saved_paths = paths
        self.config_error = None
        for key, variable in self.path_vars.items():
            variable.set(paths[key])
        self.paths_status.set("Caminhos salvos. Serão usados na próxima sincronização.")
        self.status.set("Configuração salva com sucesso.")
        logging.info("Caminhos de sincronizacao salvos em %s", self.paths_file)
        return True

    def set_paths_enabled(self, enabled):
        for widget in [*self.path_entries, self.save_btn]:
            widget.configure(state="normal" if enabled else "disabled")

    def edit_motor(self):
        if self.running or (self.logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock").exists():
            messagebox.showwarning(TITLE, "Aguarde a sincronização terminar antes de editar o motor.")
            return
        subprocess.Popen(["notepad.exe", str(self.bat)])

    def enqueue(self, function):
        def callback(*_):
            self.jobs.put(function)
        return callback

    def run_tray(self):
        try:
            self.tray.run(self.tray_setup)
        except Exception:
            logging.exception("Falha ao iniciar bandeja")
            self.jobs.put(self.show)
            self.jobs.put(lambda: messagebox.showerror(TITLE, "Falha ao iniciar a bandeja. Consulte aplicativo.log."))

    def tray_setup(self, tray):
        tray.visible = True
        self.jobs.put(lambda: setattr(self, "tray_ready", True))

    def check_tray(self):
        if not self.closed and not self.tray_ready:
            self.show()
            self.status.set("Bandeja indisponível. A janela continuará aberta.")

    def callback_error(self, kind, value, tb):
        logging.error("Erro de interface", exc_info=(kind, value, tb))
        messagebox.showerror(TITLE, str(value))

    def poll(self):
        if self.closed:
            return
        if kernel.WaitForSingleObject(self.handles[1], 0) == 0:
            kernel.ResetEvent(self.handles[1])
            self.show()
        if kernel.WaitForSingleObject(self.handles[2], 0) == 0:
            kernel.ResetEvent(self.handles[2])
            self.sync(automatic=True)
        while not self.jobs.empty():
            try:
                self.jobs.get_nowait()()
            except Exception:
                logging.exception("Erro no comando da bandeja")
                self.show()
                messagebox.showerror(TITLE, "Erro no comando. Consulte aplicativo.log.")
            if self.closed:
                return
        self.root.after(100, self.poll)

    def toggle(self):
        try:
            set_startup(self.auto.get())
            self.status.set("Inicialização automática atualizada.")
        except Exception as exc:
            self.auto.set(STARTUP.exists())
            messagebox.showerror(TITLE, str(exc))

    def compare(self):
        if self.running:
            self.show()
            return
        if (self.logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock").exists():
            self.notify_error("Aguarde a outra sincronização terminar antes de comparar.")
            return
        if self.config_error or self.current_paths() != self.saved_paths:
            self.show()
            messagebox.showwarning(TITLE, "Salve os caminhos antes de comparar as pastas.")
            return
        try:
            paths = self.validated_paths()
            motor_stamp = self.bat.stat().st_mtime_ns
        except (ValueError, OSError) as exc:
            messagebox.showerror(TITLE, str(exc))
            return
        self.running = self.comparing = True
        self.set_paths_enabled(False)
        self.stop_event.clear()
        self.btn.configure(state="disabled")
        self.compare_btn.configure(state="disabled", text="COMPARANDO...")
        self.stop_btn.configure(state="normal", text="PARAR COMPARAÇÃO")
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start(100)
        self.progress_text.set("Comparando pastas...")
        self.status.set("Analisando diferenças entre origem e destinos...")
        self.tray.title = TITLE + " - comparando pastas"
        self.tray.update_menu()
        logging.info("Comparacao iniciada; somente leitura.")

        def worker():
            try:
                result = compare(paths, self.stop_event, lambda stage:
                    self.jobs.put(lambda stage=stage: self.status.set(stage)))
            except Cancelled:
                self.jobs.put(lambda: self.comparison_done(None, motor_stamp))
            except Exception as exc:
                logging.exception("Falha ao comparar pastas")
                self.jobs.put(lambda text=str(exc): self.comparison_done(None, motor_stamp, text))
            else:
                self.jobs.put(lambda result=result: self.comparison_done(result, motor_stamp))
        threading.Thread(target=worker, daemon=True).start()

    def comparison_done(self, result, motor_stamp, error=None):
        self.running = self.comparing = False
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate", value=0)
        self.btn.configure(state="normal")
        self.compare_btn.configure(state="normal", text="COMPARAR")
        self.stop_btn.configure(state="disabled", text="PARAR SINCRONIZAÇÃO")
        self.set_paths_enabled(True)
        self.tray.title = TITLE
        self.tray.update_menu()
        if error:
            self.status.set("Falha na comparação. Consulte os logs.")
            self.progress_text.set("Comparação não concluída.")
            self.notify_error(error + "\nLogs: " + str(self.logdir))
        elif result is None or self.stop_event.is_set():
            self.status.set("Comparação interrompida.")
            self.progress_text.set("Comparação interrompida.")
            logging.info("Comparacao interrompida pelo usuario.")
        else:
            self.status.set("Comparação concluída. Revise a prévia antes de sincronizar.")
            self.progress_text.set("Comparação concluída.")
            logging.info("Comparacao concluida: %s alteracoes previstas.", len(result.changes))
            self.show()
            show_comparison(self, result, lambda: self.sync_from_comparison(result, motor_stamp))

    def sync_from_comparison(self, result, motor_stamp):
        if self.running:
            self.show()
            return
        try:
            changed = (self.current_paths() != result.paths or self.saved_paths != result.paths
                       or self.bat.stat().st_mtime_ns != motor_stamp)
        except OSError:
            changed = True
        if changed:
            messagebox.showwarning(TITLE, "Os caminhos ou o motor mudaram. Salve e faça uma nova comparação.")
            return
        self.sync()
        if self.running and self.comparison_window and self.comparison_window.winfo_exists():
            self.comparison_window.destroy()

    def sync(self, automatic=False):
        if self.running:
            if automatic:
                logging.info("Agendamento ignorado: sincronizacao em andamento.")
            else:
                self.show()
            return
        if not self.bat.exists():
            messagebox.showerror(TITLE, "Motor sincronizador.bat não encontrado.")
            return
        if (self.logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock").exists():
            if automatic:
                logging.info("Agendamento ignorado: outra execucao possui o bloqueio.")
            else:
                self.notify_error("Outra sincronização possui o bloqueio de execução. Consulte os logs antes de iniciar novamente.")
            return
        if not automatic and (self.config_error or self.current_paths() != self.saved_paths):
            self.show()
            messagebox.showwarning(TITLE, "Salve os caminhos antes de iniciar a sincronização.")
            return
        try:
            paths = validate_paths(load_paths(self.paths_file, {})) if automatic else self.validated_paths()
        except (ValueError, OSError) as exc:
            self.show()
            messagebox.showerror(TITLE, str(exc))
            return
        upgrade_motor(self.bat)
        self.running = True
        self.set_paths_enabled(False)
        self.stop_event.clear()
        self.percent = 0
        self.btn.configure(state="disabled", text="SINCRONIZANDO...")
        self.compare_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.set_progress(0, "Analisando origem")
        self.status.set("Analisando arquivos da origem...")
        self.tray.update_menu()
        def worker():
            rc = 99
            cancelled = False
            error = None
            try:
                origin = paths["ORIGEM"]
                weights = source_inventory(origin, self.stop_event)
                tracker = Progress(weights, lambda percent, stage:
                    self.jobs.put(lambda: self.set_progress(percent, stage)),
                    destinations=[key for key in DESTINATION_KEYS if paths[key]])
                execution_log = self.logdir / ("Execucao_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + ".log")
                logging.info("Iniciando motor (%s). Saida: %s", "automatico" if automatic else "manual", execution_log)
                rc = run_motor(self.bat, self.data, self.logdir, self.stop_event,
                               tracker, uuid.uuid4().hex, execution_log, paths=paths)
                cancelled = self.stop_event.is_set()
                logging.info("Motor finalizado com codigo %s", rc)
            except Cancelled:
                cancelled = True
                logging.info("Sincronizacao interrompida pelo usuario.")
            except Exception as exc:
                logging.exception("Falha ao executar motor")
                error = str(exc)
            self.jobs.put(lambda: self.done(rc, cancelled, error))
        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        if not self.running or self.stop_event.is_set():
            return
        self.stop_event.set()
        self.stop_btn.configure(state="disabled")
        operation = "comparação" if self.comparing else "sincronização"
        self.status.set("Parando " + operation + "...")
        self.tray.title = TITLE + " - parando " + operation
        self.tray.update_menu()

    def set_progress(self, percent, stage):
        self.percent = max(0, min(100, percent))
        self.progressbar["value"] = self.percent
        self.progress_text.set(f"Andamento estimado: {self.percent}% - {stage}")
        self.tray.title = f"{TITLE} - {self.percent}% (estimado) - {stage}"
        self.tray.icon = icon(self.percent)
        if self.running and not self.stop_event.is_set():
            self.status.set("Sincronização em andamento: " + stage)

    def notify_error(self, text):
        try:
            self.tray.notify(notification_text(text), TITLE + " - erro")
        except Exception:
            logging.exception("Falha ao mostrar notificacao da bandeja")
        self.show()
        messagebox.showerror(TITLE, text)

    def done(self, rc, cancelled=False, error=None):
        self.running = False
        self.set_paths_enabled(True)
        self.btn.configure(state="normal", text="INICIAR SINCRONIZAÇÃO")
        self.compare_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.tray.update_menu()
        if cancelled:
            self.status.set("Sincronização interrompida. Você pode iniciar novamente.")
            self.progress_text.set(f"Interrompida em {self.percent}% (estimado)")
            self.tray.title = f"{TITLE} - interrompida em {self.percent}%"
            self.tray.icon = icon(self.percent, "stopped")
            return
        if rc == 0:
            self.set_progress(100, "Concluída")
            self.status.set("Sincronização concluída. Confira o resultado no log.")
            self.tray.title = TITLE + " - concluída: 100%"
            self.tray.icon = icon(100, "success")
            try:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except RuntimeError:
                logging.exception("Falha ao reproduzir som de conclusao")
            return
        messages = {0: "Motor finalizado. Confira o resultado no log.",
                    10: "Origem inacessível. Consulte o log.",
                    20: "Existe um bloqueio de sincronização. Consulte o log."}
        text = error or messages.get(rc, f"Erro na sincronização (código {rc}). Consulte o log.")
        self.status.set(text)
        self.progress_text.set(f"Erro em {self.percent}% (estimado)")
        self.tray.title = TITLE + " - erro na sincronização"
        self.tray.icon = icon(self.percent, "error")
        self.notify_error(text + "\nLogs: " + str(self.logdir))

    def logs(self):
        self.logdir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.logdir)

    def hide(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            self.status.set("Aguarde a inicialização da bandeja.")

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.auto.set(STARTUP.exists())

    def quit(self):
        if self.running:
            self.show()
            messagebox.showinfo(TITLE, "Aguarde a operação terminar antes de sair.")
            return
        self.closed = True
        self.tray.stop()
        self.root.destroy()

    def self_test(self):
        result = {"frozen": FROZEN, "tray_ready": self.tray_ready,
                  "application_name": self.root.title() == "Sincronizador de Rede",
                  "blank_initial_paths": not self.paths_file.exists() and all(not v.get() for v in self.path_vars.values()),
                  "starts_hidden": self.root.state() == "withdrawn",
                  "motor_available": self.bat.is_file(),
                  "config_available": self.cfg.is_file(),
                  "sync_not_started": not self.running, "local_log_directory": self.logdir.is_dir(), "application_log": (self.logdir / "aplicativo.log").is_file(),
                  "menu": [item.text for item in self.tray.menu]}
        self.show()
        self.root.update()
        result["opens_window"] = self.root.state() == "normal"
        result["window_geometry"] = {"width": self.root.winfo_width(), "x": self.root.winfo_rootx(),
            "fields": [{"width": w.winfo_width(), "x": w.winfo_rootx()} for w in self.path_entries]}
        result["fields_fit_window"] = all(entry.winfo_width() > 400 and
            entry.winfo_rootx() + entry.winfo_width() <= self.root.winfo_rootx() + self.root.winfo_width()
            for entry in self.path_entries)
        result["save_button_fit_window"] = self.save_btn.winfo_rooty() + self.save_btn.winfo_height() <= (
            self.root.winfo_rooty() + self.root.winfo_height())
        self.hide()
        result["returns_to_tray"] = self.root.state() == "withdrawn"
        result["stop_button_idle_disabled"] = self.stop_btn.cget("state") == "disabled"
        result["editable_paths"] = len(self.path_entries) == len(FIELDS) and all(
            str(entry.cget("state")) == "normal" for entry in self.path_entries)
        result["trash_field"] = "LIXEIRA" in self.path_vars
        result["schedule_menu"] = any("automática" in item.text for item in self.tray.menu)
        result["save_paths_button"] = str(self.save_btn.cget("state")) == "normal"
        result["compare_button"] = self.compare_btn.cget("state") == "normal"
        result["compare_menu"] = any(item.text == "Comparar pastas" for item in self.tray.menu)
        result["controls_fit_window"] = all(w.winfo_rootx() + w.winfo_width() <= (
            self.root.winfo_rootx() + self.root.winfo_width()) for w in (self.btn, self.compare_btn, self.stop_btn))
        self.set_progress(37, "Teste")
        result["tray_percentage"] = "37%" in self.tray.title
        result["progressbar"] = int(self.progressbar["value"]) == 37
        fixture = self.data / 'teste-comparacao'
        fixture.mkdir(exist_ok=True)
        origin, target = fixture / 'origem', fixture / 'destino'
        origin.mkdir(exist_ok=True)
        target.mkdir(exist_ok=True)
        (origin / 'novo.txt').write_text('novo', encoding='utf-8')
        (target / 'excluir.txt').write_text('preservado', encoding='utf-8')
        preview_paths = dict(ORIGEM=str(origin), DESTINO1=str(target), DESTINO2='', DESTINO3='',
                             LIXEIRA=str(fixture / 'lixeira'))
        preview = compare(preview_paths, threading.Event())
        result["comparison_actions"] = {item.action for item in preview.changes} == {'Copiar', 'Enviar à lixeira'}
        result["comparison_read_only"] = (target / 'excluir.txt').read_text(encoding='utf-8') == 'preservado' and not (
            target / 'novo.txt').exists() and not (fixture / 'lixeira').exists()
        show_comparison(self, preview, lambda: None)
        self.root.update()
        result["comparison_window"] = self.comparison_window.winfo_exists() == 1
        Path(self.args.self_test).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.quit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tray", action="store_true")
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--self-test")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--remove-schedule", action="store_true")
    parser.add_argument("--trash-operation", choices=('archive', 'cleanup'))
    parser.add_argument("--upgrade-engine", action="store_true")
    args = parser.parse_args()
    if args.trash_operation:
        from trash_runtime import execute
        return execute(args.trash_operation)
    if args.upgrade_engine:
        data = Path(args.self_test).parent / 'selftest-data' if args.self_test else DATA
        data.mkdir(parents=True, exist_ok=True)
        if (data / 'logs' / '_SINCRONIZACAO_EM_EXECUCAO.lock').exists():
            return 1
        for name in ('sincronizador.bat', 'config.ini'):
            if not (data / name).exists():
                shutil.copyfile(BUNDLE / name, data / name)
        migrate_settings(data)
        upgrade_motor(data / 'sincronizador.bat')
        return 0
    if args.remove_schedule:
        try:
            remove_schedule()
        except Exception:
            return 1
        return 0
    handles = acquire_instance("test-" + uuid.uuid4().hex if args.self_test else "2", scheduled=args.scheduled)
    if handles is None:
        return
    try:
        app = App(handles, args)
        app.root.mainloop()
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)

if __name__ == "__main__":
    sys.exit(main())



