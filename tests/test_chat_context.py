"""The chat must always know what is going on: build errors, warnings, parts, parameters, print state."""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cad.chat import format_context, Runner, SYSTEM, API_CARD

REPLY = "Sure."


# --------------------------------------------------------------------------- pure formatting

def test_empty_state_is_empty_text():
    assert format_context(None) == ""
    assert format_context({}) == ""


def test_no_revision_says_so():
    text = format_context({"revision": None})
    assert "no successful build yet" in text


def test_ok_build_reports_revision_and_duration():
    text = format_context({"revision": 7, "build": {"duration_ms": 162.0}})
    assert text.startswith("CURRENT STATE (revision r7)")
    assert "build: ok (162 ms)" in text


def test_failed_build_report_carries_code_message_location_feature_hint():
    text = format_context({
        "revision": 4,
        "error": {
            "code": "FILLET_FAILED",
            "message": "fillet produced no solid body",
            "feature": "corner_fillets",
            "hint": "reduce the radius",
            "details": {"location": {"file": "model.py", "line": 12, "code": "p.fillet(...)"}},
        },
    })
    assert "BUILD FAILED: FILLET_FAILED: fillet produced no solid body" in text
    assert "at model.py:12: p.fillet(...)" in text
    assert "in feature 'corner_fillets'" in text
    assert "hint: reduce the radius" in text


def test_parts_list_includes_size_holes_material_and_references():
    text = format_context({"revision": 1, "parts": [
        {"name": "box", "valid": True, "volume": 37946.2, "bbox": [90, 60, 50], "holes": 8, "material": "PETG"},
        {"name": "envelope", "valid": True, "volume": 10, "bbox": [1, 1, 1], "reference": True},
    ]})
    assert "box: valid=True, 37946.2 mm3, bbox 90 x 60 x 50, 8 holes, PETG" in text
    assert "envelope:" in text and "reference envelope" in text


def test_parameters_mark_overrides_against_their_default():
    text = format_context({"revision": 1, "parameters": {
        "wall": {"value": 2.0, "default": 2.0, "overridden": False},
        "height": {"value": 54, "default": 35, "overridden": True},
    }})
    assert "wall=2.0" in text
    assert "height=54 (override, default 35)" in text


def test_print_summary_reports_fit_thin_walls_and_overhangs():
    text = format_context({
        "revision": 3, "material": "PLA", "bed": "256 x 256 x 256",
        "print": [{"part": "box", "mass_g": 36.45, "fits": True, "thin_min": 0.5, "thin_count": 4,
                   "overhang_area": 84.8, "as_modelled": True}],
    })
    assert "print readiness (PLA, bed 256 x 256 x 256)" in text
    assert "box: 36.45 g, fits, thinnest 0.5 mm on 4 faces below the limit, overhang 84.8 mm2, no print_on" in text


def test_print_summary_reports_overflow_and_a_set_orientation():
    text = format_context({"revision": 3, "print": [
        {"part": "lid", "mass_g": 16.0, "fits": False, "print_down": [0, 0, 1]}]})
    assert "DOES NOT FIT" in text
    assert "print pose set" in text
    assert "no print_on" not in text


def test_warnings_include_the_feature():
    text = format_context({"revision": 5, "warnings": [
        {"code": "ADD_NO_EFFECT", "message": "the added solid lies inside the part", "feature": "shell1"},
        {"code": "THIN_WALL", "message": "4 faces thinner than 0.8 mm"},
    ]})
    assert "ADD_NO_EFFECT [shell1]: the added solid lies inside the part" in text
    assert "THIN_WALL: 4 faces thinner than 0.8 mm" in text


def test_studies_and_joints_are_listed():
    text = format_context({"revision": 1, "studies": ["payload", "pushrod"],
                           "joints": [{"name": "elbow"}, {"name": "wrist"}]})
    assert "stress studies: payload, pushrod" in text
    assert "joints: elbow, wrist" in text


def test_context_stays_bounded_for_very_large_projects():
    parts = [{"name": f"part{i}", "valid": True, "volume": i, "bbox": [1, 2, 3],
              "holes": i, "material": "PETG"} for i in range(400)]
    text = format_context({"revision": 9, "parts": parts})
    assert len(text) <= 4200, "the prompt must stay cheap on a huge assembly"
    assert "part0" in text and "part11" in text
    assert "part12:" not in text, "only the first 12 parts are listed"


