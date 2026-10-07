"""Read-only preview of the existing one-way sync, using size and file time."""
from dataclasses import dataclass, field
from datetime import datetime
import csv
import os
from pathlib import Path
import stat

from paths_config import DESTINATION_KEYS, validate_paths
from sync_runtime import Cancelled
from windows_paths import file_path, ordinary_path

COPY = 'Copiar'
UPDATE = 'Atualizar'
TRASH = 'Enviar à lixeira'
FOLDER = 'Criar pasta'


@dataclass(frozen=True)
class FileInfo:
    relative: str
    size: int
    modified_ns: int


@dataclass(frozen=True)
class Change:
    destination: str
    action: str
    relative: str
    size: int | None


@dataclass
class Comparison:
    paths: dict
    changes: list = field(default_factory=list)
    summaries: dict = field(default_factory=dict)
    finished_at: str = ''


def check_stop(stop):
    if stop.is_set():
        raise Cancelled()


def folder_exists(root, allow_missing=False):
    """A missing destination is empty only when an ancestor is accessible."""
    try:
        info = root.stat()
    except FileNotFoundError:
        if not allow_missing:
            raise OSError('Pasta inacessível ou inexistente: ' + ordinary_path(root))
        ancestor = root.parent
        while True:
            try:
                info = ancestor.stat()
            except FileNotFoundError:
                if ancestor == ancestor.parent:
                    raise OSError('Unidade ou compartilhamento inacessível: ' + ordinary_path(root))
                ancestor = ancestor.parent
            else:
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError('Um arquivo impede acessar a pasta: ' + ordinary_path(ancestor))
                return False
    if not stat.S_ISDIR(info.st_mode):
        raise OSError('O caminho não é uma pasta: ' + ordinary_path(root))
    return True


def scan(root, stop, allow_missing=False):
    files, directories = {}, {}
    if not folder_exists(root, allow_missing):
        return files, directories
    def fail(error):
        raise error
    for folder, dirs, names in os.walk(root, onerror=fail, followlinks=False):
        check_stop(stop)
        dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()
                   and not (Path(folder) / d).is_junction()]
        relative_folder = str(Path(folder).relative_to(root))
        if relative_folder != '.':
            if os.path.normcase(relative_folder) in directories:
                raise OSError('Nomes de pastas ambíguos: ' + relative_folder)
            directories[os.path.normcase(relative_folder)] = relative_folder
        for name in names:
            check_stop(stop)
            path = Path(folder) / name
            if path.is_symlink():
                continue
            info = path.stat()  # An unreadable/disappearing file invalidates the preview.
            relative = str(path.relative_to(root))
            if os.path.normcase(relative) in files:
                raise OSError('Nomes de arquivos ambíguos: ' + relative)
            files[os.path.normcase(relative)] = FileInfo(relative, info.st_size, info.st_mtime_ns)
    root.stat()  # Never interpret an unavailable source as an empty folder.
    return files, directories


def compare(paths, stop, publish=lambda stage: None):
    paths = validate_paths(paths)
    result = Comparison(dict(paths))
    origin = file_path(paths['ORIGEM'])
    publish('Analisando origem')
    source_files, source_dirs = scan(origin, stop)
    for destination in DESTINATION_KEYS:
        if not paths[destination]:
            continue
        check_stop(stop)
        publish('Analisando destino ' + destination.removeprefix('DESTINO'))
        target = file_path(paths[destination])
        target_files, target_dirs = scan(target, stop, allow_missing=True)
        conflicts = (source_files.keys() & target_dirs.keys()) | (source_dirs.keys() & target_files.keys())
        if conflicts:
            key = next(iter(conflicts))
            relative = source_files[key].relative if key in source_files else source_dirs[key]
            raise OSError('Conflito entre arquivo e pasta no ' + destination + ': ' + relative)
        summary = {COPY: 0, UPDATE: 0, TRASH: 0, FOLDER: 0,
                   'Iguais': 0, 'Ignorados': 0, 'copy_bytes': 0, 'trash_bytes': 0}
        for key, item in source_files.items():
            check_stop(stop)
            other = target_files.get(key)
            if other is None:
                action = COPY
            elif (item.size, item.modified_ns) != (other.size, other.modified_ns):
                action = UPDATE
            else:
                summary['Iguais'] += 1
                continue
            result.changes.append(Change(destination, action, item.relative, item.size))
            summary[action] += 1
            summary['copy_bytes'] += item.size
        for key, item in target_files.items():
            check_stop(stop)
            if key in source_files:
                continue
            # Items below excluded links can exist in the source but not in its scan.
            # The trash engine checks existence; use the same check before suggesting a move.
            try:
                (origin / item.relative).stat()
            except FileNotFoundError:
                origin.stat()
            else:
                summary['Ignorados'] += 1
                continue
            result.changes.append(Change(destination, TRASH, item.relative, item.size))
            summary[TRASH] += 1
            summary['trash_bytes'] += item.size
        for key, relative in source_dirs.items():
            check_stop(stop)
            if key not in target_dirs:
                result.changes.append(Change(destination, FOLDER, relative, None))
                summary[FOLDER] += 1
        result.summaries[destination] = summary
    origin.stat()
    check_stop(stop)
    result.finished_at = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    return result


def export_csv(result, path):
    with file_path(path).open('w', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['Destino', 'Ação', 'Arquivo ou pasta', 'Tamanho (bytes)'])
        for change in result.changes:
            relative = change.relative
            if relative.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                relative = "'" + relative  # Preserve file names as text in spreadsheet readers.
            writer.writerow([change.destination, change.action, relative,
                             '' if change.size is None else change.size])
