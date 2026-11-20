"""``cad serve``: a live 3D viewport for a project, in the browser.

One process, no new dependencies.  It runs the engine in-process (so a rebuild
is the same ``~50 ms`` as ``cad build``), watches the project's sources, and
streams three things to the page over Server-Sent Events:

- the build lifecycle (``build_start`` / ``feature`` / ``frame`` / ``build_ok`` / ``build_error``),
- a preview mesh per feature, so the timeline can replay the part growing,
- progress for stress analysis.

Geometry bodies are fetched as binary (``/api/mesh``, ``/api/frame``) instead of
base64 in the event stream.  The page is plain ES modules from ``cad/web`` with
three.js vendored next to it, so it works offline.

Why not the Rust daemon: a viewer wants events *during* a build, and the daemon
protocol is one request in, one result out.  Running the engine here also keeps
the live callback in-process, which is what makes frames free.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import signal
import struct
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np

from . import __version__, live
from .engine import ProjectSession
from .errors import CadError
from .gallery import Gallery, display_name

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
                ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".map": "application/json",
                ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".json": "application/json",
                ".webmanifest": "application/manifest+json"}


# --------------------------------------------------------------------------- events


class Hub:
    """Fan out events to every connected page (one queue per connection)."""

    def __init__(self) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self.last: dict | None = None
        self.error_events = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> None:
        self.last = event
        with self._lock:
            subs = list(self._subs)
        if event.get("t") == "build_error":
            self.error_events += 1
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # a stalled page: drop the oldest, keep the newest
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass


class Frames:
    """Preview meshes of the build in flight (and the last one, for scrubbing).

    A new build starts a fresh set.  If it produces no frames (a cached build
    never runs the script), the previous set is restored, so the timeline does
    not vanish on a save that changed nothing.
    """

    def __init__(self) -> None:
        self._parts: dict[str, dict[int, dict]] = {}
        self._prev: dict[str, dict[int, dict]] = {}
        self._revision: int | None = None
        self._lock = threading.Lock()

    def begin(self) -> None:
        with self._lock:
            self._prev, self._parts = self._parts, {}

    def set_revision(self, rev) -> None:
        with self._lock:
            self._revision = rev

    def revision(self) -> int | None:
        with self._lock:
            return self._revision

    def settle(self) -> None:
        """After a build: keep the new frames, or fall back to the previous set.

        ``timeline``/``get`` already fall back to ``_prev`` while a rebuild has
        produced nothing yet, so no reader ever sees a blank timeline.
        """
        with self._lock:
            if not self._prev:
                return
            if any(self._parts.values()):
                self._prev = {}
            else:
                self._parts, self._prev = self._prev, {}

    def abort(self) -> None:
        """A build failed: its partial frames are not the model on screen."""
        with self._lock:
            self._parts, self._prev = self._prev or {}, {}

    def put(self, part: str, index: int, info: dict) -> None:
        with self._lock:
            frames = self._parts.setdefault(part, {})
            frames[index] = info
            if len(frames) > 2 * live.FRAME_MAX:  # thin the middle, keep first and last
                keys = sorted(frames)
                for k in keys[1:-1:2]:
                    frames.pop(k, None)

    def get(self, part: str, index: int) -> dict | None:
        with self._lock:
            frames = self._parts.get(part) or self._prev.get(part) or {}
            return frames.get(index)

    def timeline(self, part: str, revision=None) -> list[dict]:
        with self._lock:
            if revision is not None and self._revision is not None and revision != self._revision:
                return []  # these frames belong to another revision
            frames = self._parts.get(part) or self._prev.get(part) or {}
            return [{"index": i, **{k: v for k, v in d.items() if k != "mesh"}}
                    for i, d in sorted(frames.items())]

# --------------------------------------------------------------------------- geometry


def encode_mesh(mesh, colors=None, meta=None) -> bytes:
    """Binary payload: JSON header, then float32/uint32 arrays (little-endian)."""
    v = np.ascontiguousarray(mesh.vertices, dtype="<f4")
    n = np.ascontiguousarray(mesh.normals, dtype="<f4")
    t = np.ascontiguousarray(mesh.triangles, dtype="<u4")
    f = np.ascontiguousarray(mesh.face_ids, dtype="<i4")
    lens = np.array([len(e) for e in mesh.edges], dtype="<u4")
    pts = np.concatenate(mesh.edges) if mesh.edges else np.zeros((0, 3))
    pts = np.ascontiguousarray(pts, dtype="<f4")
    c = np.zeros((0, 3), dtype="<f4") if colors is None else np.ascontiguousarray(colors, dtype="<f4")
    head = {"vertices": len(v), "triangles": len(t), "edges": len(lens), "edge_points": len(pts),
            "colors": len(c), "deflection": mesh.linear_deflection,
            # ordered layout after the header, so a client need not hardcode it
            "arrays": [["vertices", "f4", 3], ["normals", "f4", 3], ["indices", "u4", 3], ["face_ids", "i4", 1],
                       ["edge_lens", "u4", 1], ["edge_points", "f4", 3], ["colors", "f4", 3]]}
    if meta:
        head["meta"] = meta
    hb = json.dumps(head).encode()
    return b"".join((struct.pack("<I", len(hb)), hb, v.tobytes(), n.tobytes(), t.tobytes(), f.tobytes(),
                     lens.tobytes(), pts.tobytes(), c.tobytes()))


def face_table(part, mesh) -> list[dict]:
    """Per-face area, centroid and named references, in ``face_ids`` order.

    Only faces the mesh actually uses are measured (a filleted part can carry
    faces the preview never references), and each face is integrated once.
    """
    from . import _occ

    used = sorted({int(x) for x in np.unique(mesh.face_ids)}) if len(mesh.face_ids) else []
    faces = _occ.faces(part.shape)
    out = []
    for i in used:
        if i >= len(faces):
            continue
        face = faces[i]
        area, center = _occ.surface_props(face)
        out.append({"id": i, "area": round(area, 4), "center": [round(c, 4) for c in center],
                    "refs": part._names_of(face, "face")})
    return out


# --------------------------------------------------------------------------- builder


class Builder:
    """Owns the project session and serialises builds on one background thread."""

    def __init__(self, session: ProjectSession, hub: Hub, frames: Frames) -> None:
        self.session = session
        self.hub = hub
        self.frames = frames
        self.model_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self.watcher: "Watcher | None" = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._overrides: dict | None = None
        self._force = False
        self._thread = threading.Thread(target=self._loop, name="cad-builds", daemon=True)
        self.building = False
        self.last_result: dict | None = None
        self.last_error: dict | None = None
        self.duration_ms: float | None = None
        self._created: dict[int, str | None] = {}
        self._capabilities_cache: dict | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request(self, overrides: dict | None = None, *, force: bool = False) -> None:
        with self._request_lock:
            if overrides:
                self._overrides = {**(self._overrides or {}), **overrides}
            self._force = self._force or force
            self.building = True  # a queued rebuild is already "building" to anyone polling state
            self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            if self._stop.is_set():
                break
            if not self._wake.is_set():
                continue
            with self.model_lock:
                with self._request_lock:
                    self._wake.clear()
                    overrides, force = self._overrides, self._force
                    self._overrides, self._force = None, False
                self._build(overrides, force)

    def _build(self, overrides: dict | None, force: bool) -> None:
        self.building = True
        self.frames.begin()
        errors_before = self.hub.error_events
        self.last_error = None
        t0 = time.perf_counter()
        try:
            with self.model_lock:
                result = self.session.build(overrides, force=force)
            self.last_result = result
            self.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self.publish_state(reason="build" if not result.get("cached") else "cached",
                               revision=result["revision"])
        except CadError as e:
            self.last_error = e.to_dict()
        except Exception as e:  # keep the viewer alive on an internal error
            self.last_error = {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}",
                               "details": {"traceback": traceback.format_exc()[-4000:]}}
        finally:
            if self.last_error:
                self.frames.abort()
            else:
                self.frames.settle()
            with self._request_lock:
                self.building = self._wake.is_set()
        if self.last_error and self.hub.error_events == errors_before:  # engine already reported it
            self.hub.publish({"t": "build_error", "error": self.last_error, "ts": time.time(),
                              "duration_ms": round((time.perf_counter() - t0) * 1000, 2)})

    # ------------------------------------------------------------------ state
    def state(self, revision=None) -> dict:
        # read-only w.r.t. the engine (store + frame metadata), so it must not
        # wait behind a build or a stress solve
        return self._state(revision)

    def _state(self, revision=None) -> dict:
        store = self.session.store
        cfg = self.session.config
        head = store.head()
        rev = self.session.resolve_rev(revision) if (revision or head) else None
        man = store.manifest(rev) if rev else {}
        parts: dict[str, dict] = {}
        for i, (name, info) in enumerate((man.get("parts") or {}).items()):
            parts[name] = {
                "summary": info.get("summary"),
                "color": info.get("color"),
                "reference": info.get("reference", False),
                "material": info.get("material"),
                "print_down": info.get("print_down"),
                "features": self._features(rev, name),
                "timeline": self.frames.timeline(name, rev),
            }
        lb = store.last_build() or {}
        numbers = store.revision_numbers()[-40:] if head else []
        deliverable = [n for n, info in parts.items() if not info.get("reference")]
        return {
            "project": cfg.name,
            "root": str(self.session.root),
            "entry": cfg.entry,
            "version": __version__,
            "revision": rev,
            "head": head,
            "empty": not deliverable,
            "revisions": [{"n": n, "at": self._created_at(n), "head": n == head} for n in numbers],
            "parts": parts,
            "parameters": man.get("parameters", {}),
            "overrides": man.get("overrides", {}),
            "joints": man.get("joints", []),
            "studies": man.get("studies", []),
            "warnings": (man.get("build") or {}).get("warnings", []),
            "stdout": (man.get("build") or {}).get("stdout", "")[-4000:],
            "build": {"duration_ms": self.duration_ms, "status": lb.get("status"), "cached": lb.get("cached")},
            "error": self.last_error,
            "building": self.building,
            "last_build": lb,
            "capabilities": self._capabilities(),
        }

    def _created_at(self, rev: int) -> str | None:
        cached = self._created.get(rev)
        if cached is None:
            try:
                cached = self.session.store.manifest(rev).get("created_at")
            except Exception:
                cached = None
            self._created[rev] = cached
        return cached

    def _features(self, rev: int, name: str) -> list[dict]:
        p = self.session.store.revision_dir(rev) / "parts" / f"{name}.json"
        if not p.exists():
            return []
        try:
            graph = json.loads(p.read_text())
        except Exception:
            return []
        out = []
        for f in graph.get("features", []):
            out.append({"id": f.get("id"), "type": f.get("type"), "status": f.get("status"),
                        "ms": round(f.get("duration_ms") or 0, 1), "metrics": f.get("metrics") or {},
                        "error": f.get("error"), "warnings": f.get("warnings") or []})
        return out

    def _capabilities(self) -> dict:
        if self._capabilities_cache is None:
            from . import fea, printability
            from .materials import library

            try:
                fea.require()
                ok, why, hint = True, None, None
            except CadError as e:
                ok, why, hint = False, e.message, e.hint
            self._capabilities_cache = {"fea": ok, "fea_unavailable": why, "fea_install_hint": hint,
                                        "beds": list(printability.BEDS), "default_bed": list(printability.DEFAULT_BED),
                                        "materials": [m["name"] for m in library()]}
        return self._capabilities_cache

    def publish_state(self, **info) -> None:
        self.hub.publish({"t": "state", "info": info, "ts": time.time()})

    # ------------------------------------------------------------------ geometry
    def mesh_bytes(self, part: str | None, revision=None) -> bytes:
        with self.model_lock:
            r, parts = self.session.parts(revision)
            if not parts:
                raise CadError("NOT_FOUND", "this revision has no parts")
            if part is None:
                if len(parts) != 1:
                    raise CadError("INVALID_ARGUMENT", f"revision {r} has {len(parts)} parts; name one")
                part = next(iter(parts))
            if part not in parts:
                raise CadError("NOT_FOUND", f"no part named {part!r}")
            p = parts[part]
            return encode_mesh(self.session.mesh(r, part, p))

    def faces(self, part: str, revision=None) -> list[dict]:
        with self.model_lock:
            r, parts = self.session.parts(revision)
            if not parts:
                raise CadError("NOT_FOUND", "this revision has no parts")
            if part not in parts:
                raise CadError("NOT_FOUND", f"no part named {part!r}")
            return face_table(parts[part], self.session.mesh(r, part, parts[part]))

    # ------------------------------------------------------------------ export
    def export_bytes(self, fmt: str, part: str | None = None, revision=None, *, mode=None) -> tuple[bytes, str, str]:
        """(bytes, filename, content type) for a one-click download."""
        import tempfile

        from .export import export_shape

        fmt = (fmt or "stl").lower()
        if fmt not in ("stl", "step", "3mf"):
            raise CadError("INVALID_ARGUMENT", f"cannot export {fmt!r}", hint="use stl, step or 3mf")
        with self.model_lock:
            r, parts = self.session.parts(revision)
            if not parts:
                raise CadError("NOT_FOUND", f"revision {r} has no parts",
                               hint="add geometry to the script, or choose a revision that has parts")
            deliver = {n: p for n, p in parts.items() if not p.reference} or dict(parts)
            if part is not None:
                if part not in parts:
                    raise CadError("NOT_FOUND", f"no part named {part!r}", details={"parts": list(parts)})
                deliver = {part: parts[part]}
            elif len(deliver) > 1 and fmt != "3mf":
                raise CadError("INVALID_ARGUMENT", f"revision {r} has {len(deliver)} parts; name one",
                               details={"parts": list(deliver)})
            name = self.session.config.name
            stem = f"{name}-r{r}-" + ("-".join(deliver) if len(deliver) > 1 else next(iter(deliver)))
            cfg = self.session.revision_config(r)
            with tempfile.TemporaryDirectory() as td:
                if fmt == "3mf":
                    from .threemf import export_3mf

                    path = Path(td) / f"{stem}.3mf"
                    export_3mf(deliver, path, mode=mode or cfg.threemf_mode,
                               target=getattr(cfg, "threemf_for", None) or "any", title=stem)
                    ctype = "model/3mf"
                else:
                    (pname, p) = next(iter(deliver.items()))
                    path = Path(td) / f"{stem}.{fmt}"
                    # print orientation for STL, like `cad export`
                    shape = p.as_printed().shape if (fmt == "stl" and getattr(p, "print_down", None)) else p.shape
                    export_shape(shape, path, name=pname)
                    ctype = "model/stl" if fmt == "stl" else "application/step"
                return path.read_bytes(), path.name, ctype

    # ------------------------------------------------------------------ chat context
    def chat_context(self, viewing=None) -> str:
        """What the project looks like right now, for the chat system prompt.

        ``viewing`` is the revision the person has open in the viewport, when it
        differs from head: the model has to know that what they see is not what
        the script will build.

        Everything a person can see in the viewport: build result and error,
        warnings, part sizes, parameters, print readiness.  Cheap on repeat calls
        (print reports are cached by geometry hash).
        """
        from . import chat

        store = self.session.store
        state: dict = {}
        with self.model_lock:
            head = store.head()
            state["revision"] = head
            if viewing is not None:
                state["viewing"] = viewing
            lb = store.last_build() or {}
            err = self.last_error or (lb.get("error") if lb.get("status") == "failed" else None)
            if err:
                state["error"] = err
            if head is not None:
                try:
                    m = store.manifest(head)
                except Exception:
                    m = {}
                build = m.get("build") or {}
                state["build"] = {"duration_ms": build.get("duration_ms")}
                state["warnings"] = build.get("warnings", [])
                state["parts"] = [
                    {"name": n, "valid": (p.get("summary") or {}).get("valid"),
                     "volume": (p.get("summary") or {}).get("volume"),
                     "bbox": (p.get("summary") or {}).get("bbox"),
                     "holes": (p.get("summary") or {}).get("holes"),
                     "material": (p.get("material") or {}).get("name") if isinstance(p.get("material"), dict) else None,
                     "reference": p.get("reference", False)}
                    for n, p in (m.get("parts") or {}).items()
                ]
                overrides = m.get("overrides") or {}
                state["parameters"] = {
                    k: {"value": v.get("value"), "default": v.get("default"),
                        "overridden": k in overrides or bool(v.get("overridden"))}
                    for k, v in (m.get("parameters") or {}).items()
                }
                state["joints"] = m.get("joints") or []
                state["studies"] = [s.get("name") for s in (m.get("studies") or []) if isinstance(s, dict)]
                try:
                    from .project import read_overlay

                    current, _ = store.source_hash()
                    if current != m.get("source_hash"):
                        state["dirty"] = True
                    overlay = read_overlay(self.session.root)
                    for k, v in overlay.items():
                        if k in state.get("parameters", {}):
                            state["parameters"][k]["value"] = v
                            state["parameters"][k]["overridden"] = True
                except Exception:
                    pass
                try:
                    every = self.session.print_report_all()
                    reports = every.get("parts") or {}
                    state["print"] = [
                        {"part": n, "mass_g": r.get("mass_g"),
                         "fits": (r.get("bed") or {}).get("fits"),
                         "thin_min": (r.get("thin") or {}).get("min_mm"),
                         "thin_count": (r.get("thin") or {}).get("count"),
                         "overhang_area": (r.get("overhang") or {}).get("area_mm2"),
                         "print_down": (r.get("geometry") or {}).get("print_down"),
                         "as_modelled": (r.get("orientation") or {}).get("as_modelled")}
                        for n, r in reports.items()
                    ]
                    first = next(iter(reports.values()), {})
                    state["material"] = (first.get("material") or {}).get("name")
                    bed = (first.get("bed") or {}).get("size_mm")
                    if bed:
                        state["bed"] = " x ".join(f"{v:g}" for v in bed)
                except Exception:
                    pass
        return chat.format_context(state)
    def verify_apply(self) -> dict:
        """Build whatever is on disk now (a chat edit must not leave a broken script).

        The engine's normal build path is used, so re-verifying a script that
        already has a revision returns that revision instead of publishing a
        duplicate (a retry of a successful turn must not change the project).
        """
        try:
            with self.model_lock:
                result = self.session.build(None)
            return {"ok": True, "revision": result["revision"]}
        except CadError as e:
            return {"ok": False, "error": e.to_dict()}

    def prepare_chat(self, selection, viewing):
        """Capture the source and geometry a turn will edit."""
        from .project import read_overlay
        from .selection_context import resolve_selection

        with self.model_lock:
            self._guard_viewing(viewing)
            target = resolve_selection(self, selection, viewing)
            head = self.session.store.head()
            source_hash, _ = self.session.store.source_hash()
            overrides = read_overlay(self.session.root)
        return target, lambda script: self.commit_chat(script, head, source_hash, overrides)

    def commit_chat(self, script: str, head: int | None, source_hash: str, overrides: dict) -> dict:
        """Publish a checked candidate only if its starting project is current."""
        from . import chat
        from .project import read_overlay

        store = self.session.store
        with self.model_lock:
            current = store.head()
            project = self.session.root
            same_revision = current == head
            if not same_revision and current is not None:
                latest = store.manifest(current)
                previous = store.manifest(head) if head is not None else None
                # The initial empty build and repeated builds of the same blank
                # source do not change what an in-flight first edit is based on.
                same_revision = (
                    not latest["parts"] and latest["source_hash"] == source_hash
                    and latest["overrides"] == overrides
                    and (previous is None or (not previous["parts"] and previous["key"] == latest["key"]))
                )
            with self._request_lock:
                pending_overrides = bool(self._overrides)
            if (pending_overrides or not same_revision or store.source_hash()[0] != source_hash
                    or read_overlay(project) != overrides):
                error = CadError(
                    "STALE_VIEW",
                    "the project changed while chat was preparing this edit",
                    hint="review the current model and send the request again",
                    details={"active_revision": current},
                )
                return {"ok": False, "error": error.to_dict()}

            entry = self.session.config.entry
            path = project / entry
            last_build = store.last_build()
            existed = path.exists()
            if existed and path.read_text() == script:
                info = {"file": entry, "backup": None, "bytes": len(script)}
            else:
                info = chat.apply_script(project, script, name=entry)
            self.frames.begin()
            try:
                result = self.session.build(None)
            except Exception as exc:
                restored = chat.revert(project, info.get("backup"), name=entry, created_source=None if existed else script)
                if restored and last_build is not None:
                    store.write_last_build(last_build)
                self.frames.abort()
                error = exc.to_dict() if isinstance(exc, CadError) else {
                    "code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}",
                }
                error.setdefault("details", {}).setdefault("active_revision", current)
                return {"ok": False, "error": error, "reverted": restored}
            finally:
                if self.watcher is not None:
                    self.watcher.accept()

            self.frames.settle()
            self.last_error = None
            self.last_result = result
            self.duration_ms = result["duration_ms"]
            self.publish_state(reason="chat", revision=result["revision"])
            return {"ok": True, "info": info, "revision": result["revision"]}

    # ------------------------------------------------------------------ new model / orientation
    def new_model(self, source: str | None = None) -> dict:
        """Replace the entry script with ``source`` (or the empty starter) and build it."""
        from . import chat
        from .project import read_overlay

        source = chat.NEW_MODEL_TEMPLATE if source is None else source
        if not isinstance(source, str) or not source.strip():
            raise CadError("INVALID_ARGUMENT", "source must be a non-empty string of Python",
                           hint="omit source to use the empty starter model")
        with self.model_lock:
            # a new model must not inherit the old model's parameter values
            stale = read_overlay(self.session.root)
            store = self.session.store
            head = store.head()
            path = self.session.root / self.session.config.entry
            if (not stale and head is not None and path.is_file() and path.read_text() == source):
                manifest = store.manifest(head)
                if (not manifest["parts"] and not manifest["overrides"]
                        and manifest["source_hash"] == store.source_hash()[0]):
                    return {"ok": True, "revision": head, "backup": None, "cleared_overrides": []}
            existed = path.exists()
            info = chat.apply_script(self.session.root, source, clear_overrides=True)
            try:
                result = self.session.build(None, force=True)
            except Exception:
                chat.revert(self.session.root, info.get("backup"), created_source=None if existed else source)
                if self.watcher is not None:
                    self.watcher.accept()
                raise
            self.last_error = None
            if self.watcher is not None:
                self.watcher.accept()  # our own write is not an edit to rebuild
            self.publish_state(reason="new-model", revision=result["revision"])
            return {"ok": True, "revision": result["revision"], "backup": info.get("backup"),
                    "cleared_overrides": sorted(stale) if stale else []}

    def orient(self, part: str | None, ref: str | None, viewing=None) -> dict:
        """Lay ``part`` on named face ``ref`` by inserting ``print_on`` into build()."""
        from . import chat

        self._guard_viewing(viewing)

        if not isinstance(part, str) or not part.strip():
            raise CadError("INVALID_ARGUMENT", "part is required",
                           hint="name the part to lay on the bed, e.g. {\"part\": \"plate\", \"ref\": \"base.top\"}")
        if not isinstance(ref, str) or not ref.strip():
            raise CadError("INVALID_ARGUMENT", "ref is required",
                           hint='name a face reference such as "base.top" (`cad inspect --refs` lists them)')
        part, ref = part.strip(), ref.strip()
        with self.model_lock:
            entry = chat.entry_name(self.session.root)
            path = self.session.root / entry
            if not path.exists():
                raise CadError("NOT_FOUND", f"entry script {entry} not found")
            new_source, changed = chat.orient_source(path.read_text(), part, ref)
            if not changed:
                return {"ok": True, "revision": self.session.store.head(), "changed": False}
            info = chat.apply_script(self.session.root, new_source)
            verdict = self.verify_apply()
            if not verdict.get("ok"):
                restored = chat.revert(self.session.root, info.get("backup"))
                if self.watcher is not None:
                    self.watcher.accept()
                return {"ok": False, "error": verdict.get("error") or {}, "reverted": restored}
            self.last_error = None
            if self.watcher is not None:
                self.watcher.accept()
            self.publish_state(reason="orient", revision=verdict.get("revision"))
            return {"ok": True, "revision": verdict.get("revision"), "backup": info.get("backup"), "changed": True}

    def _guard_viewing(self, viewing) -> None:
        """Refuse writes while the person is looking at an older revision.

        History browsing is read-only: the script on disk belongs to the head
        revision, so an edit made from a snapshot would change a different source
        than the one on screen.
        """
        if viewing is None or viewing == "":
            return
        try:
            seen = int(viewing)
        except (TypeError, ValueError):
            return
        head = self.session.store.head()
        if head is not None and seen != head:
            raise CadError("STALE_VIEW",
                           f"you are viewing r{seen}, but the current revision is r{head}",
                           hint="return to the current revision to make changes; browsing history is read-only")

    # ------------------------------------------------------------------ print readiness
    def print_report(self, part: str | None = None, revision=None, material: str | None = None, bed=None,
                     angle: float = 45.0, nozzle: float = 0.4, min_wall: float | None = None,
                     per_face: int = 3) -> dict:
        with self.model_lock:
            return self.session.print_report(part, revision, material=material, bed=bed, angle=angle,
                                             nozzle=nozzle, min_wall=min_wall, per_face=per_face)

    # ------------------------------------------------------------------ stress
    def fea(self, study: str, field: str = "stress", quality: str = "normal", revision=None) -> bytes:
        from . import fea_render
        from .tessellate import Mesh

        with self.model_lock:
            r, report, arrays = self.session.fea_fields(study, rev=revision, quality=quality)
            scale = fea_render.deform_scale(arrays, _diag(arrays["vertices"]), False)
            item, legend = fea_render.scene_item(arrays, report, field, scale, study)
            mesh = item.mesh
            meta = {
                "revision": r,
                "study": study,
                "field": field,
                "legend": legend,
                "report": {k: report.get(k) for k in ("ok", "verdict", "safety_factor", "target", "hotspots",
                                                      "warnings", "material", "max_von_mises_MPa",
                                                      "max_displacement_mm")},
            }
            return encode_mesh(Mesh(mesh.vertices, mesh.normals, mesh.triangles, mesh.face_ids, mesh.edges),
                               item.vertex_colors, meta=meta)


def _diag(verts: np.ndarray) -> float:
    if len(verts) == 0:
        return 1.0
    return float(np.linalg.norm(verts.max(0) - verts.min(0)))


# --------------------------------------------------------------------------- watcher


class Watcher:
    """Poll source timestamps; rebuild when one changes.

    Only ``stat`` is read per poll: hashing every source (which can include
    imported STEP files) every 250 ms would be wasteful, and the build itself
    hashes what it needs.
    """

    def __init__(self, builder: Builder, interval: float = 0.25) -> None:
        self.builder = builder
        self.interval = max(0.05, interval)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cad-watch", daemon=True)
        self.digest = self._signature()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _signature(self) -> tuple:
        store = self.builder.session.store
        try:
            files = store.source_files()
        except Exception:
            return ()
        out = []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            out.append((str(f), st.st_mtime_ns, st.st_size))
        params = store.root / "params.toml"
        try:
            st = params.stat()
            out.append((str(params), st.st_mtime_ns, st.st_size))
        except OSError:
            pass
        return tuple(out)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            with self.builder.model_lock:
                d = self._signature()
                if d != self.digest:
                    self.digest = d
                    self.builder.request()

    def accept(self) -> None:
        """Forget the current state (an edit we caused ourselves)."""
        self.digest = self._signature()


# --------------------------------------------------------------------------- chat


class ChatManager:
    """One local-model generation at a time, streamed to the page."""

    def __init__(self, root: Path, hub: Hub, context=None, verify=None, guard=None, prepare=None) -> None:
        from . import chat

        self.root = root
        self.hub = hub
        self.chat = chat
        self.context = context  # callable -> str, the current project state
        self.verify = verify    # callable -> {ok, ...}, build what the chat wrote
        self.guard = guard      # callable(viewing) -> raises when history is being browsed
        self.prepare = prepare  # callable(selection, viewing) -> context target and commit callback
        self._lock = threading.Lock()
        self._runner = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state(self) -> dict:
        cfg = self.chat.config()
        with self._lock:
            chat_id, messages = self.chat.conversation(self.root)
            return {**cfg, "running": self.running, "chat_id": chat_id, "history": messages,
                    "undo_file": self.chat.undoable_file(self.root)}

    def start(self, message: str, retry_turn: str | None = None, viewing=None,
              selection=None, chat_id: str | None = None) -> dict:
        """Queue a turn against the displayed revision and verified selection."""
        from .chat_retry import plan_turn

        with self._lock:
            if self.running:
                raise CadError("INVALID_ARGUMENT", "a reply is still streaming", hint="wait, or stop it first")
            if self.guard is not None:
                self.guard(viewing)  # history browsing is read-only
            current_chat, messages = self.chat.conversation(self.root)
            if chat_id is not None and chat_id != current_chat:
                raise CadError("STALE_CHAT", "a new chat was started. Send your message again.")
            plan = plan_turn(messages, message, new_turn=self.chat.new_turn_id(messages),
                             retry_turn=retry_turn, selection=selection)
            ctx = self.context
            if ctx is not None and viewing is not None:
                ctx = lambda: self.context(viewing)  # noqa: E731 - closes over viewing
            target, commit = self.prepare(plan.selection, viewing) if self.prepare is not None else (None, None)
            runner = self.chat.Runner(self.root, self._emit, context=ctx, verify=self.verify, commit=commit)
            self._runner = runner
            self._thread = threading.Thread(
                target=self._run_turn, args=(runner, plan, target), name="cad-chat", daemon=True,
            )
            self._thread.start()
        return {"ok": True, "started": True, "turn": plan.turn,
                "replace_turn": plan.replace_turn, "retry_of": plan.retry_of}

    def _run_turn(self, runner, plan, selection) -> None:
        try:
            runner.run(plan.message, turn=plan.turn, selection=selection,
                       replace_turn=plan.replace_turn, retry_of=plan.retry_of)
        except Exception:
            traceback.print_exc()
            code = "CHAT_INTERNAL"
            message = "chat could not finish the turn; refresh to see the current project state"
            self._emit("chat_error", turn=plan.turn, code=code, message=message)

    def stop(self) -> dict:
        with self._lock:
            if self._runner is not None:
                self._runner.stop()
        return {"ok": True, "stopped": True}

    def reset(self, chat_id: str | None = None) -> dict:
        with self._lock:
            if self.running:
                raise CadError("INVALID_ARGUMENT", "a reply is still streaming", hint="wait, or stop it first")
            current_chat, _ = self.chat.conversation(self.root)
            if chat_id is not None and current_chat != chat_id:
                return {"ok": True, "chat_id": current_chat}
            current_chat = self.chat.reset_history(self.root)
            self._emit("chat_reset", chat_id=current_chat)
            return {"ok": True, "chat_id": current_chat}

    def undo(self) -> dict:
        info = self.chat.undo(self.root)
        self.hub.publish({"t": "chat_undo", **info, "ts": time.time()})
        return info

    def _emit(self, kind: str, **payload) -> None:
        self.hub.publish({"t": kind, "ts": time.time(), **payload})


# --------------------------------------------------------------------------- HTTP


class ThreadedHTTPServer(ThreadingHTTPServer):
    """A client that vanishes mid-request is normal here (SSE reconnects, curl --max-time)."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class ProjectAPI:
    """Project operations shared by the local viewer and isolated project workers."""

    def __init__(self, service, library=False):
        self.builder = service.builder
        self.chat = service.chat
        self.library = library

    @staticmethod
    def _send(code, body, ctype="application/json", extra=None):
        return code, body, ctype, extra or {}

    def _json(self, data, code=200):
        return self._send(code, json.dumps(data, default=_json_default).encode())

    def request(self, method, path, query, body=None, content_type="application/json"):
        q = {k: v[-1] for k, v in parse_qs(query).items()}
        if method == "GET":
            if path == "/api/state":
                # read-only history browsing: ?revision=N renders that snapshot
                # without moving HEAD (POST /api/revision still exists for other consumers)
                state = self.builder.state(q.get("revision") or q.get("rev"))
                if self.library:
                    state["projects_url"] = "/projects"
                    state["project_title"] = display_name(self.builder.session.root, state["project"])
                return self._json(state)
            if path == "/api/preview":
                import io
                from . import renderer
                revision = q.get("rev", "")
                if not revision.isascii() or not revision.isdigit():
                    raise CadError("NOT_FOUND", "No preview for this revision.")
                with self.builder.model_lock:
                    _, parts = self.builder.session.parts(int(revision))
                    if not parts:
                        raise CadError("NOT_FOUND", "This revision has no parts.")
                    scene = renderer.build_scene(parts, deflection=.2)
                camera = renderer.parse_views("iso")[0]
                image, _ = renderer.render_one(scene, camera, size=(480, 320), annotate_images=False,
                                               supersample=1, background=(0.0, 0.0, 0.0), margin=.17)
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return self._send(200, buffer.getvalue(), "image/png")
            if path == "/api/mesh":
                return self._send(200, self.builder.mesh_bytes(q.get("part"), q.get("rev")),
                                  "application/octet-stream")
            if path == "/api/frame":
                raw = q.get("index") or "-1"
                index = int(raw) if raw.lstrip("-").isdigit() else -1
                fr = self.builder.frames.get(q.get("part", ""), index)
                if fr is None:
                    return self._json({"ok": False, "error": {"code": "NOT_FOUND", "message": "no such frame"}}, 404)
                return self._send(200, encode_mesh(fr["mesh"]), "application/octet-stream")
            if path == "/api/faces":
                return self._json({"faces": self.builder.faces(q.get("part", ""), q.get("rev"))})
            if path == "/api/chat":
                return self._json(self.chat.state())
            if path == "/api/print":
                return self._json(self.builder.print_report(
                    q.get("part"), q.get("rev"), material=q.get("material"), bed=q.get("bed"),
                    angle=_num(q.get("angle"), 45.0), nozzle=_num(q.get("nozzle"), 0.4),
                    min_wall=_num(q.get("min_wall"), None),
                    per_face=int(_num(q.get("per_face"), 3))))
            if path == "/api/export":
                data, fname, ctype = self.builder.export_bytes(q.get("format", "stl"), q.get("part"), q.get("rev"), mode=q.get("mode"))
                return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"'})
            if path == "/api/fea":
                return self._send(200, self.builder.fea(q.get("study", ""), q.get("field", "stress"),
                                                        q.get("quality", "normal"), q.get("rev")),
                                  "application/octet-stream")
            return self._json({"ok": False, "error": {"code": "NOT_FOUND", "message": f"no route {path}"}}, 404)
        if method == "POST":
            if path == "/api/chat":
                return self._json(self.chat.start(body.get("message", ""), retry_turn=body.get("retry_turn"),
                                                  viewing=body.get("viewing"), selection=body.get("selection"),
                                                  chat_id=body.get("chat_id")))
            if path == "/api/chat/reset":
                if content_type != "application/json" or not isinstance(body, dict):
                    raise CadError("INVALID_ARGUMENT", "Send the chat reset request as JSON.")
                return self._json(self.chat.reset(body.get("chat_id")))
            if path == "/api/chat/stop":
                return self._json(self.chat.stop())
            if path == "/api/chat/undo":
                with self.builder.model_lock:
                    self.builder._guard_viewing(body.get("viewing"))
                    info = self.chat.undo()
                    self.builder.request(force=True)
                return self._json({"ok": True, **info})
            if path == "/api/new-model":
                with self.builder.model_lock:
                    self.builder._guard_viewing(body.get("viewing"))
                    return self._json(self.builder.new_model(body.get("source")))
            if path == "/api/orient":
                return self._json(self.builder.orient(body.get("part"), body.get("ref"), viewing=body.get("viewing")))
            if path == "/api/build":
                with self.builder.model_lock:
                    self.builder._guard_viewing(body.get("viewing"))
                    self.builder.request(body.get("params"), force=bool(body.get("force")))
                return self._json({"ok": True, "queued": True})
            if path == "/api/param":
                values = body.get("values") or {}
                with self.builder.model_lock:
                    self.builder._guard_viewing(body.get("viewing"))
                    self.builder.session.set_params(values, force=bool(body.get("force")))
                    if self.builder.watcher is not None:
                        self.builder.watcher.accept()  # our own write must not look like an edit
                    self.builder.request(force=True)
                    return self._json({"ok": True, "overrides": self.builder.session.params()["overlay"]})
            if path == "/api/revision":
                rev = body.get("revision")
                with self.builder.model_lock:
                    target = self.builder.session.resolve_rev(rev)
                    self.builder.session.store.set_head(target)
                self.builder.publish_state(reason="checkout", revision=target)
                return self._json({"ok": True, "revision": target})
            return self._json({"ok": False, "error": {"code": "NOT_FOUND", "message": f"no route {path}"}}, 404)
        raise CadError("INVALID_ARGUMENT", "unsupported project operation")


