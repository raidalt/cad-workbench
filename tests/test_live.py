"""Tests for cad.live: the sink a viewer attaches to watch geometry grow.

A sink is process-global, so the fixture below detaches it (and clears any
half-open session) before and after every test.
"""

from pathlib import Path

import pytest

from cad import live
from cad.engine import ProjectSession
from cad.errors import CadError
from cad.part import Part

GOOD = '''from cad import Part, Parameter

width = Parameter("width", 40, min=10, max=100)


def build():
    p = Part("plate")
    p.box(width, 30, 5, name="base")
    p.box(10, 10, 20, at=(0, 0, 5), name="tower")
    p.hole(4, at=(0, 0), name="bore")
    return p
'''

FAILING = '''from cad import Part

p = Part("block")
p.box(20, 20, 20, name="body")
p.fillet(p.edges().vertical(), 30, name="round")
'''


@pytest.fixture(autouse=True)
def clean_live_state():
    live.set_sink(None)
    live.fail({"code": "TEST_RESET"})  # clear a session an interrupted test left behind
    yield
    live.set_sink(None)
    live.fail({"code": "TEST_RESET"})


@pytest.fixture
def events():
    evs = []
    live.set_sink(evs.append)
    yield evs
    live.set_sink(None)


def write_project(root: Path, model: str = GOOD, name: str = "demo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cad.toml").write_text(f'[project]\nname = "{name}"\nentry = "model.py"\n')
    (root / "model.py").write_text(model)
    return root


def by_type(events: list[dict], t: str) -> list[dict]:
    return [e for e in events if e["t"] == t]


def test_build_without_sink_emits_nothing(tmp_path, monkeypatch):
    emitted = []
    monkeypatch.setattr(live, "emit", lambda *a, **k: emitted.append((a, k)))
    assert live.sink_active() is False

    session = ProjectSession(write_project(tmp_path))
    first = session.build(force=True)
    second = session.build(force=True)

    assert first["revision"] == 1 and second["revision"] == 2
    assert first["parts"]["plate"]["valid"] is True
    assert live.sink_active() is False
    assert live.current() is None
    assert emitted == [], "no sink means no event is even formatted"


def test_sink_sees_build_lifecycle(tmp_path, events):
    result = ProjectSession(write_project(tmp_path)).build(force=True)

    kinds = [e["t"] for e in events]
    assert kinds[0] == "build_start"
    assert kinds[-1] == "build_ok"

    features = by_type(events, "feature")
    assert [f["id"] for f in features] == ["base", "tower", "bore"]
    assert [f["index"] for f in features] == [0, 1, 2]
    assert {f["part"] for f in features} == {"plate"}
    assert [f["type"] for f in features] == ["box", "box", "hole"]

    frames = by_type(events, "frame")
    assert frames, "a short part gets a frame per feature"
    assert all(len(f["mesh"].triangles) > 0 for f in frames)
    assert all(f["partial"] is True for f in frames)

    ok = events[-1]
    assert ok["revision"] == result["revision"]
    assert ok["parts"]["plate"]["valid"] is True


def test_frames_grow_as_features_are_added(events):
    part = Part("plate")
    assert live.start(project="demo", entry="model.py", params={}) is not None

    part.box(40, 30, 5, name="base")
    part.box(10, 10, 20, at=(0, 0, 5), name="tower")
    part.hole(4, at=(0, 0), name="bore")
    live.finish(revision=1)

    frames = by_type(events, "frame")
    assert [f["index"] for f in frames] == [0, 1, 2]
    vertices = [len(f["mesh"].vertices) for f in frames]
    triangles = [len(f["mesh"].triangles) for f in frames]
    assert vertices[0] < vertices[1] < vertices[2], vertices
    assert triangles[0] < triangles[1] < triangles[2], triangles


def test_failed_build_clears_session_and_next_build_is_clean(tmp_path, events):
    root = write_project(tmp_path, FAILING)
    session = ProjectSession(root)

    with pytest.raises(CadError) as err:
        session.build(force=True)
    assert err.value.code == "FILLET_FAILED"
    assert live.current() is None
    assert by_type(events, "build_error")[-1]["error"]["code"] == "FILLET_FAILED"

    events.clear()
    (root / "model.py").write_text(GOOD)
    result = session.build(force=True)

    kinds = [e["t"] for e in events]
    assert kinds[0] == "build_start" and kinds[-1] == "build_ok"
    features = by_type(events, "feature")
    assert [f["index"] for f in features] == list(range(len(features)))
    assert {f["part"] for f in features} == {"plate"}, "stale events from the failed part"
    assert events[-1]["revision"] == result["revision"]


def test_set_sink_none_detaches(tmp_path, events):
    session = ProjectSession(write_project(tmp_path))
    session.build(force=True)
    assert events

    old = live.set_sink(None)
    assert old is not None
    assert live.sink_active() is False

    seen = len(events)
    session.build(force=True)
    assert len(events) == seen, "a detached sink gets nothing, not even from a new build"
    assert live.current() is None


def test_raising_sink_does_not_fail_the_build(tmp_path):
    calls = []

    def broken(event):
        calls.append(event["t"])
        raise RuntimeError("viewer is broken")

    live.set_sink(broken)
    try:
        result = ProjectSession(write_project(tmp_path)).build(force=True)
    finally:
        live.set_sink(None)

    assert result["revision"] == 1
    assert calls[0] == "build_start"
    assert calls[-1] == "build_ok"
    assert live.current() is None
