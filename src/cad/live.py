"""Live build events: let a viewer watch geometry grow, feature by feature.

A *sink* is a callable ``sink(event: dict) -> None`` registered by
:mod:`cad.serve` (or any other front end).  When a sink is registered,
:class:`Part` reports each finished feature and, on a time budget, a triangle
mesh of the solid so far.

With no sink the module does nothing: a normal ``cad build`` pays for one
``is None`` check per feature.  Frames are only taken while a sink is attached,
so nothing is written to disk and no build is slowed down.

Events (all carry ``t`` = type):

``build_start``  ``{"project", "entry", "params"}``
``feature``      ``{"part", "id", "type", "index", "count", "ms", "metrics"}``
``frame``        ``{"part", "index", "id", "mesh": Mesh, "partial": True}``
``build_ok``     ``{"revision", "duration_ms", "parts", "warnings"}``
``build_error``  ``{"error": {...}}``
``note``         ``{"level", "message"}``

Callbacks run on the building thread; a sink that talks to another thread must
hand off (``cad.serve`` does).
"""

from __future__ import annotations

import threading
import time

_SINK = None
_SESSION: "_Session | None" = None
_LOCK = threading.Lock()

# frame budget: preview meshes cost a few ms each, so cap how much of a build
# goes to them.  Short parts get a frame per feature (that is what makes the
# timeline scrubbable); long ones fall back to a time budget.
FRAME_MIN_INTERVAL_S = 0.05
FRAME_ALWAYS_FIRST = 12
FRAME_MAX = 48


def sink_active() -> bool:
    return _SINK is not None


def set_sink(sink):
    """Attach (or with ``None``, detach) the event sink.  Returns the old one."""
    global _SINK
    with _LOCK:
        old, _SINK = _SINK, sink
    return old


def current() -> "_Session | None":
    return _SESSION


def emit(t: str, **payload) -> None:
    sink = _SINK
    if sink is None:
        return
    ev = {"t": t, "ts": round(time.time(), 4), **payload}
    try:
        sink(ev)
    except Exception:  # a broken viewer must never fail a build
        pass


class _Session:
    """Per-build frame budget and emitted-feature count."""

    def __init__(self) -> None:
        self.frames: dict[str, list] = {}
        self.last_frame = 0.0
        self.counts: dict[str, int] = {}

    def next_index(self, part: str) -> int:
        n = self.counts.get(part, 0)
        self.counts[part] = n + 1
        return n


def start(**info) -> _Session | None:
    global _SESSION
    if _SINK is None:
        return None
    s = _Session()
    _SESSION = s
    emit("build_start", **info)
    return s


def feature_done(part, feat) -> None:
    """Report a finished feature; snapshot geometry only when due."""
    session = _SESSION
    if session is None or _SINK is None:
        return
    name = part.name
    index = session.next_index(name)
    emit(
        "feature",
        part=name,
        id=feat.id,
        type=feat.type,
        index=index,
        count=len(part.features),
        ms=round(feat.duration_ms, 2),
        metrics={k: feat.metrics[k] for k in ("volume", "solids", "faces") if k in (feat.metrics or {})},
    )
    now = time.monotonic()
    if index >= FRAME_ALWAYS_FIRST and now - session.last_frame < FRAME_MIN_INTERVAL_S:
        return
    session.last_frame = now
    mesh = _preview_mesh(part)
    if mesh is None:
        return
    frames = session.frames.setdefault(name, [])
    frames.append({"index": index, "id": feat.id, "type": feat.type, "mesh": mesh})
    if len(frames) > FRAME_MAX:  # keep the first and last, thin the middle
        del frames[1:-1:3]
    emit("frame", part=name, index=index, id=feat.id, type=feat.type, mesh=mesh, partial=True)


def _preview_mesh(part):
    """A coarse triangle mesh of the part's current shape, or None."""
    try:
        from .tessellate import tessellate

        shape = part.shape
        if shape is None:
            return None
        return tessellate(shape, None, 0.45)
    except Exception:
        return None


def finish(*, revision=None, duration_ms=None, parts=None, warnings=None) -> None:
    global _SESSION
    session, _SESSION = _SESSION, None
    if session is None:
        return
    emit("build_ok", revision=revision, duration_ms=duration_ms, parts=parts or {}, warnings=warnings or [])


def fail(error: dict) -> None:
    global _SESSION
    session, _SESSION = _SESSION, None
    if session is None:
        return
    emit("build_error", error=error)


def note(message: str, level: str = "info") -> None:
    emit("note", level=level, message=message)
