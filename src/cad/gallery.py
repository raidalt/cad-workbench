"""Local project library. One origin with isolated workers for open projects."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote, urlencode

from .chat import NEW_MODEL_TEMPLATE
from .errors import CadError
from .project_worker import ProjectWorker
from .project import ProjectConfig, Store, _atomic_write, init_project

PREVIEW_VERSION = 2


def display_name(root: Path, fallback: str) -> str:
    try:
        return (root / ".cad/gallery-title").read_text().strip() or fallback
    except (OSError, UnicodeError):
        return fallback


def _name(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise CadError("INVALID_ARGUMENT", "Enter a project name up to 80 characters.")
    name = value.strip()
    if not name.isprintable():
        raise CadError("INVALID_ARGUMENT", "Use printable characters in the project name.")
    return name


class Gallery:
    def __init__(self, library: Path, current: Path) -> None:
        self.library = library.resolve()
        self.current = current
        if not self.library.is_dir() or current.parent != self.library:
            raise CadError("INVALID_ARGUMENT", "The served project must be directly inside the project library.")
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._children: dict[Path, ProjectWorker] = {}

    def project_path(self, key) -> Path:
        if not isinstance(key, str) or not key or key.startswith(".") or Path(key).name != key:
            raise CadError("NOT_FOUND", "Project not found.")
        path = self.library / key
        config = path / "cad.toml"
        if path.is_symlink() or config.is_symlink() or not config.is_file():
            raise CadError("NOT_FOUND", "Project not found.")
        return path

    def projects(self) -> list[dict]:
        projects = []
        for child in self.library.iterdir():
            try:
                path = self.project_path(child.name)
                config = ProjectConfig.load(path)
                store = Store(path)
                rev = store.head()
                manifest = store.manifest(rev) if rev is not None else {}
                modified = max((path / "cad.toml").stat().st_mtime,
                               (store.revision_dir(rev) / "manifest.json").stat().st_mtime if rev else 0)
                preview = "/api/projects/preview?" + urlencode({"key": child.name, "rev": rev, "v": PREVIEW_VERSION})
                projects.append({"key": child.name, "name": display_name(path, config.name),
                                 "revision": rev, "modified": modified,
                                 "preview": preview if manifest.get("parts") else None})
            except (CadError, OSError, ValueError, TypeError):
                continue
        return sorted(projects, key=lambda row: (-row["modified"], row["name"].casefold()))

    def create(self, value) -> str:
        name = _name(value)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "project"
        with self._lock:
            key = slug
            suffix = 2
            while (self.library / key).exists() or (self.library / key).is_symlink():
                key = f"{slug}-{suffix}"
                suffix += 1
            with tempfile.TemporaryDirectory(prefix=".new-cad-", dir=self.library) as scratch:
                project = Path(scratch) / "project"
                init_project(project, slug, NEW_MODEL_TEMPLATE)
                _atomic_write(project / ".cad/gallery-title", name)
                project.rename(self.library / key)
        return key

    def rename(self, key, value) -> None:
        _atomic_write(self.project_path(key) / ".cad/gallery-title", _name(value))

    def preview(self, key, raw_revision: str) -> bytes:
        path = self.project_path(key)
        if not raw_revision or not raw_revision.isascii() or not raw_revision.isdigit() or len(raw_revision) > 10:
            raise CadError("NOT_FOUND", "No preview for this project.")
        rev = int(raw_revision)
        cache = Store(path).cache_dir / "gallery" / f"r{rev}-v{PREVIEW_VERSION}.png"
        with self._preview_lock:
            if cache.is_file():
                return cache.read_bytes()
            code, data, _, _ = self.worker(path).request(
                "GET", "/api/preview", urlencode({"rev": rev}), None, "application/json")
            if code != 200:
                raise CadError("NOT_FOUND", "No preview for this project.")
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as temp:
                temp_path = Path(temp.name)
                try:
                    temp.write(data)
                    temp.close()
                    temp_path.replace(cache)
                finally:
                    temp_path.unlink(missing_ok=True)
            return data

    def open(self, key) -> str:
        path = self.project_path(key)
        return f"/projects/{quote(path.name, safe='')}/"

    def worker(self, path: Path) -> ProjectWorker:
        with self._lock:
            for key, worker in list(self._children.items()):
                if worker.process.poll() is not None:
                    worker.stop()
                    del self._children[key]
            if path not in self._children:
                self._children[path] = ProjectWorker(path)
            return self._children[path]

    def stop(self) -> None:
        with self._lock:
            for worker in self._children.values():
                worker.stop()
            self._children.clear()
