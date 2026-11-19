"""Real HTTP/CAD journeys; only the streaming language model is substituted.

These intentionally cross component boundaries: a visible selection must reach
the model, rejected candidates must never become project history, and reloading
the app must preserve what the user actually built. No slicer or external API is
used. The SSE gateway and HTTP setup are shared with the chat receipt tests.
"""

import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import urllib.request

import pytest

from cad.serve import Service
from cad.chat import NEW_MODEL_TEMPLATE
from test_chat_turns import FakeGateway, Reply, call, point_at, wait_idle, write_project


EMPTY = "def build():\n    return {}\n"


def assembly(clamp_height=8, pad_height=3):
    return f'''from cad import Part

def build():
    clamp = Part("clamp")
    clamp.box(40, 30, {clamp_height}, name="clamp_body")
    pad = Part("pad")
    pad.box(20, 16, {pad_height}, name="pad_body")
    return {{"clamp": clamp, "pad": pad}}
'''


def parametric(clearance):
    return f'''from cad import Part, Parameter

a = Parameter("a", 10, min=5, max=15)
b = Parameter("b", 10, min=5, max=15)

def build():
    p = Part("bracket")
    p.box(30, {clearance}, 3, name="base")
    return p
'''


def scripted_responses(*sources):
    remaining = iter(sources)

    def respond(_message):
        source = next(remaining, None)
        # An unexpected extra model call returns prose and fails assertions
        # below, rather than hanging the HTTP client inside a failed handler.
        text = "No further response was planned." if source is None else (
            "Here is the updated design.\n```python\n" + source + "```\n"
        )
        halfway = len(text) // 2
        return Reply(chunks=(text[:halfway], text[halfway:]))

    return respond


def chat_turn(svc, message, **context):
    """Observe the public SSE lifecycle, including either terminal outcome."""
    with urllib.request.urlopen(svc.url + "/api/events", timeout=45) as stream:
        code, body = call(svc, "/api/chat", {"message": message, **context})
        assert code == 200, body
        events = []
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            line = stream.readline().decode().strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if not event.get("t", "").startswith("chat_"):
                continue
            events.append(event)
            if event["t"] in {"chat_done", "chat_error"}:
                break
        else:
            pytest.fail(f"chat did not terminate: {events}")
    # The final event is emitted just before the worker returns. Wait for that
    # boundary so the next real-user action cannot race a still-running turn.
    deadline = time.monotonic() + 5
    while call(svc, "/api/chat")[1]["running"]:
        assert time.monotonic() < deadline, "terminal event left the chat running"
        time.sleep(0.01)
    return body, events


def mesh(svc, part, revision):
    with urllib.request.urlopen(
        f"{svc.url}/api/mesh?part={part}&rev={revision}", timeout=20
    ) as response:
        return response.read()


def successful_revision(events):
    assert events[-1]["t"] == "chat_done", events[-1]
    applied = [event for event in events if event["t"] == "chat_applied"]
    assert len(applied) == 1
    assert applied[0]["verified"] is True
    return applied[0]["revision"]


def test_first_chat_edit_survives_rebuilding_the_same_blank_project(tmp_path, monkeypatch):
    prompt_seen = threading.Event()
    release_reply = threading.Event()
    respond = scripted_responses(assembly())

    def gated_reply(message):
        prompt_seen.set()
        assert release_reply.wait(15)
        return respond(message)

    root = write_project(tmp_path / "project", NEW_MODEL_TEMPLATE)
    with FakeGateway(gated_reply) as fake, ThreadPoolExecutor() as pool:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            initial = wait_idle(svc)
            edit = pool.submit(chat_turn, svc, "Build a clamp and a separate pad.",
                               viewing=initial["revision"])
            try:
                assert prompt_seen.wait(10)
                for _ in range(2):
                    status, reset = call(svc, "/api/new-model", {})
                    assert status == 200 and reset["revision"] == initial["revision"]
                    assert reset["backup"] is None
                assert call(svc, "/api/build", {"force": True})[0] == 200
                rebuilt = wait_idle(svc)
                assert rebuilt["empty"] and rebuilt["revision"] != initial["revision"]
            finally:
                release_reply.set()

            _, events = edit.result(timeout=40)
            built = successful_revision(events)
            state = call(svc, "/api/state")[1]
            assert state["revision"] == built and set(state["parts"]) == {"clamp", "pad"}
            assert call(svc, f"/api/state?rev={initial['revision']}")[1]["empty"]