def test_context_is_truncated_when_a_single_item_is_enormous():
    text = format_context({"revision": 9, "parts": [{"name": "p" * 6000, "valid": True}]})
    assert len(text) <= 4100
    assert "(truncated)" in text


def test_odd_values_do_not_raise():
    text = format_context({
        "revision": 1,
        "parts": [{"name": None, "bbox": None, "volume": None}],
        "parameters": {"w": {}},
        "print": [{"part": "x"}],
        "warnings": [{"code": None, "message": None}],
        "joints": ["not-a-dict", {"name": "ok"}],
        "studies": [None, "s"],
    })
    assert isinstance(text, str)


# --------------------------------------------------------------------------- fake gateway

class FakeGateway:
    def __init__(self, reply: str = REPLY, chunks: list[str] | None = None):
        self.reply = reply
        self.chunks = chunks if chunks is not None else [reply]
        self.bodies: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.bodies.append(json.loads(self.rfile.read(n)))
                frames = b"".join(
                    b"data: " + json.dumps({"choices": [{"delta": {"content": c}}]}).encode() + b"\n\n"
                    for c in outer.chunks)
                frames += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(frames)))
                self.end_headers()
                self.wfile.write(frames)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def point_at(monkeypatch, fake, tmp_path):
    monkeypatch.setenv("CAD_CHAT_API_KEY", "test-key")
    monkeypatch.setenv("CAD_CHAT_BASE_URL", fake.base)
    monkeypatch.setenv("CAD_CHAT_MODEL", "test-model")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def test_runner_sends_the_project_state_in_the_system_prompt(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text("from cad import Part\n\ndef build():\n    return Part('x').box(1, 1, 1)\n")
    state = "CURRENT STATE (revision r7)\nBUILD FAILED: FILLET_FAILED: nope"
    with FakeGateway() as fake:
        point_at(monkeypatch, fake, tmp_path)
        Runner(tmp_path, lambda k, **p: None, context=lambda: state).run("why did it fail?")

    system = fake.bodies[0]["messages"][0]["content"]
    assert "CURRENT STATE (revision r7)" in system
    assert "FILLET_FAILED" in system
    assert "def build" in system                 # the script is still there
    assert "from cad import Part" in system


def test_runner_survives_a_broken_context_provider(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text("x = 1\n")
    with FakeGateway() as fake:
        point_at(monkeypatch, fake, tmp_path)

        def boom():
            raise RuntimeError("context exploded")

        events = []
        Runner(tmp_path, lambda k, **p: events.append(k), context=boom).run("hi")

    assert fake.bodies, "the request must still be sent"
    assert "chat_error" not in events
    assert "chat_done" in events


def test_no_context_provider_still_works(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text("x = 1\n")
    with FakeGateway() as fake:
        point_at(monkeypatch, fake, tmp_path)
        Runner(tmp_path, lambda k, **p: None).run("hi")
    assert "CURRENT STATE (revision" not in fake.bodies[0]["messages"][0]["content"]


def test_system_template_keeps_every_placeholder():
    text = SYSTEM.format(card=API_CARD, source="SCRIPT", state="STATE")
    assert "SCRIPT" in text and "STATE" in text and "parametric CAD" in text
    assert "trust" in text.lower() or "Trust" in text


# --------------------------------------------------------------------------- real builds

MODEL = '''from cad import Part, Parameter

wall = Parameter("wall", 2.0, min=1.0, max=6.0)


def build():
    p = Part("box")
    p.box(40, 30, 20, name="base")
    p.shell(wall, open_faces=p.faces("base.top"))
    return p
'''


def write_project(tmp_path, source=MODEL):
    (tmp_path / "cad.toml").write_text('[project]\nname = "ctx"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text(source)
    return tmp_path


def wait_idle(svc, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(svc.url.rstrip("/") + "/api/state", timeout=5) as r:
                state = json.loads(r.read())
        except Exception:
            time.sleep(0.05)
            continue
        if not state.get("building"):
            return state
        time.sleep(0.05)
    raise AssertionError("the service never went idle")


def test_builder_context_after_a_good_build(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        text = svc.builder.chat_context()

    assert "CURRENT STATE (revision r1)" in text
    assert "build: ok" in text
    assert "box: valid=True" in text
    assert "XYZ min (-20, -15, 0), max (20, 15, 20) mm" in text
    assert "wall=2.0" in text
    assert "print readiness" in text
    assert "no print_on" in text
    assert "BUILD FAILED" not in text


def test_builder_context_contains_the_build_error_and_keeps_the_last_good_revision(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        (root / "model.py").write_text(MODEL.replace("p.shell(wall", "p.fillet(p.edges().vertical(), 500)\n    p.shell(wall"))
        svc.builder._build(None, True)  # synchronous, so the test can read the result
        text = svc.builder.chat_context()

    assert "CURRENT STATE (revision r1)" in text, "a failed build does not move the revision"
    assert "BUILD FAILED:" in text
    assert "FILLET_FAILED" in text or "OPERATION_FAILED" in text
    assert "model.py:" in text, "the location of the failing line is included"


def test_builder_context_marks_parameter_overrides(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        svc.session.set_params({"wall": 3.5})
        svc.builder._build(None, True)
        text = svc.builder.chat_context()

    assert "wall=3.5 (override, default 2.0)" in text


def test_builder_context_reports_a_set_print_orientation(tmp_path):
    from cad.serve import Service

    source = MODEL.replace("    return p", '    p.print_on("+z")\n    return p')
    root = write_project(tmp_path, source)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        text = svc.builder.chat_context()

    assert "print pose set" in text
    assert "no print_on" not in text


def test_builder_context_before_any_revision(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path, "raise RuntimeError('broken on purpose')\n")
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        for _ in range(100):
            if svc.builder.last_error:
                break
            time.sleep(0.05)
        text = svc.builder.chat_context()

    assert "no successful build yet" in text
    assert "BUILD FAILED" in text
    assert isinstance(text, str) and text


def test_service_wires_the_context_provider(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        assert svc.chat.context is not None
        assert "CURRENT STATE" in svc.chat.context()


# --------------------------------------------------------------------------- dirty / transactional

def test_dirty_note_explains_that_the_viewport_is_behind_the_script():
    text = format_context({"revision": 5, "build": {"duration_ms": 100}, "dirty": True})
    assert "has changed since r5 was built" in text
    assert "still showing r5" in text


def test_failed_build_note_points_at_the_last_good_revision():
    text = format_context({"revision": 5, "error": {"code": "SCRIPT_ERROR", "message": "boom"}})
    assert "BUILD FAILED" in text
    assert "still shows r5, the last good build" in text


def test_builder_context_marks_unbuilt_edits(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        (root / "model.py").write_text(MODEL.replace("40, 30, 20", "50, 30, 20"))
        text = svc.builder.chat_context()

    assert "has changed since r1 was built" in text


def test_builder_context_uses_the_live_overlay_even_before_a_rebuild(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        svc.session.set_params({"wall": 4.0})  # writes params.toml, no build
        text = svc.builder.chat_context()

    assert "wall=4.0 (override, default 2.0)" in text


def test_a_chat_edit_that_does_not_build_never_reaches_live_verification(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    original = (root / "model.py").read_text()
    reply = "Here you go.\n\n```python\n" + MODEL.replace("return p", "return undefined_name") + "```\n"
    with FakeGateway(reply) as fake:
        point_at(monkeypatch, fake, tmp_path)
        events = []
        verified = []
        Runner(root, lambda k, **p: events.append({"t": k, **p}),
               verify=lambda: verified.append(True)).run("break it")

    kinds = [e["t"] for e in events]
    assert "chat_applied" not in kinds
    err = next(e for e in events if e["t"] == "chat_error")
    assert err["code"] == "SCRIPT_ERROR"
    assert err["outcome"]["validation"]["ok"] is False
    assert not verified
    assert (root / "model.py").read_text() == original, "the broken edit must not stay on disk"


def test_a_chat_edit_that_builds_is_kept_and_verified(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    new_model = MODEL.replace('Parameter("wall", 2.0', 'Parameter("wall", 3.0')
    reply = "Thicker walls.\n\n```python\n" + new_model + "```\n"
    with FakeGateway(reply) as fake:
        point_at(monkeypatch, fake, tmp_path)
        events = []
        Runner(root, lambda k, **p: events.append({"t": k, **p}),
               verify=lambda: {"ok": True, "revision": 2}).run("thicken")

    applied = next(e for e in events if e["t"] == "chat_applied")
    assert applied["verified"] is True
    assert applied["revision"] == 2
    assert "Parameter(\"wall\", 3.0" in (root / "model.py").read_text()


def test_shifted_part_exposes_model_space_datums():
    part = {"name": "pad", "bbox": {"size": [20, 12, 4], "min": [5, -6, 10], "max": [25, 6, 14]}}
    assert "XYZ min (5, -6, 10), max (25, 6, 14) mm" in format_context({"parts": [part]})
