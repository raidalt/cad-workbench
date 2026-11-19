"""Reopening a project that already has a build must not depend on in-memory frames."""

import json
import time
import urllib.error
import urllib.request


def call(svc, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(svc.url.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200].decode("utf-8", "replace")}


def write_project(tmp_path):
    (tmp_path / "cad.toml").write_text('[project]\nname = "reopen"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text(
        "from cad import Part\n\n\ndef build():\n"
        "    p = Part('plate')\n    p.box(40, 30, 4, name='body')\n    return p\n")
    return tmp_path


def test_frames_are_safe_before_any_build():
    from cad.serve import Frames

    f = Frames()
    assert f.timeline("plate") == []
    assert f.get("plate", 0) is None
    f.begin()
    f.settle()  # nothing was produced and there is no previous set
    assert f.timeline("plate") == []


def test_cold_start_on_an_existing_project_serves_state(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as first:
        deadline = time.time() + 10
        while time.time() < deadline:
            code, state = call(first, "/api/state")
            if code == 200 and state.get("revision") and not state.get("building"):
                break
            time.sleep(0.05)
        assert state["revision"] == 1

    # a new process would have no frames in memory: this is the cold-start path
    with Service(root, port=0, poll=60) as second:
        code, state = call(second, "/api/state")
        assert code == 200, state
        assert state["revision"] == 1
        assert state["parts"]["plate"]["timeline"] == [], "no frames exist until a build runs here"
        assert state["empty"] is False

        code, body = call(second, "/api/frame?part=plate&index=0")
        assert code == 404
        assert body["ok"] is False and body["error"]["code"] == "NOT_FOUND"

        code, body = call(second, "/api/frame?part=plate&index=nonsense")
        assert code == 404 and body["error"]["code"] == "NOT_FOUND"

        # and a build on the reopened project still works and yields frames
        code, body = call(second, "/api/build", {"force": True})
        assert code == 200
        deadline = time.time() + 10
        while time.time() < deadline:
            _, state = call(second, "/api/state")
            if not state.get("building") and state["parts"]["plate"]["timeline"]:
                break
            time.sleep(0.05)
        assert state["parts"]["plate"]["timeline"], "frames appear after a build in this process"
