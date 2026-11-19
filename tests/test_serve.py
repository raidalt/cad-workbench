"""HTTP tests for cad.serve: state, binary meshes, frames, params, revisions, SSE.

Every service binds port 0 (a free port) and runs in its own project under
``tmp_path``.  Build requests are asynchronous, so helpers wait for the build
thread to go idle with a bounded deadline instead of sleeping.
"""

import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cad.project import read_overlay
from cad.serve import Frames, Hub, Service, encode_mesh

MODEL = '''from cad import Part, Parameter

width = Parameter("width", 40, min=10, max=100)


def build():
    p = Part("plate")
    p.box(width, 30, 5, name="base")
    p.hole(4, at=(0, 0), name="bore")
    return p
'''

TWO_PARTS = '''from cad import Part


def build():
    left = Part("left")
    left.box(10, 10, 10, name="left_box")
    right = Part("right")
    right.box(10, 10, 10, name="right_box")
    return [left, right]
'''

WEB = Path(__file__).resolve().parents[1] / "src" / "cad" / "web"


# --------------------------------------------------------------------------- helpers


def write_project(root: Path, model: str = MODEL, name: str = "demo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cad.toml").write_text(f'[project]\nname = "{name}"\nentry = "model.py"\n')
    (root / "model.py").write_text(model)
    return root


def request(svc: Service, path: str, payload=None, timeout: float = 10.0):
    """(status, body) for one request; 4xx is returned, not raised."""
    url = svc.url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def call(svc: Service, path: str, payload=None):
    code, body = request(svc, path, payload)
    return code, json.loads(body)


def wait_idle(svc: Service, predicate=lambda state: True, deadline: float = 5.0) -> dict:
    """Wait until no build is running and ``predicate(state)`` holds."""
    end = time.monotonic() + deadline
    state: dict = {}
    while time.monotonic() < end:
        state = call(svc, "/api/state")[1]
        if not state["building"] and predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError(f"build did not settle within {deadline}s; state={state}")


def wait_revision(svc: Service, before: int, deadline: float = 5.0) -> dict:
    return wait_idle(svc, lambda s: s["revision"] != before, deadline)


def decode_mesh(body: bytes) -> tuple[dict, int]:
    """Parse the JSON header, return it and the number of bytes the arrays occupy."""
    n = struct.unpack("<I", body[:4])[0]
    head = json.loads(body[4:4 + n])
    size = (4 + n
            + head["vertices"] * 12    # positions (float32 xyz)
            + head["vertices"] * 12    # normals
            + head["triangles"] * 12   # indices (uint32 x3)
            + head["triangles"] * 4    # face ids (int32)
            + head["edges"] * 4        # points per edge
            + head["edge_points"] * 12
            + head["colors"] * 12)
    return head, size


def decode_face_ids(body: bytes) -> list[int]:
    head, _ = decode_mesh(body)
    n = struct.unpack("<I", body[:4])[0]
    start = 4 + n + head["vertices"] * 24 + head["triangles"] * 12
    return [struct.unpack_from("<i", body, start + i * 4)[0] for i in range(head["triangles"])]


def expected_mesh(svc: Service, part: str, rev=None) -> bytes:
    with svc.builder.model_lock:
        r, parts = svc.session.parts(rev)
        return encode_mesh(svc.session.mesh(r, part, parts[part]))


# --------------------------------------------------------------------------- state


