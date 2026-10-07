"""Cancelable Windows process tree and estimated Robocopy progress."""
import codecs
import ctypes
import logging
from ctypes import wintypes
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
from paths_config import FIELDS, DESTINATION_KEYS
from windows_paths import file_path, path_key, ordinary_path

ENGINE_VERSION = '2.9.0'

class Cancelled(Exception):
    pass

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

class BASIC_LIMITS(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

class EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

api = ctypes.WinDLL("kernel32", use_last_error=True)
api.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
api.CreateJobObjectW.restype = wintypes.HANDLE
api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
api.CloseHandle.argtypes = [wintypes.HANDLE]
for name in ("SetInformationJobObject", "AssignProcessToJobObject", "TerminateJobObject", "CloseHandle"):
    getattr(api, name).restype = wintypes.BOOL

def upgrade_motor(path):
    motor = path.read_text(encoding="utf-8-sig")
    marker = re.compile(r'^rem SC_ENGINE_VERSION=' + re.escape(ENGINE_VERSION) + r'\s*$', re.MULTILINE)
    if marker.search(motor):
        return
    template = Path(__file__).resolve().parent / 'sincronizador.bat'
    updated = template.read_text(encoding='utf-8-sig')
    if marker.search(updated):
        # Keep editable options and legacy path defaults; JSON is never changed.
        for key in ['ORIGEM', *DESTINATION_KEYS, 'LIXEIRA', 'RETENCAO_DIAS', 'DRYRUN', 'TENTATIVAS', 'ESPERA']:
            setting = re.search(r'^set "' + key + r'=(.*)"$', motor, re.MULTILINE)
            if setting:
                updated = re.sub(r'^set "' + key + r'=.*"$', lambda _: setting.group(0),
                                 updated, count=1, flags=re.MULTILINE)
        backup = path.parent / 'logs' / ('sincronizador.antes-' + ENGINE_VERSION + '.bat')
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copyfile(path, backup)
        temporary = path.with_name('sincronizador.atualizando.tmp')
        try:
            temporary.write_text(updated, encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return
    updated = motor
    if "SC_WAIT_GATE" not in updated:
        updated = updated.replace("@echo off", "@echo off\nif defined SC_WAIT_GATE set /p SC_GATE= >nul", 1)
    if "echo %SC_RUN_TOKEN% Inicio:" not in updated:
        updated = updated.replace('>"%LOCK%" echo Inicio:', '>"%LOCK%" echo %SC_RUN_TOKEN% Inicio:')
    if "SC_DEST_START" not in updated:
        updated = updated.replace('set "NOME=%~2"', 'set "NOME=%~2"\necho SC_DEST_START %NOME%')
        updated = updated.replace("goto :eof", "echo SC_DEST_END %NOME%\ngoto :eof")
        updated = updated.replace('if "%DRYRUN%"=="1" (',
            'echo SC_COPY_START %NOME%\nif "%DRYRUN%"=="1" (')
    if "/BYTES" not in updated.upper():
        updated = updated.replace("/TEE ", "/V /FP /BYTES /NDL /TEE ")
    for _, key in FIELDS:
        old_override = f'if defined SC_{key} set "{key}=%SC_{key}%"'
        override = f'if defined SC_PATHS_CONFIG set "{key}=%SC_{key}%"'
        updated = updated.replace(old_override, override)
        if override not in updated:
            updated = re.sub(r'^set "' + key + r'=.*"$',
                lambda match: match.group(0) + "\n" + override, updated, count=1, flags=re.MULTILINE)
        if key in DESTINATION_KEYS:
            call = f'call :PROCESSAR "%{key}%" "{key}"'
            updated = re.sub(r'^' + re.escape(call) + '$',
                lambda _: "if defined " + key + " " + call, updated, flags=re.MULTILINE)
    if updated != motor:
        backup = path.with_name("sincronizador.antes-2.2.bat")
        if not backup.exists():
            shutil.copyfile(path, backup)
        path.write_text(updated, encoding="utf-8")

def cleanup_lock(logdir, token):
    path = logdir / "_SINCRONIZACAO_EM_EXECUCAO.lock"
    if path.exists() and token in path.read_text(errors="replace"):
        path.unlink()

def source_inventory(origin, stop):
    weights = {}
    root = file_path(origin)
    def fail(error):
        raise error
    if not root.is_dir():
        raise OSError("Origem inacessível: " + origin)
    for folder, directories, files in os.walk(root, onerror=fail, followlinks=False):
        if stop.is_set():
            raise Cancelled()
        directories[:] = [d for d in directories
            if not (Path(folder) / d).is_symlink() and not (Path(folder) / d).is_junction()]
        for name in files:
            if stop.is_set():
                raise Cancelled()
            path = Path(folder) / name
            if not path.is_symlink():
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    # The inventory estimates progress; Robocopy compares files again.
                    # Do not skip a disappearance of the origin itself.
                    root.stat()
                    logging.warning('Arquivo removido durante o levantamento: %s', ordinary_path(path))
                    continue
                weights[path_key(path)] = max(1, size)
    return weights

class Progress:
    def __init__(self, weights, publish, destinations=None):
        self.weights = weights
        self.total = max(1, sum(weights.values()))
        self.publish = publish
        self.destinations = list(destinations) if destinations is not None else ["DESTINO1", "DESTINO2", "DESTINO3"]
        if not self.destinations:
            raise ValueError("Nenhum destino configurado.")
        self.destination = -1
        self.copying = False
        self.done = set()
        self.pending = None
        self.fraction = 0.0
        self.completed_weight = 0
        self.last = -1

    def emit(self):
        if self.destination < 0:
            return
        part = 0.1 + 0.9 * min(1, (self.completed_weight +
            (self.weights.get(self.pending, 0) * self.fraction)) / self.total) if self.copying else 0
        percent = min(99, int((self.destination + part) * 100 / len(self.destinations)))
        percent = max(self.last, percent)
        if percent != self.last:
            self.last = percent
            self.publish(percent, "Destino " + self.destinations[self.destination].removeprefix("DESTINO"))

    def settle(self):
        if self.pending and self.pending not in self.done:
            self.completed_weight += self.weights[self.pending]
            self.done.add(self.pending)
        self.pending = None
        self.fraction = 0

    def feed(self, line):
        marker = re.fullmatch(r"SC_(DEST_START|COPY_START|DEST_END) DESTINO([123])", line.strip())
        if marker:
            kind, number = marker.groups()
            key = "DESTINO" + number
            if key not in self.destinations:
                return
            index = self.destinations.index(key)
            if kind == "DEST_START":
                self.destination = index
                self.copying = False
                self.done.clear()
                self.completed_weight = 0
                self.pending = None
                self.fraction = 0
                self.emit()
            elif kind == "COPY_START":
                self.copying = True
                self.emit()
            else:
                self.settle()
                self.last = max(self.last, min(99, int((index + 1) * 100 / len(self.destinations))))
                self.publish(self.last, "Destino " + number + " finalizado")
            return
        if not self.copying:
            return
        path_match = re.search(r"(?:[A-Za-z]:\\|\\\\)[^\r\n]+$", line)
        if path_match:
            key = path_key(path_match.group().strip())
            if key in self.weights:
                self.settle()
                self.pending = key
                self.emit()
                return
        percentage = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)%\s*", line)
        if percentage and self.pending:
            self.fraction = min(1, float(percentage.group(1).replace(",", ".")) / 100)
            self.emit()

def run_motor(bat, cwd, logdir, stop, tracker, token, execution_log, paths=None):
    """Gate the BAT until it belongs to a Job Object; cancel all descendants."""
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    process = None
    reader = None
    chunks = queue.Queue(maxsize=32)
    try:
        limits = EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        env = os.environ.copy()
        env.pop("SC_PATHS_CONFIG", None)
        for _, key in FIELDS:
            env.pop("SC_" + key, None)
        if paths is not None:
            env["SC_PATHS_CONFIG"] = "1"
            env.update({"SC_" + key: paths[key] for _, key in FIELDS})
        env.update(SC_LOGDIR=str(logdir), SC_TRASH_INDEX=str(logdir.parent / 'lixeira.sqlite3'),
                   SC_WAIT_GATE="1", SC_RUN_TOKEN=token)
        env.update(SC_ENGINE_EXE=sys.executable,
                   SC_ENGINE_ARGS='' if getattr(sys, 'frozen', False) else
                   subprocess.list2cmdline([str(Path(__file__).resolve().parent / 'app.py')]))
        command = '"' + os.environ.get("COMSPEC", "cmd.exe") + '" /d /s /c ""' + str(bat) + '""'
        process = subprocess.Popen(command, cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if not api.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        if stop.is_set():
            raise Cancelled()
        process.stdin.write(b"GO\n")
        process.stdin.flush()
        process.stdin.close()
        def read():
            try:
                while True:
                    block = os.read(process.stdout.fileno(), 4096)
                    if not block:
                        break
                    chunks.put(block)
            except Exception as error:
                chunks.put(error)
            finally:
                chunks.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        decoder = codecs.getincrementaldecoder("oem")(errors="replace")
        pending = ""
        with execution_log.open("wb") as output:
            while True:
                if stop.is_set():
                    api.TerminateJobObject(job, 1223)
                    raise Cancelled()
                try:
                    block = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                if block is None:
                    break
                if isinstance(block, Exception):
                    raise block
                output.write(block)
                output.flush()
                pending += decoder.decode(block)
                lines = re.split(r"[\r\n]", pending)
                pending = lines.pop()
                for line in lines:
                    tracker.feed(line)
            pending += decoder.decode(b"", final=True)
            if pending:
                tracker.feed(pending)
        return process.wait()
    finally:
        # Closing this handle kills any BAT/Robocopy child still alive.
        api.CloseHandle(job)
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if reader:
                # Drain the queue so the reader can finish even after cancellation.
                while reader.is_alive():
                    try:
                        chunks.get(timeout=0.1)
                    except queue.Empty:
                        pass
                reader.join(timeout=1)
            process.stdout.close()
        cleanup_lock(logdir, token)

