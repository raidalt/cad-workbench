"""Cross-cutting reliability: a new model must not inherit old parameters, and the
chat must know when the person is looking at a historical revision."""

import json
import time
import urllib.error
import urllib.request

MODEL = '''from cad import Part, Parameter

wall = Parameter("wall", 2.0, min=1.0, max=8.0)


def build():
    p = Part("plate")
    p.box(40, 30, 4, name="base")
    p.shell(wall, open_faces=p.faces("base.top"))
    return p
'''


def write_project(tmp_path):
    (tmp_path / "cad.toml").write_text('[project]\nname = "integration"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text(MODEL)
    return tmp_path


def call(svc, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(svc.url.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_idle(svc, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        _, state = call(svc, "/api/state")
        if not state.get("building"):
            return state
        time.sleep(0.05)
    raise AssertionError("service never went idle")


def test_new_model_does_not_inherit_stale_parameter_overrides(tmp_path):
    from cad.project import read_overlay
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        svc.session.set_params({"wall": 4.5})
        svc.builder._build(None, True)
        assert read_overlay(root) == {"wall": 4.5}

        code, body = call(svc, "/api/new-model", {})
        assert code == 200 and body["ok"] is True, body
        assert body["cleared_overrides"] == ["wall"]
        assert read_overlay(root) == {}, "a fresh model starts with no overrides"

        state = wait_idle(svc)
        assert state["empty"] is True
        assert not [w for w in state.get("warnings") or [] if w.get("code") == "UNKNOWN_PARAMETER"]
        assert (root / ".cad" / "backups").glob("params-*.toml"), "the old params.toml is kept as a backup"


def test_chat_context_names_the_revision_being_viewed(tmp_path):
    from cad.serve import Service

    root = write_project(tmp_path)
    with Service(root, port=0, poll=60) as svc:
        wait_idle(svc)
        svc.builder._build(None, True)  # force -> r2, same content
        assert svc.session.store.head() == 2

        text = svc.builder.chat_context(viewing=1)
        assert "looking at r1" in text
        assert "head is r2" in text
        assert "does not change r1" in text

        same = svc.builder.chat_context(viewing=2)
        assert "looking at r2" not in same, "no note when the person is already on head"


# --------------------------------------------------------------------------- failure receipts

def _fake_gateway(reply: str):
    """A minimal OpenAI-compatible SSE server returning one code-block reply."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            frames = (b"data: " + json.dumps({"choices": [{"delta": {"content": reply}}]}).encode() + b"\n\n"
                      b"data: [DONE]\n\n")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_reverted_receipt_records_the_revision_that_was_on_screen(tmp_path, monkeypatch):
    """The failure receipt keeps `active_revision` from the engine, not from the current view."""
    from cad.chat import Runner, history

    root = write_project(tmp_path)
    candidate = "from cad import Part\n\ndef build():\n    p = Part('x')\n    p.box(1, 1, 1)\n    return p\n"
    reply = f"Trying.\n\n```python\n{candidate}```\n"
    srv = _fake_gateway(reply)
    try:
        monkeypatch.setenv("CAD_CHAT_API_KEY", "k")
        monkeypatch.setenv("CAD_CHAT_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
        monkeypatch.setenv("CAD_CHAT_MODEL", "fake")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        verify = lambda: {"ok": False, "error": {"code": "SCRIPT_ERROR", "message": "boom",
                                                 "details": {"active_revision": 1}}}
        Runner(root, lambda k, **p: None, verify=verify).run("change it")
    finally:
        srv.shutdown()
        srv.server_close()

    outcome = history(root)[-1]["outcome"]
    assert outcome["reverted"] is True
    assert outcome["revision"] is None, "no revision was created"
    assert outcome["active_revision"] == 1, "r1 is what stayed on screen"


def test_a_failure_without_an_engine_revision_omits_the_field(tmp_path, monkeypatch):
    """Legacy/other failures must not invent a shown revision."""
    from cad.chat import Runner, history

    root = write_project(tmp_path)
    reply = "Trying.\n\n```python\nfrom cad import Part\n\ndef build():\n    return Part('x').box(1, 1, 1)\n```\n"
    srv = _fake_gateway(reply)
    try:
        monkeypatch.setenv("CAD_CHAT_API_KEY", "k")
        monkeypatch.setenv("CAD_CHAT_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
        monkeypatch.setenv("CAD_CHAT_MODEL", "fake")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        verify = lambda: {"ok": False, "error": {"code": "SCRIPT_ERROR", "message": "boom"}}
        Runner(root, lambda k, **p: None, verify=verify).run("change it")
    finally:
        srv.shutdown()
        srv.server_close()

    outcome = history(root)[-1]["outcome"]
    assert outcome["active_revision"] is None