def test_state_shape(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        state = wait_idle(svc)

        assert state["project"] == "demo"
        assert isinstance(state["revision"], int) and state["revision"] == 1
        assert set(state["parts"]) == {"plate"}

        part = state["parts"]["plate"]
        assert [f["id"] for f in part["features"]] == ["base", "bore"]
        assert all(f["status"] == "ok" for f in part["features"])
        assert isinstance(part["timeline"], list)

        width = state["parameters"]["width"]
        assert width["value"] == 40
        assert width["min"] == 10 and width["max"] == 100
        assert isinstance(state["capabilities"]["fea"], bool)
        assert state["capabilities"]["fea"] is True


# --------------------------------------------------------------------------- meshes


def test_mesh_binary_matches_encode_mesh(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)

        code, body = request(svc, "/api/mesh?part=plate")
        assert code == 200
        assert body == expected_mesh(svc, "plate"), "the route must serve exactly encode_mesh output"

        head, size = decode_mesh(body)
        assert head["vertices"] > 0 and head["triangles"] > 0
        assert size == len(body), "every byte in the payload belongs to a declared array"

        assert request(svc, "/api/mesh?part=plate&rev=head")[1] == body

        # a second revision so that -1 (one back from head) exists
        assert call(svc, "/api/param", {"values": {"width": 70}})[0] == 200
        wait_revision(svc, 1)

        code, old = request(svc, "/api/mesh?part=plate&rev=-1")
        assert code == 200
        old_head, old_size = decode_mesh(old)
        assert old_head["triangles"] > 0 and old_size == len(old)


def test_mesh_without_part_needs_a_single_part_project(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)
        code, body = request(svc, "/api/mesh")
        assert code == 200
        head, size = decode_mesh(body)
        assert head["triangles"] > 0 and size == len(body)

    with Service(write_project(tmp_path / "two", TWO_PARTS), port=0) as svc:
        state = wait_idle(svc)
        assert set(state["parts"]) == {"left", "right"}

        code, body = call(svc, "/api/mesh")
        assert 400 <= code < 500
        assert body["ok"] is False
        assert body["error"]["code"] == "INVALID_ARGUMENT"
        assert request(svc, "/api/mesh?part=left")[0] == 200


def test_frame_returns_preview_mesh(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)
        assert call(svc, "/api/build", {"force": True})[0] == 200
        state = wait_revision(svc, 1)

        timeline = state["parts"]["plate"]["timeline"]
        assert timeline, "a forced build records one frame per feature"
        index = timeline[0]["index"]

        code, body = request(svc, f"/api/frame?part=plate&index={index}")
        assert code == 200
        head, size = decode_mesh(body)
        assert head["triangles"] > 0 and size == len(body)

        code, err = call(svc, "/api/frame?part=plate&index=9999")
        assert code == 404
        assert err["ok"] is False and err["error"]["code"] == "NOT_FOUND"


def test_faces_are_subset_of_mesh_face_ids(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)

        code, body = call(svc, "/api/faces?part=plate")
        assert code == 200
        faces = body["faces"]
        assert faces

        used = set(decode_face_ids(request(svc, "/api/mesh?part=plate")[1]))
        assert {f["id"] for f in faces} <= used
        assert any(f["refs"] for f in faces), "named faces carry their reference"


# --------------------------------------------------------------------------- params / revisions


def test_param_writes_overlay_and_rebuilds(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        before = wait_idle(svc)

        code, body = call(svc, "/api/param", {"values": {"width": 70}})
        assert code == 200 and body["ok"] is True
        assert read_overlay(svc.root)["width"] == 70

        state = wait_revision(svc, before["revision"])
        assert state["parameters"]["width"]["value"] == 70
        assert state["parameters"]["width"]["overridden"] is True

        code, err = call(svc, "/api/param", {"values": {"width": 5000}})
        assert 400 <= code < 500
        assert err["ok"] is False and err["error"]["code"] == "PARAMETER_OUT_OF_RANGE"

        code, err = call(svc, "/api/param", {"values": {"height": 10}})
        assert 400 <= code < 500
        assert err["ok"] is False and err["error"]["code"] == "UNKNOWN_PARAMETER"


def test_cached_rebuild_keeps_the_timeline(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)
        assert call(svc, "/api/build", {"force": True})[0] == 200
        state = wait_revision(svc, 1)
        assert state["parts"]["plate"]["timeline"]

        assert call(svc, "/api/build", {})[0] == 200
        state = wait_idle(svc, lambda s: (s["last_build"] or {}).get("cached") is True)
        assert state["parts"]["plate"]["timeline"], "a cached build must not drop the frames"


def test_revision_switch_restores_parameter_values(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        assert wait_idle(svc)["revision"] == 1

        assert call(svc, "/api/param", {"values": {"width": 70}})[0] == 200
        state = wait_revision(svc, 1)
        assert state["revision"] == 2
        assert state["parameters"]["width"]["value"] == 70

        code, body = call(svc, "/api/revision", {"revision": 1})
        assert code == 200 and body["ok"] is True

        state = call(svc, "/api/state")[1]
        assert state["revision"] == 1
        assert state["parameters"]["width"]["value"] == 40


def test_stress_overlay_solves_the_requested_revision(tmp_path):
    from cad import fea

    if fea.missing():
        pytest.skip("optional FEA packages not installed")
    model = """from cad import Part, Parameter, Study

length = Parameter("length", 80, min=60, max=120)

def build():
    beam = Part("beam", material="PETG")
    beam.box(length, 10, 10, name="body")
    study = Study("payload", beam)
    study.fixed("body.left")
    study.force("body.right", 10, "-z")
    return beam
"""
    with Service(write_project(tmp_path, model), port=0) as svc:
        initial = wait_idle(svc)
        assert initial["capabilities"]["fea"]
        assert call(svc, "/api/param", {"values": {"length": 110}})[0] == 200
        latest = wait_revision(svc, initial["revision"])
        path = "/api/fea?study=payload&field=stress&quality=draft"
        results = []
        for revision in (initial["revision"], latest["revision"]):
            status, body = request(svc, f"{path}&rev={revision}", timeout=45)
            assert status == 200
            header, size = decode_mesh(body)
            assert size == len(body) and header["colors"] > 0
            assert header["meta"]["revision"] == revision
            report = header["meta"]["report"]
            assert report["max_von_mises_MPa"] > 0
            results.append(report["max_displacement_mm"])
        assert 0 < results[0] < results[1]
        assert call(svc, "/api/state")[1]["head"] == latest["revision"]


# --------------------------------------------------------------------------- failures


def test_failed_build_keeps_the_last_good_revision(tmp_path):
    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:  # no watcher rebuild: we drive builds ourselves
        state = wait_idle(svc)
        assert state["revision"] == 1
        good = request(svc, "/api/mesh?part=plate")[1]

        (root / "model.py").write_text("from cad import Part\n\nraise RuntimeError('boom')\n")
        assert call(svc, "/api/build", {"force": True})[0] == 200
        state = wait_idle(svc, lambda s: s["error"] is not None)

        assert state["revision"] == 1, "a failed build must not move HEAD"
        assert state["error"]["code"] == "SCRIPT_ERROR"
        assert request(svc, "/api/mesh?part=plate")[1] == good


# --------------------------------------------------------------------------- events


def test_events_stream_reports_the_build(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)
        stream = urllib.request.urlopen(svc.url.rstrip("/") + "/api/events", timeout=10)
        try:
            assert call(svc, "/api/build", {"force": True})[0] == 200
            names = []
            event = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and "build_ok" not in names:
                line = stream.readline().decode()
                if not line:
                    break
                line = line.rstrip("\n")
                if line.startswith("event: "):
                    event = line[len("event: "):]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: "):])  # every data line must be JSON
                    assert isinstance(data, dict) and data["t"] == event
                    names.append(event)
            assert names[0] == "build_start"
            assert "feature" in names
            assert names[-1] == "build_ok"
        finally:
            stream.close()


def test_hub_fans_out_to_subscribers():
    hub = Hub()
    queue = hub.subscribe()
    hub.publish({"t": "feature"})
    assert queue.get_nowait() == {"t": "feature"}

    hub.unsubscribe(queue)
    hub.publish({"t": "state"})
    assert queue.empty()


def test_frames_restore_the_previous_timeline():
    frames = Frames()
    frames.begin()
    frames.put("plate", 0, {"mesh": None, "id": "base"})
    frames.settle()
    assert frames.timeline("plate") == [{"index": 0, "id": "base"}]

    frames.begin()  # a cached build never runs the script, so it puts nothing
    frames.settle()
    assert frames.timeline("plate") == [{"index": 0, "id": "base"}]


# --------------------------------------------------------------------------- static


@pytest.mark.skipif(not (WEB / "index.html").exists(), reason="the web frontend is not written yet")
def test_static_route_serves_the_frontend(tmp_path):
    with Service(write_project(tmp_path), port=0) as svc:
        wait_idle(svc)
        url = svc.url.rstrip("/") + "/static/index.html"
        with urllib.request.urlopen(url, timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/html")
            body = resp.read()
        assert body.lstrip().lower().startswith(b"<!doctype html")


# --------------------------------------------------------------------------- ports


def test_second_service_picks_a_free_port(tmp_path):
    with Service(write_project(tmp_path), port=0) as first:
        with Service(write_project(tmp_path / "two", TWO_PARTS), port=first.port) as second:
            assert second.port != first.port
