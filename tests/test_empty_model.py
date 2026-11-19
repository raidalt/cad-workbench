"""Intentional zero-part models: build() returning {} is a revision, not an error.

A new project (and a change that removes every part) has nothing to draw, but
it still has a script, a history and a place to go next.  These tests pin the
line between "expressed an empty model on purpose" (a revision with 0 parts)
and "never expressed a model at all" (NO_MODEL).
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cad.cli import main
from cad.engine import Engine
from cad.errors import CadError
from cad.runner import run_script
from cad.serve import Service

EMPTY = '''from cad import Part


def build():
    # Start a part here, for example:
    # return Part("plate").box(60, 40, 4)
    return {}
'''

PLATE = '''from cad import Part


def build():
    p = Part("plate")
    p.box(40, 30, 5, name="base")
    return p
'''


def write_project(root: Path, model: str = EMPTY, name: str = "empty") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cad.toml").write_text(f'[project]\nname = "{name}"\nentry = "model.py"\n')
    (root / "model.py").write_text(model)
    return root


def request(svc: Service, path: str, payload=None, timeout: float = 20.0):
    url = svc.url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def call(svc: Service, path: str, payload=None):
    return request(svc, path, payload)


def wait_idle(svc: Service, deadline: float = 10.0) -> dict:
    end = time.monotonic() + deadline
    state: dict = {}
    while time.monotonic() < end:
        state = call(svc, "/api/state")[1]
        if not state["building"]:
            return state
        time.sleep(0.01)
    raise AssertionError(f"build did not settle within {deadline}s; state={state}")


# --------------------------------------------------------------------------- runner semantics


def test_build_returning_empty_is_a_valid_zero_part_build(tmp_path):
    root = write_project(tmp_path)

    out = run_script(root / "model.py")

    assert out.parts == {}
    assert out.empty_model is True


@pytest.mark.parametrize("body", ["", "x = 1\n"])
def test_no_build_and_no_geometry_is_still_no_model(tmp_path, body):
    (tmp_path / "model.py").write_text(body)

    with pytest.raises(CadError) as exc:
        run_script(tmp_path / "model.py")

    assert exc.value.code == "NO_MODEL"


@pytest.mark.parametrize("returned", ["None", "'x'", "[1, 2]", "42"])
def test_build_returning_a_non_part_is_still_an_error(tmp_path, returned):
    (tmp_path / "model.py").write_text(f"def build():\n    return {returned}\n")

    with pytest.raises(CadError) as exc:
        run_script(tmp_path / "model.py")

    assert exc.value.code == "SCRIPT_ERROR"


# --------------------------------------------------------------------------- revision + API


def test_empty_build_publishes_a_cached_revision_and_a_quiet_viewer(tmp_path):
    root = write_project(tmp_path)
    session = Engine().session(root)

    first = session.build(force=True)
    second = session.build()

    assert first["revision"] == 1 and first["parts"] == {}
    assert second["cached"] is True and second["revision"] == 1
    assert session.store.revision_numbers() == [1], "a cached build must not publish a revision"

    with Service(root, port=0, poll=60) as svc:
        state = wait_idle(svc)
        assert state["revision"] == 1
        assert state["empty"] is True
        assert state["parts"] == {}

        for route in ("/api/mesh", "/api/faces"):
            code, body = call(svc, route)
            assert code == 404, route
            assert body["ok"] is False
            assert body["error"] == {"code": "NOT_FOUND", "message": "this revision has no parts"}


def test_cli_build_and_inspect_say_zero_parts(tmp_path):
    root = write_project(tmp_path)

    code, out, err = main(["build"], root)
    assert code == 0, err
    assert "revision 1 (0 parts)" in out

    code, out, err = main(["inspect"], root)
    assert code == 0, err
    assert "0 parts" in out

    for command in ("render", "export"):
        code, out, err = main([command], root)
        assert code != 0, f"{command} must fail on an empty revision"
        assert "NOT_FOUND" in err
        assert "has no parts" in err

    assert Engine().session(root).store.revision_numbers() == [1]


# --------------------------------------------------------------------------- new model


def test_new_model_replaces_the_script_and_keeps_every_revision(tmp_path):
    root = write_project(tmp_path, PLATE, name="demo")
    with Service(root, port=0, poll=60) as svc:
        assert wait_idle(svc)["parts"].keys() == {"plate"}

        code, body = call(svc, "/api/new-model", {})
        assert code == 200
        assert body["ok"] is True
        assert body["revision"] == 2
        assert body["backup"] and Path(body["backup"]).exists()
        assert Path(body["backup"]).parent == root / ".cad" / "backups"

        state = wait_idle(svc)
        assert state["revision"] == 2
        assert state["empty"] is True and state["parts"] == {}
        assert [r["n"] for r in state["revisions"]] == [1, 2], "history is preserved"
        assert (root / "model.py").read_text() == EMPTY

        # the old revision is still readable, by number and by checkout
        code, state = call(svc, "/api/state?rev=1")
        assert code == 200 and set(state["parts"]) == {"plate"}
        assert state["empty"] is False

        code, body = call(svc, "/api/revision", {"revision": 1})
        assert code == 200 and body["ok"] is True
        state = call(svc, "/api/state")[1]
        assert set(state["parts"]) == {"plate"} and state["empty"] is False

        # an invalid source is refused before anything is written
        before = (root / "model.py").read_bytes()
        code, body = call(svc, "/api/new-model", {"source": "def build(:\n    pass\n"})
        assert code == 400
        assert body["ok"] is False and body["error"]["code"] == "INVALID_ARGUMENT"
        assert (root / "model.py").read_bytes() == before


# --------------------------------------------------------------------------- orientation


def test_orient_lays_the_part_on_a_named_face(tmp_path):
    root = write_project(tmp_path, PLATE, name="demo")
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)

        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.top"})
        assert code == 200
        assert body["ok"] is True and body["changed"] is True
        assert body["revision"] == 2
        assert Path(body["backup"]).exists()

        state = wait_idle(svc)
        assert state["revision"] == 2
        assert state["parts"]["plate"]["print_down"] == [0, 0, 1]
        assert [r["n"] for r in state["revisions"]] == [1, 2]

        source = (root / "model.py").read_text()
        assert 'p.print_on(p.faces("base.top"))' in source
        assert source.index("print_on") < source.index("return p")

        # idempotent: the same call is a no-op, not another revision
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.top"})
        assert code == 200 and body["ok"] is True and body["changed"] is False
        assert body["revision"] == 2
        assert [r["n"] for r in call(svc, "/api/state")[1]["revisions"]] == [1, 2]


def test_orient_reverts_on_a_bad_ref_and_refuses_an_unparseable_script(tmp_path):
    root = write_project(tmp_path, PLATE, name="demo")
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)

        before = (root / "model.py").read_bytes()
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "nope.top"})
        assert code == 200
        assert body["ok"] is False and body["reverted"] is True
        assert body["error"]["code"] in ("UNKNOWN_REFERENCE", "REFERENCE_LOST")
        assert (root / "model.py").read_bytes() == before, "the failed edit is reverted byte for byte"
        assert call(svc, "/api/state")[1]["revision"] == 1, "no revision was published"

        (root / "model.py").write_text("def build(:\n    pass\n")
        broken = (root / "model.py").read_bytes()
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.top"})
        assert code == 400
        assert body["ok"] is False and body["error"]["code"] == "INVALID_ARGUMENT"
        assert (root / "model.py").read_bytes() == broken
