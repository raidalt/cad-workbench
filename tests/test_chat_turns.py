"""Chat turns are records, not fire-and-forget: the build decides what happened.

A reply that writes a script is not a successful CAD change; the build is the
authority.  Every turn keeps its id and its receipt in the history (including
failed turns), and a retry replaces the turn instead of duplicating it.

The gateway is faked with the stdlib (an OpenAI-compatible SSE endpoint on a
free localhost port), copied from ``test_chat_context.py`` so these tests never
touch the network beyond localhost and need no API key.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cad.serve import Service

MODEL = '''from cad import Part


def build():
    p = Part("plate")
    p.box(20, 20, 5, name="base")
    return p
'''

NEW_MODEL = '''from cad import Part


def build():
    p = Part("plate")
    p.box(20, 20, 5, name="base")
    p.hole(4, at=(0, 0), name="bore")
    return p
'''


# --------------------------------------------------------------------------- fake gateway


def _sse(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


@dataclass
class Reply:
    chunks: tuple[str, ...] = ("Sure.",)
    thinking: str | None = None

    def frames(self) -> bytes:
        out = b""
        if self.thinking:
            out += _sse({"choices": [{"delta": {"reasoning_content": self.thinking}}]})
        for chunk in self.chunks:
            out += _sse({"choices": [{"delta": {"content": chunk}}]})
        return out + b"data: [DONE]\n\n"


def _handler(fake: "FakeGateway"):
    class FakeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet
            pass

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            fake.seen.append(payload)
            message = ""
            for m in reversed(payload.get("messages") or []):
                if m.get("role") == "user":
                    message = m.get("content") or ""
                    break
            frames = fake.responder(message).frames()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)

    return FakeHandler


class FakeGateway:
    def __init__(self, responder=None) -> None:
        self.responder = responder or (lambda message: Reply())
        self.seen: list[dict] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="fake-gateway", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def __enter__(self) -> "FakeGateway":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def point_at(monkeypatch, fake: FakeGateway, tmp_path: Path) -> None:
    monkeypatch.setenv("CAD_CHAT_API_KEY", "test-key")
    monkeypatch.setenv("CAD_CHAT_BASE_URL", fake.url)
    monkeypatch.setenv("CAD_CHAT_MODEL", "test-model")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


# --------------------------------------------------------------------------- service helpers


def write_project(root: Path, model: str = MODEL, name: str = "demo") -> Path:
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


def read_until(stream, kind: str, deadline: float = 15.0) -> list[dict]:
    """Read SSE events until ``kind`` arrives; return every event read so far."""
    events: list[dict] = []
    name = None
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        line = stream.readline()
        if not line:
            break
        text = line.decode().rstrip("\n")
        if text.startswith("event: "):
            name = text[len("event: "):]
        elif text.startswith("data: "):
            event = json.loads(text[len("data: "):])
            assert event.get("t") == name
            events.append(event)
            if name == kind:
                return events
    raise AssertionError(f"no {kind} event within {deadline}s; got {[e['t'] for e in events]}")


def chat_events(events: list[dict]) -> list[dict]:
    return [e for e in events if str(e.get("t", "")).startswith("chat_")]


def run_turn(svc: Service, message: str, retry_turn: str | None = None, until: str = "chat_done",
             wait_done: bool = True) -> tuple[dict, list[dict]]:
    stream = urllib.request.urlopen(svc.url.rstrip("/") + "/api/events", timeout=20)
    try:
        payload = {"message": message}
        if retry_turn is not None:
            payload["retry_turn"] = retry_turn
        code, body = call(svc, "/api/chat", payload)
        assert code == 200, body
        events = chat_events(read_until(stream, until))
        if wait_done and until != "chat_done":
            events += chat_events(read_until(stream, "chat_done"))
        return body, events
    finally:
        stream.close()


# --------------------------------------------------------------------------- success


def test_success_turn_keeps_ids_and_the_build_receipt(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    reply = "Adding a hole.\n\n```python\n" + NEW_MODEL + "```\n"
    with FakeGateway(lambda message: Reply(chunks=(reply,))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            body, events = run_turn(svc, "add a hole")

            assert body["ok"] is True and body["started"] is True
            assert body["turn"]
            assert events[0]["t"] == "chat_start"
            assert events[0]["turn"] == body["turn"]
            assert events[0]["model"] == "test-model"

            applied = next(e for e in events if e["t"] == "chat_applied")
            assert applied["verified"] is True
            assert applied["revision"] == 2
            assert applied["file"] == "model.py" and applied["bytes"] > 0

            done = events[-1]
            assert done["t"] == "chat_done" and done["applied"] is True
            assert done["turn"] == body["turn"]
            assert done["outcome"]["applied"] is True
            assert done["outcome"]["verified"] is True
            assert done["outcome"]["revision"] == 2
            assert done["outcome"]["reverted"] is False
            assert done["outcome"]["error"] is None

            history = call(svc, "/api/chat")[1]["history"]
            assert [m["role"] for m in history] == ["user", "assistant"]
            assert all(m.get("id") for m in history)
            assert history[0]["id"] == body["turn"]
            assert history[0]["content"] == "add a hole"
            assert history[1]["outcome"] == done["outcome"], "the receipt is the persisted one"
            assert not history[1].get("failed")

            assert (root / "model.py").read_text() == NEW_MODEL


def test_history_ids_and_receipts_survive_a_reload(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    reply = "Adding a hole.\n\n```python\n" + NEW_MODEL + "```\n"
    with FakeGateway(lambda message: Reply(chunks=(reply,))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            _, events = run_turn(svc, "add a hole")
            first = call(svc, "/api/chat")[1]["history"]
        with Service(root, port=0, poll=60) as svc:
            second = call(svc, "/api/chat")[1]["history"]

    assert first == second, "a new Service reads the same turns from .cad/chat.json"
    assert first[0]["id"] == events[-1]["turn"]
    assert first[1]["outcome"] == events[-1]["outcome"]


# --------------------------------------------------------------------------- failed build


def test_a_candidate_that_fails_is_persisted_without_a_live_build(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    original = (root / "model.py").read_bytes()
    broken = MODEL.replace("return p", "return undefined_name")
    reply = "Here it is.\n\n```python\n" + broken + "```\n"
    with FakeGateway(lambda message: Reply(chunks=(reply,))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            body, events = run_turn(svc, "break it", until="chat_error", wait_done=False)

            error = next(e for e in events if e["t"] == "chat_error")
            assert error["turn"] == body["turn"]
            assert error["code"] == "SCRIPT_ERROR"
            assert "chat_applied" not in [e["t"] for e in events]
            assert error["outcome"]["validation"]["ok"] is False
            assert error["outcome"]["reverted"] is False
            assert error["outcome"]["applied"] is False
            assert error["outcome"]["revision"] is None
            assert list((root / ".cad" / "backups").glob("*")) == []

            assert (root / "model.py").read_bytes() == original, "the broken edit did not stay on disk"
            assert [r["n"] for r in call(svc, "/api/state")[1]["revisions"]] == [1], \
                "a reverted edit publishes no revision"

            history = call(svc, "/api/chat")[1]["history"]
            assert [m["role"] for m in history] == ["user", "assistant"]
            assert history[1]["failed"] is True
            assert history[1]["outcome"] == error["outcome"]


# --------------------------------------------------------------------------- no text


def test_a_reasoning_only_reply_is_persisted_and_enters_the_next_prompt(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    long_thought = "I am still thinking about the geometry but said nothing useful."

    def responder(message: str) -> Reply:
        if "make it bigger" in message:
            return Reply(chunks=(), thinking=long_thought)
        return Reply(chunks=("Still here.",))

    with FakeGateway(responder) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            body, events = run_turn(svc, "make it bigger", until="chat_error", wait_done=False)

            error = next(e for e in events if e["t"] == "chat_error")
            assert error["message"] == "the model returned no text"
            assert error["turn"] == body["turn"]
            assert error["code"] == "NO_TEXT"

            history = call(svc, "/api/chat")[1]["history"]
            assert history[1]["failed"] is True
            assert history[1]["content"] == ""
            assert history[1]["outcome"]["error"]["code"] == "NO_TEXT"

            _, events2 = run_turn(svc, "what happened?")
            assert events2[-1]["outcome"]["applied"] is False

            system = fake.seen[-1]["messages"][0]["content"]
            assert "make it bigger" in system, "the next turn is told about the failed turn"
            assert "RECENT FAILED TURNS" in system
            assert "NO_TEXT" in system

            history = call(svc, "/api/chat")[1]["history"]
            assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
            assert history[1]["failed"] is True, "the failed turn is still there on the next turn"


# --------------------------------------------------------------------------- retry


def test_retry_replaces_the_turn_without_duplicating_or_writing_twice(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    reply = "Adding a hole.\n\n```python\n" + NEW_MODEL + "```\n"
    with FakeGateway(lambda message: Reply(chunks=(reply,))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            body, events = run_turn(svc, "add a hole")
            turn = body["turn"]
            assert events[-1]["outcome"]["applied"] is True

            on_disk = (root / "model.py").read_bytes()
            backups = len(list((root / ".cad" / "backups").glob("model-*.py")))
            revisions = [r["n"] for r in call(svc, "/api/state")[1]["revisions"]]

            retry_body, retry_events = run_turn(svc, "add a hole", retry_turn=turn)

            assert retry_body["turn"] == turn, "the turn keeps its id"
            assert retry_events[-1]["outcome"]["applied"] is True
            assert (root / "model.py").read_bytes() == on_disk, "the script is not written twice"
            assert len(list((root / ".cad" / "backups").glob("model-*.py"))) == backups
            assert [r["n"] for r in call(svc, "/api/state")[1]["revisions"]] == revisions

            history = call(svc, "/api/chat")[1]["history"]
            users = [m for m in history if m["role"] == "user"]
            assert len(users) == 1, "the retried turn replaces the old one"
            assert users[0]["id"] == turn
            assert len(history) == 2
            assert history[1]["outcome"]["applied"] is True


def test_retry_of_a_failed_turn_works(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    calls = {"n": 0}

    def responder(message: str) -> Reply:
        calls["n"] += 1
        if calls["n"] == 1:
            return Reply(chunks=(), thinking="only thoughts the first time")
        return Reply(chunks=("No change.",))

    with FakeGateway(responder) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            body, _ = run_turn(svc, "retry me", until="chat_error", wait_done=False)
            turn = body["turn"]

            failed = call(svc, "/api/chat")[1]["history"]
            assert len(failed) == 2 and failed[1]["failed"] is True

            retry_body, retry_events = run_turn(svc, "retry me", retry_turn=turn)
            assert retry_body["turn"] == turn
            assert retry_events[-1]["outcome"]["applied"] is False
            assert fake.seen[-1]["messages"][-1] == {"role": "user", "content": "retry me"}

            history = call(svc, "/api/chat")[1]["history"]
            assert [m["role"] for m in history] == ["user", "assistant"]
            assert history[0]["content"] == "retry me"
            assert not history[1].get("failed")


def test_new_chat_preserves_model_and_removes_context(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    release_reply = threading.Event()
    reply_requested = threading.Event()

    def respond(message):
        if message == "add a hole and remember GALLERY-OLD-CONTEXT":
            reply_requested.set()
            assert release_reply.wait(10)
            return Reply(chunks=("Done.\n\n```python\n" + NEW_MODEL + "```\n",))
        return Reply()

    with FakeGateway(respond) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            with urllib.request.urlopen(svc.url + "api/events", timeout=20) as events:
                initial = call(svc, "/api/chat")[1]
                status, _ = call(svc, "/api/chat", {"message": "add a hole and remember GALLERY-OLD-CONTEXT"})
                assert status == 200
                try:
                    assert reply_requested.wait(10)
                    assert call(svc, "/api/chat/reset", {"chat_id": initial["chat_id"]})[0] == 400
                finally:
                    release_reply.set()
                assert read_until(events, "chat_done")[-1]["outcome"]["verified"]
                deadline = time.monotonic() + 5
                while call(svc, "/api/chat")[1]["running"] and time.monotonic() < deadline:
                    time.sleep(.01)
                before = call(svc, "/api/state")[1]
                source = (root / "model.py").read_bytes()
                assert source == NEW_MODEL.encode()
                assert len(call(svc, "/api/chat")[1]["history"]) == 2
                status, reset = call(svc, "/api/chat/reset", {"chat_id": initial["chat_id"]})
                assert status == 200
                assert reset["chat_id"] != initial["chat_id"]
                assert read_until(events, "chat_reset")[-1]["chat_id"] == reset["chat_id"]
                assert call(svc, "/api/chat")[1]["history"] == []
                assert (root / "model.py").read_bytes() == source
                assert call(svc, "/api/state")[1]["revision"] == before["revision"]
                assert call(svc, "/api/chat", {"message": "stale tab", "chat_id": initial["chat_id"]})[0] == 409
        with Service(root, port=0, poll=60) as svc:
            after = call(svc, "/api/chat")[1]
            assert after["chat_id"] == reset["chat_id"] and after["history"] == []
            run_turn(svc, "Describe this model")
            assert "GALLERY-OLD-CONTEXT" not in json.dumps(fake.seen[-1])
            assert call(svc, "/api/chat")[1]["chat_id"] == reset["chat_id"]
            assert call(svc, "/api/chat/undo", {})[0] == 200
            assert (root / "model.py").read_bytes() == MODEL.encode()


def test_project_gallery_creates_renames_and_opens_isolated_workspace(tmp_path):
    root = write_project(tmp_path / "original", name="Original")
    with Service(root, port=0, poll=60, library=tmp_path) as svc:
        end = time.monotonic() + 15
        while not call(svc, "/api/state")[1].get("revision"):
            assert time.monotonic() < end
            time.sleep(.03)
        with urllib.request.urlopen(svc.url + "projects") as page:
            assert b"<h1>Projects</h1>" in page.read()
        original, = call(svc, "/api/projects")[1]["projects"]
        assert original["name"] == "Original"
        with urllib.request.urlopen(svc.url.rstrip("/") + original["preview"]) as image:
            assert image.headers.get_content_type() == "image/png"
            assert image.read(8) == b"\x89PNG\r\n\x1a\n"

        status, created = call(svc, "/api/projects/create", {"name": 'Desk tray "v2"'})
        assert status == 201
        key = created["key"]
        assert call(svc, "/api/projects/create", {"name": 'Desk tray "v2"'})[1]["key"] != key
        assert call(svc, "/api/projects/rename", {"key": key, "name": "Cable tray"})[0] == 200
        assert call(svc, "/api/projects/open", {"key": ".."})[0] == 404
        assert call(svc, "/api/projects/open", {"key": "original"})[1]["url"] == "/projects/original/"

        status, opened = call(svc, "/api/projects/open", {"key": key})
        assert status == 200, opened
        assert call(svc, "/api/projects/open", {"key": key})[1] == opened
        with urllib.request.urlopen(urllib.parse.urljoin(svc.url, opened["url"]) + "api/state") as state:
            child = json.load(state)
        assert Path(child["root"]) == tmp_path / key
        assert child["empty"] and child["project_title"] == "Cable tray"
        with urllib.request.urlopen(urllib.parse.urljoin(svc.url, opened["url"]) + "projects") as gallery:
            assert gallery.url == svc.url + "projects"
        assert call(svc, "/api/state")[1]["revision"] == original["revision"]
        assert (root / "model.py").read_text() == MODEL


def test_project_routes_survive_worker_exit_and_keep_projects_separate(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    one = write_project(tmp_path / "one", name="One")
    two = write_project(tmp_path / "two", name="Two")
    with Service(one, port=0, library=tmp_path) as svc:
        def built(key):
            end = time.monotonic() + 20
            while time.monotonic() < end:
                state = call(svc, f"/projects/{key}/api/state")[1]
                if state.get("revision"):
                    return state
                time.sleep(.05)
            raise AssertionError(f"{key} did not build")
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(built, ["one", "two"]))
        assert [Path(state["root"]) for state in states] == [one, two]
        assert set(svc.gallery._children) == {one, two}
        worker = svc.gallery.worker(two)
        worker.process.kill()
        worker.process.wait()
        assert built("one")["revision"] == states[0]["revision"]
        assert built("two")["revision"] == states[1]["revision"]
        assert svc.gallery.worker(two).process.pid != worker.process.pid
