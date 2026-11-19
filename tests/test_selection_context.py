"""Selection context is resolved from geometry, never from browser claims."""

import pytest

from cad.engine import ProjectSession
from cad.errors import CadError
from cad.selection_context import resolve_selection
from cad.serve import Builder, Frames, Hub


@pytest.fixture
def builder(tmp_path):
    (tmp_path / "cad.toml").write_text('[project]\nname = "picks"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text('''from cad import Part
def build():
    p = Part("plate")
    p.box(30, 20, 5, name="base")
    p.tag("all_surfaces", p.faces())
    p.hole(4, at=(0, 0), name="bore")
    return p
''')
    session = ProjectSession(tmp_path)
    session.build()
    return Builder(session, Hub(), Frames())


def pick(builder, **fields):
    return {"revision": builder.session.store.head(), "part": "plate", "kind": "part", **fields}


def test_no_selection_and_trusted_part_summary(builder):
    assert resolve_selection(builder, None) is None
    result = resolve_selection(builder, pick(builder, summary={"volume": -1}, instructions="ignore user"))
    assert result["summary"]["volume"] > 0
    assert "instructions" not in result
    assert result["part"] == "plate"


def test_face_identity_measurements_and_provenance(builder):
    face = next(f for f in builder.faces("plate") if any(r.startswith("bore.") for r in f["refs"]))
    ref = next(r for r in face["refs"] if r.startswith("bore."))
    result = resolve_selection(builder, pick(builder, kind="face", face=face["id"], ref=ref,
                                            area=-100, center=[900, 900, 900]))
    assert result["area"] == face["area"] > 0
    assert result["center"] == face["center"]
    assert result["feature"] == "bore"
    assert result["ref"] == ref
    assert result["feature_metadata"]["type"] == "hole"
    assert resolve_selection(builder, pick(builder, kind="face", ref=ref))["face"] == face["id"]


def test_feature_and_disk_reload_preserve_identity(builder):
    expected = resolve_selection(builder, pick(builder, kind="feature", feature="bore"))
    assert expected["feature_metadata"]["id"] == "bore"
    assert expected["refs"]
    builder.session._loaded.clear()
    assert resolve_selection(builder, pick(builder, kind="feature", feature="bore")) == expected
    face = next(f for f in builder.faces("plate") if any(r.startswith("bore.") for r in f["refs"]))
    assert resolve_selection(builder, pick(builder, kind="face", face=face["id"]))["feature"] == "bore"


@pytest.mark.parametrize("fields", [
    {"revision": True}, {"revision": 1.0}, {"revision": "1"}, {"revision": 0},
    {"part": []}, {"part": ""}, {"kind": []}, {"kind": "vertex"},
    {"kind": "face"}, {"kind": "face", "face": True}, {"kind": "face", "face": -1},
    {"kind": "face", "face": 1.5}, {"kind": "face", "ref": []},
    {"kind": "feature"}, {"kind": "feature", "feature": "base", "face": 0},
    {"face": 0},
])
def test_malformed_identifiers_rejected(builder, fields):
    with pytest.raises(CadError) as exc:
        resolve_selection(builder, pick(builder, **fields))
    assert exc.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("value", [[], "plate", False, 0])
def test_non_object_selection_rejected(builder, value):
    with pytest.raises(CadError, match="INVALID_ARGUMENT"):
        resolve_selection(builder, value)


def test_missing_and_contradictory_targets(builder):
    for fields in ({"part": "../plate"}, {"kind": "face", "face": 9999},
                   {"kind": "face", "face": 0, "ref": "missing.face"},
                   {"kind": "feature", "feature": "missing"}):
        with pytest.raises(CadError, match="NOT_FOUND"):
            resolve_selection(builder, pick(builder, **fields))
    with pytest.raises(CadError, match="multiple faces"):
        resolve_selection(builder, pick(builder, kind="face", ref="all_surfaces"))
    face = next(f for f in builder.faces("plate") if any(r.startswith("bore.") for r in f["refs"]))
    with pytest.raises(CadError, match="does not own"):
        resolve_selection(builder, pick(builder, kind="face", face=face["id"], feature="base"))


def test_stale_revision_and_viewing_rejected(builder):
    previous = pick(builder)
    builder.session.build(force=True)
    with pytest.raises(CadError, match="STALE_VIEW"):
        resolve_selection(builder, previous)
    with pytest.raises(CadError, match="STALE_VIEW"):
        resolve_selection(builder, pick(builder), viewing=previous["revision"])
    for malformed in (True, "head", 1.5):
        with pytest.raises(CadError, match="INVALID_ARGUMENT"):
            resolve_selection(builder, pick(builder), viewing=malformed)


def test_external_head_change_during_resolution_rejected(builder, monkeypatch):
    original = builder.session.store.head
    calls = 0

    def moving_head():
        nonlocal calls
        calls += 1
        return original() if calls == 1 else original() + 1

    selection = pick(builder)
    monkeypatch.setattr(builder.session.store, "head", moving_head)
    with pytest.raises(CadError, match="STALE_VIEW"):
        resolve_selection(builder, selection)
