"""Crash-safe trash metadata stored in AppData, outside the flat trash folder."""
import json
import os
from pathlib import Path
import sqlite3


def index_path():
    if os.environ.get('SC_TRASH_INDEX'):
        return Path(os.environ['SC_TRASH_INDEX'])
    logs = Path(os.environ['SC_LOGDIR']) if os.environ.get('SC_LOGDIR') else Path(os.environ['SC_TRASH_LOG']).parent
    return logs.parent / 'lixeira.sqlite3'


def fingerprint(path):
    info = path.stat(follow_symlinks=False)
    return dict(device=str(info.st_dev), inode=str(info.st_ino), size=info.st_size,
                modified_ns=info.st_mtime_ns, born_ns=getattr(info, 'st_birthtime_ns', 0))


class TrashIndex:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            marker = self.connection.execute('SELECT value FROM metadata WHERE key=?', ('application',)).fetchone()
            if marker is not None and marker['value'] != 'SincronizadorRede-flat-1':
                raise ValueError('Indice de lixeira de outro aplicativo ou versao desconhecida.')
            self.connection.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('application', 'SincronizadorRede-flat-1'))
            self.connection.execute('''CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, root TEXT NOT NULL, name_key TEXT NOT NULL, name TEXT NOT NULL,
                destination TEXT NOT NULL, relative TEXT NOT NULL, source TEXT NOT NULL,
                archived_utc TEXT NOT NULL, state TEXT NOT NULL, fingerprint TEXT NOT NULL,
                UNIQUE(root, name_key))''')
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def occupied(self, root, name):
        return self.connection.execute('SELECT 1 FROM files WHERE root=? AND name_key=?',
                                       (root, os.path.normcase(name))).fetchone() is not None

    def prepare(self, root, name, destination, relative, source, instant, identity):
        with self.connection:
            cursor = self.connection.execute('''INSERT INTO files
                (root,name_key,name,destination,relative,source,archived_utc,state,fingerprint)
                VALUES (?,?,?,?,?,?,?,?,?)''', (root, os.path.normcase(name), name, destination, relative,
                    source, instant.isoformat(), 'pending', json.dumps(identity)))
        return cursor.lastrowid

    def identity(self, identifier, identity):
        with self.connection:
            self.connection.execute('UPDATE files SET fingerprint=? WHERE id=?', (json.dumps(identity), identifier))

    def complete(self, identifier, instant):
        with self.connection:
            self.connection.execute('UPDATE files SET state=?,archived_utc=? WHERE id=?',
                                     ('ready', instant.isoformat(), identifier))

    def records(self, root):
        return self.connection.execute('SELECT * FROM files WHERE root=?', (root,)).fetchall()

    def forget(self, identifier):
        with self.connection:
            self.connection.execute('DELETE FROM files WHERE id=?', (identifier,))

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
