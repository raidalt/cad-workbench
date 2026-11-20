"""Guarded project source edits shared by chat, model replacement, orientation and hosted jobs."""
import hashlib
import json
import time
from pathlib import Path

from .errors import CadError
from .project import Store, atomic_bytes, read_overlay, write_overlay


def entry_name(root: Path) -> str:
    from .project import ProjectConfig

    try:
        return ProjectConfig.load(root).entry
    except Exception:
        return "model.py"


def apply_script(root: Path, source: str, *, name: str | None = None, clear_overrides: bool = False) -> dict:
    """Back up and write ``source`` as the project's model script (compile-checked)."""
    name = name or entry_name(root)
    try:
        compile(source, name, "exec")
    except SyntaxError as e:
        raise CadError("INVALID_ARGUMENT", f"the proposed script does not parse: {e.msg} (line {e.lineno})",
                       hint="ask for the change again; nothing was written") from e
    target = root / name
    backups = root / ".cad" / "backups"
    with Store(root).lock():
        backups.mkdir(parents=True, exist_ok=True)
        backup = None
        data = source.encode("utf-8")
        params = root / "params.toml"
        saved_params = params.read_bytes() if clear_overrides and read_overlay(root) else None
        existed = target.exists()
        if existed or saved_params is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
            backup = backups / f"{target.stem}-{stamp}{target.suffix}"
            previous = target.read_bytes() if existed else b""
            edit = {"entry": name, "existed": existed, "before": hashlib.sha256(previous).hexdigest(),
                    "after": hashlib.sha256(data).hexdigest()}
            record = backups / "receipts" / backup.name
            atomic_bytes(backup, previous)
            try:
                if saved_params is not None:
                    edit["params_before"] = saved_params.decode("utf-8")
                    write_overlay(root, {})
                    edit["params_after"] = hashlib.sha256(params.read_bytes()).hexdigest()
                atomic_bytes(record, json.dumps(edit).encode())
                atomic_bytes(target, data)
            except OSError:
                if saved_params is not None:
                    atomic_bytes(params, saved_params)
                record.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
                raise
        else:
            atomic_bytes(target, data)
    return {"file": name, "backup": str(backup) if backup else None, "bytes": len(data)}


def revert(root: Path, backup: str | None, *, name: str | None = None, created_source: str | None = None) -> bool:
    """Put a backup back (used when an applied script does not build)."""
    entry = name or entry_name(root)
    with Store(root).lock():
        if backup:
            return _restore_backup(root, entry, Path(backup))
        target = root / entry
        if created_source is not None and target.is_file() and target.read_bytes() == created_source.encode():
            target.unlink()
            return True
        return False


def _verified_backup(root: Path, entry: str, backup: Path):
    target = root / entry
    record = backup.parent / "receipts" / backup.name
    if not backup.is_file() or not target.is_file() or not record.is_file():
        return None
    try:
        edit = json.loads(record.read_bytes())
    except (UnicodeError, ValueError):
        return None
    if not isinstance(edit, dict):
        return None
    previous = backup.read_bytes()
    if (edit.get("entry") != entry or hashlib.sha256(target.read_bytes()).hexdigest() != edit.get("after")
            or hashlib.sha256(previous).hexdigest() != edit.get("before")):
        return None
    params = root / "params.toml"
    if "params_after" in edit:
        if not params.is_file() or hashlib.sha256(params.read_bytes()).hexdigest() != edit["params_after"]:
            return None
        if not isinstance(edit.get("params_before"), str):
            return None
    return edit, previous


def _restore_backup(root: Path, entry: str, backup: Path) -> bool:
    verified = _verified_backup(root, entry, backup)
    if verified is None:
        return False
    edit, previous = verified
    target = root / entry
    params = root / "params.toml"
    record = backup.parent / "receipts" / backup.name
    if "params_after" in edit:
        atomic_bytes(params, edit["params_before"].encode("utf-8"))
    if edit.get("existed", True):
        atomic_bytes(target, previous)
    else:
        target.unlink()
    backup.unlink()
    record.unlink()
    return True


def _latest_backup(root: Path, entry: str) -> Path | None:
    target = Path(entry)
    return max((root / ".cad" / "backups").glob(f"{target.stem}-*{target.suffix}"), default=None)


def undoable_file(root: Path) -> str | None:
    entry = entry_name(root)
    with Store(root).lock():
        latest = _latest_backup(root, entry)
        if latest and _verified_backup(root, entry, latest):
            return entry
    return None


def undo(root: Path) -> dict:
    """Restore the newest backup of the entry script."""
    entry = entry_name(root)
    with Store(root).lock():
        latest = _latest_backup(root, entry)
        if not latest:
            raise CadError("NOT_FOUND", "no chat edit to undo", hint="nothing has been applied in this project")
        if not _restore_backup(root, entry, latest):
            raise CadError("STALE_VIEW", "undo could not verify the current file against this edit",
                           hint=f"Compare the current file with the saved backup at {latest}.",
                           details={"backup": str(latest)})
        return {"restored": entry, "from": str(latest)}
