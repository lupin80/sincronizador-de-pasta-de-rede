"""Literal file paths and deletion-time retention for the BAT's trash phases."""
from datetime import datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from paths_config import FIELDS, DESTINATION_KEYS, validate_paths
from windows_paths import file_path, path_key, ordinary_path
from trash_index import TrashIndex, fingerprint, index_path

MANIFEST = '_sincronizador_lote.json'
BATCH_NAME = re.compile(r'^\d{8}T\d{6}Z_[0-9a-f]{32}$')


def _shorten(value, units):
    return value.encode('utf-16-le')[:max(0, units) * 2].decode('utf-16-le', errors='ignore')


def flat_target(root, name, key, instant, index):
    """Keep the original name when free; never overwrite another archived file."""
    candidate = root / name
    while os.path.lexists(candidate) or index.occupied(path_key(root), candidate.name):
        stem, extension = os.path.splitext(name)
        local_time = instant.astimezone(timezone(timedelta(hours=-3)))
        suffix = ' (apagado ' + local_time.strftime('%Y%m%d-%H%M%S') + ' ' + key + '-' + uuid.uuid4().hex[:8] + ')'
        # A filename component must still fit even when the original name was long.
        extension = _shorten(extension, 32)
        available = 255 - len((suffix + extension).encode('utf-16-le')) // 2
        candidate = root / (_shorten(stem, available) + suffix + extension)
    return candidate


def move_flat(source, target, index, identifier, before):
    try:
        os.rename(source, target)  # Windows rename refuses an existing target.
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17:
            raise
        incomplete = target.with_name('.sincronizador-' + uuid.uuid4().hex + '.tmp')
        owned = False
        try:
            with incomplete.open('xb'):
                owned = True
            shutil.copy2(source, incomplete)
            if fingerprint(source) != before:
                raise OSError('Arquivo alterado durante o arquivamento: ' + ordinary_path(source))
            os.rename(incomplete, target)
            index.identity(identifier, fingerprint(target))
            if fingerprint(source) != before:
                raise OSError('Arquivo alterado antes de remover a copia do destino: ' + ordinary_path(source))
            source.unlink()
        finally:
            if owned:
                incomplete.unlink(missing_ok=True)


def log(message):
    path = file_path(os.environ['SC_TRASH_LOG'])
    with path.open('ab') as output:
        output.write((message + '\r\n').encode('oem', errors='replace'))


def archive(paths, key, dryrun=False, now=None):
    """Count only completed moves; preserve the source of a failed move."""
    paths = validate_paths(paths)
    if key not in DESTINATION_KEYS or not paths[key]:
        raise ValueError('Destino de lixeira invalido.')
    origin, destination = file_path(paths['ORIGEM']), file_path(paths[key])
    if not origin.is_dir():
        raise OSError('Origem inacessivel: ' + str(origin))
    if not destination.exists():
        if dryrun:
            log('[SIMULACAO] Criar destino: ' + str(destination))
            return 0, 0
        destination.mkdir(parents=True, exist_ok=True)
        log('[DESTINO_CRIADO] ' + str(destination))
    if not destination.is_dir():
        raise OSError('Destino inacessivel: ' + str(destination))
    instant = now or datetime.now(timezone.utc)
    root = file_path(paths['LIXEIRA'])
    return _archive_files(origin, destination, root, key, instant, dryrun, now)


def _archive_files(origin, destination, root, key, instant, dryrun, now):
    moved, errors = 0, 0
    index = None
    def walk_error(error):
        nonlocal errors
        errors += 1
        log('[ERRO_LISTAGEM_DESTINO] ' + str(error))
    try:
        for folder, directories, files in os.walk(destination, onerror=walk_error, followlinks=False):
            directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()
                              and not (Path(folder) / d).is_junction()]
            for name in files:
                source = Path(folder) / name
                if source.is_symlink():
                    continue
                relative = source.relative_to(destination)
                corresponding = origin / relative
                try:
                    corresponding.stat()
                except FileNotFoundError:
                    # A share disappearing must not be treated as mass deletion.
                    if not origin.is_dir():
                        log('[ERRO_ORIGEM] Origem ficou inacessivel durante a sincronizacao.')
                        return moved, errors + 1
                except OSError as exc:
                    errors += 1
                    log('[ERRO_VERIFICACAO_ORIGEM] ' + str(relative) + ': ' + str(exc))
                    continue
                else:
                    continue
                if dryrun:
                    log('[SIMULACAO_LIXEIRA] ' + str(relative))
                    continue
                try:
                    if index is None:
                        index = TrashIndex(index_path())
                    root.mkdir(parents=True, exist_ok=True)
                    before = fingerprint(source)
                    target = flat_target(root, name, key, instant, index)
                    identifier = index.prepare(path_key(root), target.name, key, str(relative),
                                               ordinary_path(source), instant, before)
                    move_flat(source, target, index, identifier, before)
                    moved += 1
                    index.identity(identifier, fingerprint(target))
                    index.complete(identifier, now or datetime.now(timezone.utc))
                except Exception as exc:
                    errors += 1
                    log('[ERRO_LIXEIRA] ' + str(relative) + ': ' + str(exc))
                else:
                    log('[LIXEIRA] ' + str(source) + ' -> ' + str(target))
    finally:
        if index is not None:
            index.close()
    return moved, errors


