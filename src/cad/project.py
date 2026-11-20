"""Project layout, parameter overlay and immutable revision storage.

Layout::

    my-part/
      cad.toml            project config (source of truth, commit to git)
      model.py            entry script (source of truth)
      params.toml         parameter overlay written by `cad set` (source of truth)
      .cad/               computational state (safe to delete, ignored by git)
        HEAD              active revision number
        lock              build/publish lock
        last_build.json   result of the most recent build attempt (success or failure)
        revisions/<N>/    immutable successful revisions:
          manifest.json   inputs, hashes, parameters, part summaries
          parts/<name>.brep, parts/<name>.json (feature graph + named refs)
          source/         snapshot of the sources used (for `cad restore`)
        cache/meshes/     tessellations keyed by geometry hash + tolerance
        cache/renders/    rendered images keyed by geometry + camera + settings

Revisions are published atomically: they are written to a temporary directory
and renamed into place, so readers never observe a partial revision.  A failed
build never touches HEAD or existing revisions.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .errors import INVALID_ARGUMENT, CadError

CONFIG_NAME = "cad.toml"
PARAMS_NAME = "params.toml"
STATE_DIR = ".cad"
from .errors import NOT_FOUND  # noqa: E402,F401  (re-exported)
GEOMETRY_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl", ".ttf", ".otf")
SOURCE_SUFFIXES = (".py", ".toml", ".json", ".csv", ".txt", ".dxf", ".svg") + GEOMETRY_SUFFIXES
IGNORED_DIRS = {STATE_DIR, ".git", "renders", "out", "__pycache__", ".venv", "node_modules"}


@dataclass
class ProjectConfig:
    name: str
    entry: str
    views: list[str]
    size: tuple[int, int]
    export_formats: list[str]
    raw: dict
    threemf_mode: str = "objects"
    threemf_for: str = "any"

    @staticmethod
    def load(root: Path, *, default_name: str | None = None) -> "ProjectConfig":
        p = root / CONFIG_NAME
        raw: dict = {}
        if p.exists():
            try:
                raw = tomllib.loads(p.read_text())
            except tomllib.TOMLDecodeError as e:
                raise CadError(INVALID_ARGUMENT, f"{p} is not valid TOML: {e}") from e
        proj = raw.get("project", {})
        render = raw.get("render", {})
        export = raw.get("export", {})
        size = render.get("size", [1024, 768])
        return ProjectConfig(
            name=proj.get("name", default_name or root.name),
            entry=proj.get("entry", "model.py"),
            views=list(render.get("views", ["iso", "front", "right", "top"])),
            size=(int(size[0]), int(size[1])),
            export_formats=list(export.get("formats", ["step", "stl"])),
            raw=raw,
            threemf_mode=str(export.get("threemf_mode", "objects")),
            threemf_for=str(export.get("threemf_for", "any")),
        )


def find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for cad.toml."""
    p = start.resolve()
    if p.is_file():
        p = p.parent
    for d in [p, *p.parents]:
        if (d / CONFIG_NAME).is_file():
            return d
    return None


def init_project(root: Path, name: str | None = None, template: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / CONFIG_NAME
    if cfg.exists():
        raise CadError(INVALID_ARGUMENT, f"{cfg} already exists")
    name = name or root.resolve().name
    cfg.write_text(
        f'[project]\nname = "{name}"\nentry = "model.py"\n\n'
        '[render]\nviews = ["iso", "front", "right", "top"]\nsize = [1024, 768]\n\n'
        '[export]\nformats = ["step", "stl"]\n'
    )
    model = root / "model.py"
    if not model.exists():
        model.write_text(template or DEFAULT_TEMPLATE.replace("{name}", name))
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(".cad/\nrenders/\nout/\n")
    return root


DEFAULT_TEMPLATE = '''"""{name}: describe the part here.

Build with `cad build`, check with `cad inspect`, look with `cad render`.
API reference: `cad docs`.  Worked examples: `cad examples list`, `cad examples show <name>`.
"""
from cad import Part, Parameter

size = Parameter("size", 20, min=1, max=1000)


def build():
    part = Part("{name}")
    part.box(size, size, size, name="body")   # centred in X/Y, bottom face on Z = 0
    part.assert_valid()
    return part
'''


# --------------------------------------------------------------------------- params overlay


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))


def read_overlay(root: Path) -> dict[str, Any]:
    p = root / PARAMS_NAME
    if not p.exists():
        return {}
    data = tomllib.loads(p.read_text())
    flat: dict[str, Any] = {}

    def walk(prefix, d):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(key, v)
            else:
                flat[key] = v

    walk("", data.get("parameters", data))
    return flat


def write_overlay(root: Path, values: dict[str, Any]) -> None:
    lines = ["# Parameter overrides (written by `cad set`). Part of the design's source of truth.", "[parameters]"]
    for k in sorted(values):
        lines.append(f"{json.dumps(k)} = {_toml_value(values[k])}")
    _atomic_write(root / PARAMS_NAME, "\n".join(lines) + "\n")


def parse_value(text: str) -> Any:
    t = text.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


# --------------------------------------------------------------------------- storage


