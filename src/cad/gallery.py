"""Local project library. Each open project keeps its own viewer process."""

from __future__ import annotations

import io
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode

from .chat import NEW_MODEL_TEMPLATE
from .errors import CadError
from .project import ProjectConfig, Store, _atomic_write, init_project

PREVIEW_VERSION = 2
GALLERY_PORT_ENV = "CAD_GALLERY_PORT"


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
        self.port = 0
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._children: dict[str, tuple[subprocess.Popen, str]] = {}

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
        from . import renderer
        from .engine import ProjectSession

        path = self.project_path(key)
        if not raw_revision or not raw_revision.isascii() or not raw_revision.isdigit() or len(raw_revision) > 10:
            raise CadError("NOT_FOUND", "No preview for this project.")
        rev = int(raw_revision)
        session = ProjectSession(path)
        cache = session.store.cache_dir / "gallery" / f"r{rev}-v{PREVIEW_VERSION}.png"
        with self._preview_lock:
            if cache.is_file():
                return cache.read_bytes()
            _, parts = session.parts(rev)
            if not parts:
                raise CadError("NOT_FOUND", "No preview for this project.")
            scene = renderer.build_scene(parts, deflection=.2)
            camera = renderer.parse_views("iso")[0]
            image, _ = renderer.render_one(scene, camera, size=(480, 320), annotate_images=False,
                                           supersample=1, background=(0.0, 0.0, 0.0), margin=.17)
            result = io.BytesIO()
            image.save(result, format="PNG")
            data = result.getvalue()
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
        if path == self.current:
            return "/"
        with self._lock:
            previous = self._children.get(key)
            if previous and previous[0].poll() is None:
                return previous[1]
            if previous:
                self._stop_process(previous[0])
            env = {**os.environ, GALLERY_PORT_ENV: str(self.port)}
            proc = subprocess.Popen(
                [sys.executable, "-m", "cad", "serve", "--json", "--no-open", "--port", "0",
                 "--project", str(path)],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, start_new_session=True,
            )
            try:
                assert proc.stdout is not None
                if not select.select([proc.stdout], [], [], 15)[0]:
                    raise TimeoutError("viewer did not start")
                report = json.loads(proc.stdout.readline())
                info = report["result"]
                if not report["ok"] or Path(info["root"]) != path:
                    raise ValueError("viewer did not start the requested project")
                url = f"http://127.0.0.1:{int(info['port'])}/"
                self._children[key] = (proc, url)
                return url
            except (KeyError, ValueError, OSError, TypeError) as error:
                self._stop_process(proc)
                raise CadError("INTERNAL", "Could not open this project. Try opening it again.") from error

    @staticmethod
    def _stop_process(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    def stop(self) -> None:
        with self._lock:
            for process, _ in self._children.values():
                self._stop_process(process)
            self._children.clear()