def event_stream(hub):
    subscription = hub.subscribe()
    try:
        yield b"retry: 500\n\n"
        while True:
            try:
                ev = subscription.get(timeout=5)
            except queue.Empty:
                yield b": ping\n\n"
                continue
            payload = json.dumps({k: v for k, v in ev.items() if k != "mesh"}, default=_json_default)
            yield f"event: {ev.get('t', 'message')}\ndata: {payload}\n\n".encode()
    finally:
        hub.unsubscribe(subscription)


class Handler(BaseHTTPRequestHandler):
    server_version = "cad"
    protocol_version = "HTTP/1.1"

    # set by serve()
    builder: Builder
    hub: Hub
    chat: ChatManager
    gallery: Gallery | None = None
    project_api: ProjectAPI

    def log_message(self, fmt, *args):  # quiet: the CLI owns the console
        pass

    # -- helpers
    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=_json_default).encode())

    def _error(self, e: Exception) -> None:
        if isinstance(e, CadError):
            code = 404 if e.code == "NOT_FOUND" else 409 if e.code in {"STALE_VIEW", "STALE_CHAT"} else 400
            self._json({"ok": False, "error": e.to_dict()}, code)
        else:
            self._json({"ok": False, "error": {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}",
                                               "details": {"traceback": traceback.format_exc()[-2000:]}}}, 500)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/" and self.gallery is not None:
                return self._send(302, b"", extra={"Location": self.gallery.open(self.gallery.current.name)})
            if u.path in ("/", "/index.html", "/static/index.html"):
                return self._static("chat/index.html")
            if u.path in ("/projects", "/projects/"):
                if self.gallery is not None:
                    return self._static("projects.html")
                raise CadError("NOT_FOUND", "No project library is configured.")
            if u.path == "/api/projects" and self.gallery is not None:
                return self._json({"projects": self.gallery.projects()})
            if u.path == "/api/projects/preview" and self.gallery is not None:
                image = self.gallery.preview(q.get("key"), q.get("rev", ""))
                return self._send(200, image, "image/png", extra={"Cache-Control": "private, max-age=3600"})
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            return self._project("GET", u, None)
        except Exception as e:  # noqa: BLE001
            self._error(e)

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.gallery is not None and u.path.startswith("/api/projects/"):
                if self.headers.get_content_type() != "application/json" or not isinstance(body, dict):
                    raise CadError("INVALID_ARGUMENT", "Send project requests as JSON.")
                if u.path == "/api/projects/create":
                    return self._json({"ok": True, "key": self.gallery.create(body.get("name"))}, 201)
                if u.path == "/api/projects/open":
                    return self._json({"ok": True, "url": self.gallery.open(body.get("key"))})
                if u.path == "/api/projects/rename":
                    self.gallery.rename(body.get("key"), body.get("name"))
                    return self._json({"ok": True})
            return self._project("POST", u, body)
        except Exception as e:  # noqa: BLE001
            self._error(e)

    def _project(self, method, u, body):
        path = u.path
        worker = None
        if path.startswith("/projects/") and self.gallery is not None:
            key, _, route = path[len("/projects/"):].partition("/")
            root = self.gallery.project_path(unquote(key))
            if not route and method == "GET":
                return self._static("chat/index.html")
            if route == "projects" and method == "GET":
                return self._send(303, b"", extra={"Location": "/projects"})
            path = "/" + route
            if not path.startswith("/api/"):
                raise CadError("NOT_FOUND", "Unknown workspace route")
            worker = self.gallery.worker(root)
        elif self.gallery is not None:
            worker = self.gallery.worker(self.gallery.current)

        if method == "GET" and path == "/api/events":
            stream = worker.events() if worker else event_stream(self.hub)
            return self._events(stream)

        api = worker or self.project_api
        return self._send(*api.request(method, path, u.query, body, self.headers.get_content_type()))

    # -- static
    def _static(self, name: str) -> None:
        target = (WEB_DIR / name).resolve()
        if target.is_dir():  # a built sub-app (e.g. /static/chat/)
            target = target / "index.html"
        if not target.is_relative_to(WEB_DIR) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = STATIC_TYPES.get(target.suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    # -- SSE
    def _events(self, stream) -> None:
        self.close_connection = True
        self.connection.settimeout(30)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for chunk in stream:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stream.close()


def _json_default(v):
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def _num(value, default):
    """Query-string number, falling back to ``default`` instead of raising."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- entry


class Service:
    """A running viewer: HTTP routes, build thread, source watcher, live sink.

    ``cad serve`` wraps this and blocks; tests use it directly (``port=0`` picks
    a free port).
    """

    def __init__(self, root: str | os.PathLike, *, host: str = "127.0.0.1", port: int = 8137,
                 poll: float = 0.25, library: str | os.PathLike | None = None) -> None:
        self.root = Path(root).resolve()
        if not (self.root / "cad.toml").exists():
            raise CadError("NOT_FOUND", f"{self.root} is not a cad project (no cad.toml)",
                           hint="run `cad init` here or `cad serve --project <dir>`")
        self.host = host
        self.port = port
        if library is not None and host != "127.0.0.1":
            raise CadError("INVALID_ARGUMENT", "The project library requires --host 127.0.0.1.")
        self.gallery = Gallery(Path(library), self.root) if library is not None else None
        self.session = ProjectSession(self.root)
        self.hub = Hub()
        self.frames = Frames()
        self.builder = Builder(self.session, self.hub, self.frames)
        self.watcher = Watcher(self.builder, poll)
        self.builder.watcher = self.watcher
        self.chat = ChatManager(self.root, self.hub, context=self.builder.chat_context,
                                verify=self.builder.verify_apply, guard=self.builder._guard_viewing,
                                prepare=self.builder.prepare_chat)
        self._httpd: ThreadingHTTPServer | None = None
        self._old_sink = None

    def start(self) -> "Service":
        if os.environ.get("VERCEL") == "1":
            raise CadError("INVALID_ARGUMENT", "use the hosted Sandbox job endpoint on Vercel")
        handler = type("CadHandler", (Handler,), {"builder": self.builder, "hub": self.hub, "chat": self.chat,
                                                "gallery": self.gallery, "project_api": ProjectAPI(self, self.gallery is not None)})
        httpd = None
        for p in range(self.port, self.port + 20):
            try:
                httpd = ThreadedHTTPServer((self.host, p), handler)
                break
            except OSError:
                continue
        if httpd is None:
            raise CadError("INTERNAL", f"no free port in {self.port}..{self.port + 19}", hint="pass --port")
        self._httpd = httpd
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, name="cad-http", daemon=True).start()
        if self.gallery is None:
            self.start_engine()
        else:
            self.gallery.worker(self.root)
        return self

    def start_engine(self):
        self._old_sink = live.set_sink(self._sink)
        self.builder.start()
        self.watcher.start()
        from .project import read_overlay

        store = self.session.store
        head = store.head()
        current = False
        if head is not None:
            manifest = store.manifest(head)
            try:
                current = (store.source_hash()[0] == manifest["source_hash"]
                           and read_overlay(self.root) == manifest.get("project_overrides", manifest["overrides"]))
            except (CadError, OSError, ValueError):
                pass  # schedule a build so the error reaches the workspace
        if current:
            self.builder.publish_state(reason="ready", revision=head)
        else:
            self.builder.request()
        return self

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def revision(self):
        return self.session.store.head()

    def _sink(self, ev: dict) -> None:
        if ev.get("t") == "frame":
            mesh = ev.pop("mesh")
            self.frames.put(ev["part"], ev["index"], {"mesh": mesh, "id": ev.get("id"), "type": ev.get("type")})
        elif ev.get("t") == "build_ok":
            self.frames.set_revision(ev.get("revision"))
        self.hub.publish(ev)

    def stop(self) -> None:
        if self.gallery is None:
            self.chat.stop()
            self.watcher.stop()
            self.builder.stop()
            live.set_sink(self._old_sink)
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self.gallery is not None:
            self.gallery.stop()

    def __enter__(self) -> "Service":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def serve(root: str | os.PathLike, *, host: str = "127.0.0.1", port: int = 8137, open_browser: bool = True,
          poll: float = 0.25, quiet: bool = False, on_ready=None, library: str | os.PathLike | None = None) -> dict:
    svc = Service(root, host=host, port=port, poll=poll, library=library).start()

    def on_term(*_):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, on_term)
    except ValueError:  # not the main thread (tests)
        pass

    info = {"project": svc.session.config.name, "root": str(svc.root), "url": svc.url,
            "host": svc.host, "port": svc.port, "revision": svc.revision}
    if on_ready is not None:
        on_ready(dict(info))
    if not quiet:
        print(f"cad serve  {svc.session.config.name}  {svc.url}", flush=True)
        print(f"  watching {svc.root}  (edit model.py or drag a parameter; ctrl-c stops)", flush=True)
        if svc.host not in ("127.0.0.1", "localhost", "::1"):
            print(f"  warning: bound to {svc.host}; anyone who can reach this port can run this project's Python", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(svc.url)).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        if not quiet:
            print("\nstopping", flush=True)
    finally:
        out = dict(info, stopped=True, error=svc.builder.last_error)
        svc.stop()
    return out
