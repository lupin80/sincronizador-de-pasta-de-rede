"""Extended Windows paths for file operations, ordinary paths for display/keys."""
import os
from pathlib import Path


def ordinary_path(path):
    value = os.fspath(path)
    if value[:8].upper() == '\\\\?\\UNC\\':
        return '\\\\' + value[8:]
    if value.startswith('\\\\?\\'):
        return value[4:]
    return value


def file_path(path):
    """Normalize before adding the prefix; never save this prefix in settings."""
    value = os.fspath(path)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return Path(value)
    value = os.path.abspath(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def path_key(path):
    return os.path.normcase(os.path.normpath(ordinary_path(path)))


def notification_text(text):
    """NOTIFYICONDATA.szInfo holds 256 UTF-16 units including its terminator."""
    encoded = text.encode('utf-16-le')
    if len(encoded) <= 255 * 2:
        return text
    suffix = '…\nConsulte os logs para mais detalhes.'
    available = 255 - len(suffix.encode('utf-16-le')) // 2
    return encoded[:available * 2].decode('utf-16-le', errors='ignore') + suffix
