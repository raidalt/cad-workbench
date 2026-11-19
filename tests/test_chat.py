"""Tests for the in-app chat backend: config, script extraction, edits, undo, streaming.

The gateway is faked by a tiny stdlib HTTP server on port 0 that speaks the
OpenAI Chat Completions SSE dialect at ``POST /v1/chat/completions`` — three
content deltas, an optional ``reasoning_content`` delta, a possible error event,
and ``data: [DONE]``.  Nothing here touches the network beyond localhost and no
real API key is needed: every test points ``CAD_CHAT_BASE_URL`` at the fake.

The HTTP route tests use one ``Service`` on port 0 over a throwaway project,
exactly like ``test_serve.py``.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cad.chat import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ENV_KEYS,
    MAX_HISTORY,
    Runner,
    apply_script,
    config,
    config_path,
    extract_script,
    history,
    load_config,
    save_history,
    undo,
)
from cad.errors import CadError
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

TEST_KEY = "test-key"
TEST_MODEL = "test-model"


# --------------------------------------------------------------------------- fake gateway


def _sse(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


@dataclass
class Reply:
    """What the fake gateway answers: SSE chunks, optional reasoning, failures."""

    chunks: tuple[str, ...] = ("Hello", " from the", " fake model.")
    thinking: str | None = None
    status: int = 200
    error: str | None = None  # body of an HTTP failure: {"error": {"message": ...}}
    stream_error: str | None = None  # an SSE error event in the middle of the stream
    delay: float = 0.0
    finish_reason: str | None = None

    def frames(self) -> list[bytes]:
        out: list[bytes] = []
        if self.thinking:
            out.append(_sse({"choices": [{"delta": {"reasoning_content": self.thinking}}]}))
        out.extend(_sse({"choices": [{"delta": {"content": chunk}}]}) for chunk in self.chunks)
        if self.stream_error:
            out.append(_sse({"error": {"message": self.stream_error}}))
        else:
            if self.finish_reason:
                out.append(_sse({"choices": [{"delta": {}, "finish_reason": self.finish_reason}]}))
            out.append(b"data: [DONE]\n\n")
        return out


def _handler(fake: "FakeGateway"):
    class FakeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _body(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — the gateway has no GET routes
            return self._body(404, b'{"error": {"message": "no GET routes"}}')

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/v1/chat/completions":
                return self._body(404, b'{"error": {"message": "not found"}}')
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) or b"{}"
            payload = json.loads(raw)
            fake.seen.append({"headers": {k.lower(): v for k, v in self.headers.items()},
                              "body": payload})
            message = ""
            for m in reversed(payload.get("messages") or []):
                if m.get("role") == "user":
                    message = m.get("content") or ""
                    break
            reply = fake.responder(message)
            if reply.status != 200:
                body = json.dumps({"error": {"message": reply.error or "boom"}}).encode()
                return self._body(reply.status, body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for frame in reply.frames():
                    self.wfile.write(frame)
                    self.wfile.flush()
                    if reply.delay:
                        time.sleep(reply.delay)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return FakeHandler


class FakeGateway:
    """An OpenAI-compatible endpoint over a free localhost port, streamed as SSE."""

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


def point_at(monkeypatch, fake: FakeGateway) -> None:
    """Send chat traffic to the fake and nowhere else."""
    monkeypatch.setenv("CAD_CHAT_API_KEY", TEST_KEY)
    monkeypatch.setenv("CAD_CHAT_BASE_URL", fake.url)
    monkeypatch.setenv("CAD_CHAT_MODEL", TEST_MODEL)


def clear_env(monkeypatch) -> None:
    """No config from the developer's shell: tests own every override."""
    for key in ENV_KEYS + ("CAD_CHAT_BASE_URL", "CAD_CHAT_MODEL", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)