def test_initial_blank_revision_can_finish_after_chat_captures_its_source(tmp_path):
    root = write_project(tmp_path / "project", NEW_MODEL_TEMPLATE)
    svc = Service(root, port=0)
    _, commit = svc.builder.prepare_chat(None, None)

    initial = svc.session.build()
    edit = commit(assembly())

    assert edit["ok"] and edit["revision"] == initial["revision"] + 1
    assert set(svc.builder.state()["parts"]) == {"clamp", "pad"}


def test_empty_board_selected_edits_history_and_reload(tmp_path, monkeypatch):
    """Build, click a face, edit it, click a feature, and browse history."""
    root = write_project(tmp_path / "project", EMPTY)
    with FakeGateway(scripted_responses(assembly(), assembly(12), assembly(12, 5))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            initial = wait_idle(svc)
            assert initial["empty"] is True
            assert initial["parts"] == {}

            _, events = chat_turn(svc, "Build a clamp body and a separate replaceable pad.")
            first_revision = successful_revision(events)
            first = call(svc, "/api/state")[1]
            assert set(first["parts"]) == {"clamp", "pad"}
            original_pad = mesh(svc, "pad", first_revision)

            faces = call(svc, f"/api/faces?part=clamp&rev={first_revision}")[1]["faces"]
            selection = {"revision": first_revision, "part": "clamp", "kind": "face", "face": faces[0]["id"]}
            _, events = chat_turn(
                svc, "Make this body 12 mm tall; keep the pad unchanged.",
                viewing=first_revision, selection=selection,
            )
            second_revision = successful_revision(events)
            second = call(svc, "/api/state")[1]
            assert second["parts"]["clamp"]["summary"]["bbox"]["size"][2] == pytest.approx(12)
            assert mesh(svc, "pad", second_revision) == original_pad
            prompt = "\n".join(message["content"] for message in fake.seen[1]["messages"])
            assert "CURRENT SELECTION" in prompt
            target = json.loads(prompt.split("CURRENT SELECTION", 1)[1].split("\n", 1)[1].split("\n", 1)[0])
            assert target["part"] == "clamp" and target["kind"] == "face"
            assert target["face"] == faces[0]["id"]
            assert target["area"] == faces[0]["area"]

            before_rejection = call(svc, "/api/chat")[1]["history"]
            code, rejected = call(svc, "/api/chat", {
                "message": "Now make this taller again", "selection": selection,
                "viewing": second_revision,
            })
            assert code == 409, rejected
            assert rejected["error"]["code"] in {"STALE_SELECTION", "STALE_VIEW"}
            assert len(fake.seen) == 2
            assert call(svc, "/api/chat")[1]["history"] == before_rejection

            _, events = chat_turn(svc, "Make this pad feature 5 mm thick.", viewing=second_revision, selection={
                "revision": second_revision, "part": "pad", "kind": "feature", "feature": "pad_body",
            })
            final_revision = successful_revision(events)
            final = call(svc, "/api/state")[1]
            assert final["parts"]["pad"]["summary"]["bbox"]["size"][2] == pytest.approx(5)
            assert final["parts"]["clamp"]["summary"] == second["parts"]["clamp"]["summary"]

            old = call(svc, f"/api/state?revision={first_revision}")[1]
            assert old["revision"] == first_revision
            assert old["head"] == final_revision
            assert call(svc, "/api/state")[1]["head"] == final_revision
            history = call(svc, "/api/chat")[1]["history"]
            assert len(history) == 6
            assert len({message["id"] for message in history}) == 6
            final_mesh = mesh(svc, "clamp", final_revision)

        with Service(root, port=0, poll=60) as reloaded:
            state = wait_idle(reloaded)
            assert state["head"] == final_revision
            assert len(state["revisions"]) == len(final["revisions"])
            assert mesh(reloaded, "clamp", final_revision) == final_mesh
            assert call(reloaded, "/api/chat")[1]["history"] == history


def test_crossed_parameter_failure_repairs_before_publication_and_failure_preserves_project(tmp_path, monkeypatch):
    """Mixed extrema fail even when default/all-low/all-high succeed."""
    root = write_project(tmp_path / "project", assembly())
    risky = parametric("a - b + 5")
    repaired = parametric("a + b + 5")
    broken = "from cad import Part\ndef build():\n    return undefined_body\n"
    with FakeGateway(scripted_responses(risky, repaired, broken, broken)) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            initial = wait_idle(svc)
            _, events = chat_turn(svc, "Build a parameterized bracket, valid throughout both advertised parameter ranges.")
            assert len(fake.seen) == 2, "repair must not issue a duplicate gateway request"
            revision = successful_revision(events)
            phases = [event["phase"] for event in events if event["t"] == "chat_validation"]
            assert "repairing" in phases and "passed" in phases
            assert len(fake.seen) == 2
            feedback = "\n".join(message["content"] for message in fake.seen[1]["messages"])
            assert "parameter_corner" in feedback
            assert '"a"' in feedback and '"b"' in feedback
            state = call(svc, "/api/state")[1]
            assert len(state["revisions"]) == len(initial["revisions"]) + 1
            assert (root / "model.py").read_text() == repaired
            assert set(state["parts"]) == {"bracket"}

            # Use the actual extreme combination through the same parameter UI
            # route a person uses, proving the repaired design remains editable.
            code, response = call(svc, "/api/param", {"values": {"a": 5, "b": 15}})
            assert code == 200, response
            deadline = time.monotonic() + 10
            while True:
                state = call(svc, "/api/state")[1]
                if not state["building"] and state["head"] != revision:
                    break
                assert time.monotonic() < deadline, state
                time.sleep(0.01)
            assert not state["error"]
            assert state["parts"]["bracket"]["summary"]["bbox"]["size"][1] == pytest.approx(25)
            stable_revision = state["head"]
            stable_revisions = state["revisions"]
            stable_mesh = mesh(svc, "bracket", stable_revision)

            _, failed = chat_turn(svc, "Add an adjustable mounting foot to the bracket.")
            assert failed[-1]["t"] == "chat_error"
            assert not any(event["t"] == "chat_applied" for event in failed)
            assert len(fake.seen) == 4, "a bad candidate gets one bounded repair"
            after = call(svc, "/api/state")[1]
            assert after["head"] == stable_revision
            assert after["revisions"] == stable_revisions
            assert after["overrides"] == state["overrides"]
            assert (root / "model.py").read_text() == repaired
            assert mesh(svc, "bracket", stable_revision) == stable_mesh
            history = call(svc, "/api/chat")[1]["history"]
            assert len(history) == 4
            assert history[-1]["failed"] is True
            assert history[1]["outcome"]["revision"] == revision


def test_new_model_during_generation_cannot_be_overwritten_by_old_reply(tmp_path, monkeypatch):
    """Starting over in another UI action wins over an in-flight edit."""
    root = write_project(tmp_path / "project", assembly())
    generation_started = threading.Event()
    release_reply = threading.Event()
    response = scripted_responses(assembly(18))

    def delayed_reply(message):
        generation_started.set()
        release_reply.wait(15)
        return response(message)

    with FakeGateway(delayed_reply) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc, ThreadPoolExecutor(max_workers=1) as executor:
            initial = wait_idle(svc)
            pending = executor.submit(chat_turn, svc, "Make the clamp 18 mm tall.", viewing=initial["head"])
            try:
                assert generation_started.wait(10), "chat did not reach the model"
                code, cleared = call(svc, "/api/new-model", {})
                assert code == 200, cleared
                blank_source = (root / "model.py").read_text()
                blank = call(svc, "/api/state")[1]
                assert blank["empty"] is True
            finally:
                release_reply.set()
            _, events = pending.result(timeout=45)
            assert events[-1]["t"] == "chat_error"
            assert events[-1]["code"] == "STALE_VIEW"
            assert not any(event["t"] == "chat_applied" for event in events)
            state = call(svc, "/api/state")[1]
            assert state["head"] == cleared["revision"]
            assert state["revisions"] == blank["revisions"]
            assert state["parts"] == {}
            assert (root / "model.py").read_text() == blank_source