def _atomic_write(path: Path, text: str) -> None:
    atomic_bytes(path, text.encode("utf-8"))


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if path.is_file():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def hash_json(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class Store:
    """On-disk state of one project (.cad directory)."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.dir = self.root / STATE_DIR
        self.revisions_dir = self.dir / "revisions"
        self.cache_dir = self.dir / "cache"

    # ---------------------------------------------------------------- sources
    def source_files(self, *, include_overlay: bool = False) -> list[Path]:
        """Inputs shared by the watcher, build key, revision and candidate snapshot."""
        declared = ProjectConfig.load(self.root).raw.get("project", {}).get("inputs", [])
        if not isinstance(declared, list) or not all(isinstance(v, str) for v in declared):
            raise CadError(INVALID_ARGUMENT, "project.inputs must be a list of relative file paths")
        files = set()

        def visit(directory: Path, ancestors: set[Path]) -> None:
            resolved = directory.resolve()
            if resolved in ancestors:
                raise CadError(INVALID_ARGUMENT, f"recursive project directory symlink: {directory}")
            for path in directory.iterdir():
                if path.name in IGNORED_DIRS or (path.name.startswith(".") and
                        (path.is_dir() or not path.name.lower().endswith(SOURCE_SUFFIXES))):
                    continue
                if path.is_dir():
                    visit(path, ancestors | {resolved})
                elif path.is_file():
                    files.add(path)
                elif path.is_symlink():
                    raise CadError(INVALID_ARGUMENT, f"missing project input: {path}")

        visit(self.root, set())
        for name in declared:
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] == STATE_DIR:
                raise CadError(INVALID_ARGUMENT, f"invalid project input: {name}")
            path = self.root / rel
            if not path.is_file():
                raise CadError(INVALID_ARGUMENT, f"missing project input: {name}")
            files.add(path)
        if not include_overlay:
            files.discard(self.root / PARAMS_NAME)
        return sorted(files)

    def snapshot_file(self, src: Path, dst: Path, digest: str) -> None:
        """Copy a source file into a revision; large files (imported geometry) are stored once
        under cache/blobs by content hash and hard-linked, so revisions do not multiply them."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.stat().st_size < 256 * 1024:
            shutil.copy2(src, dst)
            self._verify_copy(dst, digest)
            return
        blob = self.cache_dir / "blobs" / digest
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            tmp = blob.with_suffix(".tmp")
            try:
                shutil.copyfile(src, tmp)
                self._verify_copy(tmp, digest)
                os.replace(tmp, blob)
            finally:
                tmp.unlink(missing_ok=True)
        try:
            os.link(blob, dst)
        except OSError:
            shutil.copyfile(blob, dst)

    @staticmethod
    def _verify_copy(path: Path, expected: str) -> None:
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise CadError("STALE_SOURCE", "source file changed while saving the revision",
                               hint="rebuild the current project")

    def source_hash(self) -> tuple[str, dict[str, str]]:
        per = {}
        for f in self.source_files():
            per[str(f.relative_to(self.root))] = hashlib.sha256(f.read_bytes()).hexdigest()
        return hash_json(per), per

    # ---------------------------------------------------------------- locking
    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # ---------------------------------------------------------------- revisions
    def revision_numbers(self) -> list[int]:
        if not self.revisions_dir.exists():
            return []
        return sorted(int(p.name) for p in self.revisions_dir.iterdir() if p.name.isdigit() and (p / "manifest.json").exists())

    def head(self) -> int | None:
        p = self.dir / "HEAD"
        if p.exists():
            t = p.read_text().strip()
            if t.isdigit() and (self.revisions_dir / t / "manifest.json").exists():
                return int(t)
        nums = self.revision_numbers()
        return nums[-1] if nums else None

    def set_head(self, rev: int) -> None:
        self.manifest(rev)  # validates existence
        _atomic_write(self.dir / "HEAD", f"{rev}\n")

    def revision_dir(self, rev: int) -> Path:
        return self.revisions_dir / str(rev)

    def manifest(self, rev: int) -> dict:
        p = self.revision_dir(rev) / "manifest.json"
        if not p.exists():
            raise CadError(NOT_FOUND, f"revision {rev} does not exist", hint="list revisions with `cad revisions`",
                           details={"available": self.revision_numbers()[-20:]})
        return json.loads(p.read_text())

    def find_by_key(self, key: str) -> int | None:
        for rev in reversed(self.revision_numbers()):
            try:
                if self.manifest(rev).get("key") == key:
                    return rev
            except (CadError, json.JSONDecodeError):
                continue
        return None

    def publish(self, write_contents) -> int:
        """Atomically create the next revision.  ``write_contents(tmpdir, rev)`` fills it."""
        self.revisions_dir.mkdir(parents=True, exist_ok=True)
        with self.lock():
            nums = self.revision_numbers()
            rev = (nums[-1] + 1) if nums else 1
            tmp = Path(tempfile.mkdtemp(dir=self.revisions_dir, prefix=".tmp-"))
            try:
                write_contents(tmp, rev)
                os.rename(tmp, self.revision_dir(rev))
            except BaseException:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            _atomic_write(self.dir / "HEAD", f"{rev}\n")
        return rev

    def write_last_build(self, record: dict) -> None:
        record = dict(record, at=now_iso())
        _atomic_write(self.dir / "last_build.json", json.dumps(record, indent=2, default=str))

    def last_build(self) -> dict | None:
        p = self.dir / "last_build.json"
        return json.loads(p.read_text()) if p.exists() else None


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