def cleanup_flat(root, cutoff, dryrun, instant):
    database = index_path()
    if not database.exists():
        return
    resolved_root = root.resolve()
    with TrashIndex(database) as index:
        for record in index.records(path_key(root)):
            name = record['name']
            if not name or name in ('.', '..') or any(char in name for char in ('/', '\\', ':', '\0')):
                raise ValueError('Nome invalido no indice da lixeira.')
            target = root / name
            try:
                info = target.stat(follow_symlinks=False)
            except FileNotFoundError:
                root.stat()  # An unavailable share is not proof of deletion.
                if not dryrun:
                    index.forget(record['id'])
                continue
            if not stat.S_ISREG(info.st_mode) or target.is_symlink():
                log('[RETENCAO_IGNORADA] Item nao e arquivo regular: ' + name)
                continue
            if target.resolve().parent != resolved_root:
                raise OSError('Arquivo fora da lixeira configurada.')
            if fingerprint(target) != json.loads(record['fingerprint']):
                log('[RETENCAO_IGNORADA] Arquivo alterado ou substituido: ' + name)
                continue
            if record['state'] == 'pending':
                source = file_path(record['source'])
                try:
                    source.stat(follow_symlinks=False)
                except FileNotFoundError:
                    # Recover a completed rename after a crash without trusting an offline destination.
                    ancestor = source.parent
                    while True:
                        try:
                            parent_info = ancestor.stat()
                        except FileNotFoundError:
                            if ancestor == ancestor.parent:
                                raise OSError('Destino inacessivel ao recuperar registro da lixeira.')
                            ancestor = ancestor.parent
                        else:
                            if not stat.S_ISDIR(parent_info.st_mode):
                                raise OSError('Destino deixou de ser uma pasta.')
                            break
                    if not dryrun:
                        index.complete(record['id'], instant)
                    log('[LIXEIRA_RECUPERADA] Registro de movimentacao concluida: ' + name)
                continue  # A recovered file receives its full retention period.
            if record['state'] != 'ready':
                raise ValueError('Estado desconhecido no indice da lixeira.')
            archived = datetime.fromisoformat(record['archived_utc'])
            if archived.tzinfo is None:
                raise ValueError('Data de arquivamento sem fuso horario.')
            if archived > cutoff:
                continue
            if dryrun:
                log('[SIMULACAO_RETENCAO] ' + str(target))
            else:
                try:
                    target.unlink()
                except PermissionError:
                    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
                    target.unlink()
                index.forget(record['id'])
                log('[RETENCAO] Arquivo removido: ' + str(target))


def cleanup(trash, retention_days, dryrun=False, now=None):
    """Clean indexed flat files and recognized legacy batches, never unknown files."""
    if retention_days < 1:
        raise ValueError('A retencao deve ser de pelo menos um dia.')
    root = file_path(trash)
    if not root.exists():
        return
    instant = now or datetime.now(timezone.utc)
    cutoff = instant - timedelta(days=retention_days)
    cleanup_flat(root, cutoff, dryrun, instant)
    resolved_root = root.resolve()
    for key in DESTINATION_KEYS:
        parent = root / key
        if not parent.exists():
            continue
        if parent.is_symlink() or parent.is_junction():
            raise OSError('Pasta de lixeira e um link: ' + str(parent))
        if not parent.is_dir():
            continue  # A flat archived file may itself be named DESTINO1/2/3.
        for batch in parent.iterdir():
            if not batch.is_dir() or not BATCH_NAME.fullmatch(batch.name):
                continue  # Preserve legacy files: their deletion date is unknown.
            if batch.is_symlink() or batch.is_junction():
                raise OSError('Lote de lixeira e um link: ' + str(batch))
            manifest = batch / MANIFEST
            if not manifest.is_file() or manifest.is_symlink():
                continue
            record = json.loads(manifest.read_text(encoding='utf-8'))
            if record.get('application') != 'SincronizadorRede' or record.get('version') != 1:
                continue
            created = datetime.fromisoformat(record['created_utc'])
            if created.tzinfo is None:
                raise ValueError('Data de lote sem fuso horario: ' + str(batch))
            if created > cutoff:
                continue
            resolved_batch = batch.resolve()
            if not resolved_batch.is_relative_to(resolved_root) or resolved_batch == resolved_root:
                raise OSError('Lote fora da lixeira configurada.')
            # No recursive operation may cross a junction added to the batch.
            for folder, dirs, _ in os.walk(batch, followlinks=False):
                if any((Path(folder) / d).is_symlink() or (Path(folder) / d).is_junction() for d in dirs):
                    raise OSError('Lote contem links de pastas: ' + str(batch))
            if dryrun:
                log('[SIMULACAO_RETENCAO] ' + str(batch))
            else:
                def remove_readonly(function, path, error):
                    target = Path(path)
                    if not isinstance(error, PermissionError) or not target.resolve().is_relative_to(resolved_batch):
                        raise error
                    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
                    function(target)
                shutil.rmtree(resolved_batch, onexc=remove_readonly)
                log('[RETENCAO] Lote removido: ' + str(batch))


def execute(operation):
    moved = 0
    try:
        paths = validate_paths({key: os.environ.get('SC_' + key, '') for _, key in FIELDS})
        dryrun = os.environ.get('SC_DRYRUN') == '1'
        if operation == 'archive':
            moved, errors = archive(paths, os.environ['SC_CURRENT_NOME'], dryrun)
            return 1 if errors else 0
        cleanup(paths['LIXEIRA'], int(os.environ['SC_RETENCAO_DIAS']), dryrun)
        return 0
    except Exception as exc:
        log('[ERRO_' + operation.upper() + '] ' + str(exc))
        return 1
    finally:
        if operation == 'archive':
            file_path(os.environ['SC_TRASH_RESULT']).write_text(str(moved) + '\n', encoding='ascii')
