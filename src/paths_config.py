"""Persistent folder settings, independent of the legacy BAT's text encoding."""
import json
import ntpath
import os
from pathlib import Path
import tempfile

FIELDS = (("Origem", "ORIGEM"), ("Destino 1", "DESTINO1"),
          ("Destino 2", "DESTINO2"), ("Destino 3", "DESTINO3"), ("Lixeira", "LIXEIRA"))
DESTINATION_KEYS = ("DESTINO1", "DESTINO2", "DESTINO3")


def overlaps(first, second):
    first, second = (ntpath.normcase(ntpath.normpath(p)) for p in (first, second))
    try:
        return ntpath.commonpath((first, second)) in (first, second)
    except ValueError:
        return False


def validate_paths(values, trash=None, require_trash=True):
    """Check syntax without requiring an offline share to be available."""
    result = {}
    for label, key in FIELDS:
        value = values.get(key, "")
        if not isinstance(value, str) or not value.strip():
            if key == "ORIGEM" or (key == "LIXEIRA" and require_trash) or not isinstance(value, str):
                raise ValueError(f"Preencha o campo {label}.")
            result[key] = ""
            continue
        value = value.strip().replace("/", "\\")
        # The legacy engine expands paths in CALL, ECHO and delayed-expansion blocks.
        # These characters cannot safely be passed through that engine.
        if any(c in value for c in '"%!^&()|<>') or any(ord(c) < 32 for c in value):
            raise ValueError(f"{label}: o motor não aceita os caracteres \" % ! ^ & ( ) | < > nos caminhos.")
        drive, tail = ntpath.splitdrive(value)
        unc = (drive.startswith("\\\\") and len(drive[2:].split("\\")) == 2
               and all(part and part not in (".", "..") and ":" not in part
                       for part in drive[2:].split("\\")))
        local = len(drive) == 2 and drive[0].isalpha() and drive[1] == ":" and tail.startswith("\\")
        if not (unc or local) or ":" in tail or "*" in value or "?" in value:
            raise ValueError(f"{label}: informe um caminho completo, como C:\\Pasta ou \\\\servidor\\compartilhamento.")
        value = ntpath.normpath(value)
        if any(part.endswith((".", " ")) for part in value.split("\\") if part):
            raise ValueError(f"{label}: os nomes das pastas não podem terminar com ponto ou espaço.")
        if local and value == drive + "\\":
            raise ValueError(f"{label}: selecione uma pasta dentro da unidade, em vez da unidade inteira.")
        result[key] = value
    if not any(result[key] for key in DESTINATION_KEYS):
        raise ValueError("Preencha pelo menos um destino.")
    pairs = [(label, key) for label, key in FIELDS if result[key]]
    for index, (label, key) in enumerate(pairs):
        for other_label, other_key in pairs[index + 1:]:
            if overlaps(result[key], result[other_key]):
                raise ValueError(f"{label} e {other_label} não podem ser iguais nem ficar um dentro do outro.")
        if trash and overlaps(result[key], trash):
            raise ValueError(f"{label} não pode ficar dentro da lixeira do motor nem conter essa lixeira.")
    return result


def load_paths(path, defaults):
    if not path.exists():
        return dict(defaults)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("formato inválido")
        # Keep existing source/destinations when upgrading the four-field format.
        # A missing trash folder is completed through the UI before a run or save.
        return validate_paths(values, require_trash=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Não foi possível ler {path.name}: {exc}. Corrija os campos e salve novamente.") from exc


def save_paths(path, values):
    """Commit all folder values together, keeping the previous file on failure."""
    data = json.dumps(values, ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="caminhos-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