def dead_url() -> str:
    """A localhost URL nobody is listening on (bind, read the port, close)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------- service helpers


def write_project(root: Path, model: str = MODEL) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cad.toml").write_text('[project]\nname = "demo"\nentry = "model.py"\n')
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


def wait_idle(svc: Service, deadline: float = 10.0) -> dict:
    end = time.monotonic() + deadline
    state: dict = {}
    while time.monotonic() < end:
        state = call(svc, "/api/state")[1]
        if not state["building"]:
            return state
        time.sleep(0.01)
    raise AssertionError(f"build did not settle within {deadline}s; state={state}")


def read_until(stream, kind: str, deadline: float = 8.0) -> list[dict]:
    """Read SSE events until ``kind`` arrives; return every event read so far."""
    events: list[dict] = []
    name = None
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            line = stream.readline()
        except (TimeoutError, OSError) as e:  # the stream stalled
            raise AssertionError(f"no {kind} event within {deadline}s ({e}); got {[e['t'] for e in events]}")
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


def wait_until(predicate, deadline: float = 5.0, what: str = "condition") -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"{what} did not happen within {deadline}s")


def chat_events(events: list[dict]) -> list[dict]:
    return [e for e in events if str(e.get("t", "")).startswith("chat_")]


# --------------------------------------------------------------------------- config


def test_config_without_a_key_is_unavailable(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = config()

    assert cfg["available"] is False
    assert cfg["provider"] == "vercel-ai-gateway"
    assert cfg["url"] == DEFAULT_BASE_URL
    assert cfg["model"] == DEFAULT_MODEL
    assert cfg["models"] == [DEFAULT_MODEL]
    assert cfg["config_path"] == str(tmp_path / "cad" / "chat.toml")
    assert cfg["config_path"].endswith("cad/chat.toml")
    assert cfg["config_path"] in cfg["reason"], "the reason must name the config file"
    assert "API key" in cfg["reason"]


def test_load_config_reads_the_toml_and_env_overrides_it(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    xdg = tmp_path / "xdg"
    toml = xdg / "cad" / "chat.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text(
        'api_key = "file-key"\n'
        'base_url = "http://127.0.0.1:9999/v1"\n'
        'model = "file-model"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert config_path() == toml
    cfg = load_config()
    assert cfg == {"api_key": "file-key", "base_url": "http://127.0.0.1:9999/v1", "model": "file-model"}
    assert config()["available"] is True

    monkeypatch.setenv("CAD_CHAT_API_KEY", "env-key")
    monkeypatch.setenv("CAD_CHAT_BASE_URL", "http://127.0.0.1:1/v1/")
    monkeypatch.setenv("CAD_CHAT_MODEL", "env-model")
    cfg = load_config()
    assert cfg["api_key"] == "env-key", "env beats the file"
    assert cfg["base_url"] == "http://127.0.0.1:1/v1", "trailing slash is stripped"
    assert cfg["model"] == "env-model"


def test_load_config_accepts_the_gateway_env_names(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "vercel-key")

    assert load_config()["api_key"] == "vercel-key"
    assert config()["available"] is True

    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gateway-key")
    assert load_config()["api_key"] == "gateway-key"

    monkeypatch.setenv("CAD_CHAT_API_KEY", "cad-key")
    assert load_config()["api_key"] == "cad-key", "CAD_CHAT_API_KEY wins"


def test_load_config_survives_a_broken_toml(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    xdg = tmp_path / "xdg"
    toml = xdg / "cad" / "chat.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text("api_key = \n")  # TOMLDecodeError
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    cfg = load_config()

    assert cfg == {"api_key": None, "base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL}
    assert config()["available"] is False


# --------------------------------------------------------------------------- extract_script


def test_extract_script_finds_the_python_block():
    reply = "Here you go:\n\n```python\nfrom cad import Part\n\n\ndef build():\n    return Part('x')\n```\n"
    script = extract_script(reply)
    assert script is not None
    assert script.startswith("from cad import Part")
    assert script.endswith("\n")
    assert "def build" in script


def test_extract_script_ignores_bash_and_blocks_without_build():
    reply = (
        "Run this:\n```bash\npython model.py\n```\n"
        "And this fragment is not a whole script:\n```python\nx = 1\ny = 2\n```\n"
    )
    assert extract_script(reply) is None


def test_extract_script_returns_none_without_a_block():
    assert extract_script("I changed nothing, the part is fine.") is None


def test_extract_script_prefers_the_last_valid_block():
    reply = (
        "First attempt:\n```python\ndef build():\n    return old\n```\n"
        "Actually, use this:\n```python\ndef build():\n    return new\n```\n"
    )
    script = extract_script(reply)
    assert script is not None
    assert "return new" in script and "return old" not in script


def test_extract_script_accepts_py_and_untagged_blocks():
    py = "```py\ndef build():\n    return 1\n```"
    untagged = "```\ndef build():\n    return 2\n```"
    assert "return 1" in extract_script(py)
    assert "return 2" in extract_script(untagged)


# --------------------------------------------------------------------------- apply_script


def test_apply_script_writes_and_backs_up(tmp_path):
    (tmp_path / "model.py").write_text(MODEL)

    info = apply_script(tmp_path, NEW_MODEL)

    assert info["file"] == "model.py"
    assert info["bytes"] == len(NEW_MODEL)
    assert (tmp_path / "model.py").read_text() == NEW_MODEL

    backup = Path(info["backup"])
    assert backup.exists()
    assert backup.read_text() == MODEL, "the backup must hold the old file"
    assert backup.parent == tmp_path / ".cad" / "backups"


def test_apply_script_refuses_a_broken_script(tmp_path):
    (tmp_path / "model.py").write_text(MODEL)

    with pytest.raises(CadError) as exc:
        apply_script(tmp_path, "def build(:\n    pass\n")

    assert exc.value.code == "INVALID_ARGUMENT"
    assert (tmp_path / "model.py").read_text() == MODEL, "a broken script must not touch disk"
    assert not (tmp_path / ".cad" / "backups").exists(), "no backup is created for a refusal"


def test_apply_script_creates_the_backup_directory(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()

    info = apply_script(root, NEW_MODEL)

    assert (root / ".cad" / "backups").is_dir()
    assert info["backup"] is None, "a first write has nothing to back up"
    assert (root / "model.py").read_text() == NEW_MODEL


# --------------------------------------------------------------------------- undo


def test_undo_restores_the_previous_file(tmp_path):
    (tmp_path / "model.py").write_text(MODEL)
    info = apply_script(tmp_path, NEW_MODEL)
    backup = Path(info["backup"])
    assert backup.exists()

    restored = undo(tmp_path)

    assert restored["restored"] == "model.py"
    assert (tmp_path / "model.py").read_text() == MODEL
    assert not backup.exists(), "the backup that was used is consumed"


def test_undo_with_nothing_to_restore_is_not_found(tmp_path):
    with pytest.raises(CadError) as exc:
        undo(tmp_path)
    assert exc.value.code == "NOT_FOUND"
    assert "undo" in exc.value.message


# --------------------------------------------------------------------------- history


def test_history_keeps_the_newest_messages_in_order(tmp_path):
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(50)]

    save_history(tmp_path, messages)
    kept = history(tmp_path)

    assert len(kept) == MAX_HISTORY == 40
    assert [m["content"] for m in kept] == [f"m{i}" for i in range(10, 50)]


def test_history_of_a_corrupt_file_is_empty(tmp_path):
    (tmp_path / ".cad").mkdir()
    (tmp_path / ".cad" / "chat.json").write_text("{ this is not json")

    assert history(tmp_path) == []


def test_history_of_a_non_dict_file_is_empty(tmp_path):
    (tmp_path / ".cad").mkdir()

    for body in ("[]", "null", "7", '"text"'):
        (tmp_path / ".cad" / "chat.json").write_text(body)
        assert history(tmp_path) == [], f"{body!r} is not a history file"


def test_history_missing_file_is_empty(tmp_path):
    assert history(tmp_path) == []


# --------------------------------------------------------------------------- Runner


def test_runner_streams_without_applying_a_plain_reply(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    with FakeGateway() as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []

        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert names[0] == "chat_start"
    assert events[0]["model"] == TEST_MODEL
    assert names.count("chat_delta") >= 1  # deltas are coalesced before they are emitted
    assert "chat_metrics" in names
    assert "".join(e["text"] for e in events if e["t"] == "chat_delta") == "Hello from the fake model."
    assert names[-1] == "chat_done"
    assert events[-1]["applied"] is False
    assert "chat_applied" not in names
    assert "chat_thinking" not in names
    assert (tmp_path / "model.py").read_text() == MODEL

    assert len(fake.seen) == 1, "one request per message"
    sent = fake.seen[0]
    assert sent["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    assert sent["body"]["model"] == TEST_MODEL
    assert sent["body"]["stream"] is True
    system = sent["body"]["messages"][0]
    assert system["role"] == "system"
    assert "current model.py" in system["content"] and "def build" in system["content"]
    assert sent["body"]["messages"][-1] == {"role": "user", "content": "hi"}


def test_runner_reports_thinking_before_the_answer(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    reply = Reply(chunks=("All set.",), thinking="checking the geometry")
    with FakeGateway(lambda message: reply) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert [n for n in names if n != "chat_metrics"] == ["chat_start", "chat_thinking", "chat_delta", "chat_done"]
    assert [e["text"] for e in events if e["t"] == "chat_thinking"] == ["checking the geometry"]
    assert "".join(e["text"] for e in events if e["t"] == "chat_delta") == "All set."
    assert events[-1]["applied"] is False


def test_runner_applies_a_valid_code_block(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    reply = Reply(chunks=("Adding a hole.\n", "\n```python\n" + NEW_MODEL + "```\n"))
    with FakeGateway(lambda message: reply) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("add a hole")

    names = [e["t"] for e in events]
    assert names[0] == "chat_start"
    assert "chat_applied" in names
    assert [n for n in names if n != "chat_metrics"][-2:] == ["chat_applied", "chat_done"], "apply before done"
    assert events[-1]["applied"] is True
    applied_ev = next(e for e in events if e["t"] == "chat_applied")
    assert applied_ev["file"] == "model.py"
    assert Path(applied_ev["backup"]).exists()

    written = (tmp_path / "model.py").read_text()
    assert written == NEW_MODEL.strip() + "\n"
    assert (tmp_path / ".cad" / "backups").is_dir()


def test_runner_rejects_a_length_limited_model_even_if_it_contains_a_valid_script(tmp_path, monkeypatch):
    model_path = tmp_path / "model.py"
    model_path.write_text(MODEL)
    changed = "def build():\n    return {}"
    reply = Reply(chunks=(f"```python\n{changed}\n```",), finish_reason="length")
    with FakeGateway(lambda message: reply) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("edit")

    assert events[-1]["t"] == "chat_error"
    assert events[-1]["code"] == "OUTPUT_LIMIT"
    assert events[-1]["outcome"]["revision"] is None
    assert model_path.read_text() == MODEL


def test_runner_http_error_becomes_a_chat_error(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    with FakeGateway(lambda message: Reply(status=500, error="boom")) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert names[0] == "chat_start"
    assert names[-1] == "chat_error"
    assert "500" in events[-1]["message"]
    assert "boom" in events[-1]["message"]
    assert (tmp_path / "model.py").read_text() == MODEL
    assert not (tmp_path / ".cad" / "backups").exists()


def test_runner_mid_stream_error_becomes_a_chat_error(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    reply = Reply(chunks=("Partial answer. ",), stream_error="gateway exploded")
    with FakeGateway(lambda message: reply) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert names[0] == "chat_start"
    assert "chat_delta" in names, "the text before the error still streams"
    assert names[-1] == "chat_error"
    assert "gateway exploded" in events[-1]["message"]
    assert "chat_done" not in names, "a failed reply does not finish cleanly"
    assert (tmp_path / "model.py").read_text() == MODEL


def test_runner_unreachable_gateway_becomes_a_chat_error(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    clear_env(monkeypatch)
    monkeypatch.setenv("CAD_CHAT_API_KEY", TEST_KEY)
    monkeypatch.setenv("CAD_CHAT_BASE_URL", f"{dead_url()}/v1")
    monkeypatch.setenv("CAD_CHAT_MODEL", TEST_MODEL)

    events: list[dict] = []
    Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert names[0] == "chat_start"
    assert names[-1] == "chat_error"
    assert "cannot reach the model gateway" in events[-1]["message"]


def test_runner_without_a_key_never_calls_the_network(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    clear_env(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("CAD_CHAT_BASE_URL", f"{dead_url()}/v1")

    events: list[dict] = []
    Runner(tmp_path, lambda kind, **payload: events.append({"t": kind, **payload})).run("hi")

    names = [e["t"] for e in events]
    assert names == ["chat_error"], "no chat_start: the request is never made"
    assert "chat.toml" in events[0]["message"]


def test_runner_stop_before_the_stream_ends(tmp_path, monkeypatch):
    (tmp_path / "model.py").write_text(MODEL)
    # The second chunk carries a code block, but it only arrives after stop().
    reply = Reply(chunks=("Working on it", "\n```python\n" + NEW_MODEL + "```\n"), delay=0.2)
    with FakeGateway(lambda message: reply) as fake:
        point_at(monkeypatch, fake)
        events: list[dict] = []
        lock = threading.Lock()

        def emit(kind: str, **payload) -> None:
            with lock:
                events.append({"t": kind, **payload})

        runner = Runner(tmp_path, emit)
        thread = threading.Thread(target=runner.run, args=("hi",), daemon=True)
        thread.start()
        try:
            wait_until(lambda: any(e["t"] == "chat_delta" for e in events), 5.0, "the first delta")
            runner.stop()
            thread.join(timeout=10)
            assert not thread.is_alive()
        finally:
            if thread.is_alive():  # never leave a stream running
                runner.stop()
                thread.join(timeout=2)

    names = [e["t"] for e in events]
    assert names[-1] == "chat_done"
    assert events[-1]["applied"] is False
    assert events[-1]["stopped"] is True
    assert "chat_applied" not in names, "a stopped reply must never write the file"
    assert (tmp_path / "model.py").read_text() == MODEL


# --------------------------------------------------------------------------- HTTP routes


def test_chat_state_and_arguments(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    with FakeGateway() as fake:
        point_at(monkeypatch, fake)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)

            code, state = call(svc, "/api/chat")
            assert code == 200
            assert state["available"] is True
            assert state["provider"] == "vercel-ai-gateway"
            assert state["url"] == fake.url
            assert state["model"] == TEST_MODEL
            assert state["models"] == [TEST_MODEL]
            assert state["config_path"].endswith("cad/chat.toml")
            assert state["reason"] is None
            assert state["running"] is False
            assert state["history"] == []

            for message in ("", "   "):
                code, err = call(svc, "/api/chat", {"message": message})
                assert 400 <= code < 500
                assert err["ok"] is False
                assert err["error"]["code"] == "INVALID_ARGUMENT"

            code, body = call(svc, "/api/chat/stop", {})
            assert code == 200
            assert body["ok"] is True

            assert fake.seen == [], "empty messages must not reach the gateway"


def test_chat_post_streams_events(tmp_path, monkeypatch):
    root = write_project(tmp_path)
    with FakeGateway() as fake:
        point_at(monkeypatch, fake)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            stream = urllib.request.urlopen(svc.url.rstrip("/") + "/api/events", timeout=10)
            try:
                code, body = call(svc, "/api/chat", {"message": "hi"})
                assert code == 200
                assert body["ok"] is True
                events = chat_events(read_until(stream, "chat_done"))
            finally:
                stream.close()

            assert events[0]["t"] == "chat_start"
            assert events[0]["model"] == TEST_MODEL
            deltas = [e["text"] for e in events if e["t"] == "chat_delta"]
            assert "".join(deltas) == "Hello from the fake model."
            assert events[-1]["t"] == "chat_done"
            assert events[-1]["applied"] is False

            state = call(svc, "/api/chat")[1]
            assert [m["role"] for m in state["history"]] == ["user", "assistant"]
            assert state["history"][0]["content"] == "hi"
            assert state["running"] is False


def test_chat_undo_route_restores_the_file(tmp_path, monkeypatch):
    root = write_project(tmp_path)

    def responder(message: str) -> Reply:
        if "hole" in message:
            return Reply(chunks=("Adding a hole.\n", "\n```python\n" + NEW_MODEL + "```\n"))
        return Reply()

    with FakeGateway(responder) as fake:
        point_at(monkeypatch, fake)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            stream = urllib.request.urlopen(svc.url.rstrip("/") + "/api/events", timeout=10)
            try:
                code, body = call(svc, "/api/chat", {"message": "add a hole"})
                assert code == 200 and body["ok"] is True
                events = chat_events(read_until(stream, "chat_done"))
            finally:
                stream.close()

            assert events[-1]["applied"] is True
            seq = [e["t"] for e in events if e["t"] != "chat_metrics"]
            assert seq[-2:] == ["chat_applied", "chat_done"]
            assert "hole" in (root / "model.py").read_text()
            assert (root / "model.py").read_text() == NEW_MODEL.strip() + "\n"

            code, body = call(svc, "/api/chat/undo", {})
            assert code == 200
            assert body["ok"] is True
            assert body["restored"] == "model.py"

            assert (root / "model.py").read_text() == MODEL
            assert list((root / ".cad" / "backups").glob("model-*.py")) == []
