"""History browsing is read-only: `?revision=N` renders a snapshot without moving HEAD,
and mutations are refused while a past revision is on screen."""

import json
import time
import urllib.error
import urllib.request

SCRIPT = '''from cad import Part


def build():
    p = Part("plate")
    p.box({width}, 30, 4, name="base")
    return p
'''


def write_project(tmp_path):
    (tmp_path / "cad.toml").write_text('[project]\nname = "history"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text(SCRIPT.format(width=40))
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


def two_revisions(tmp_path):
    """r1 is a 40 mm plate, r2 is a 50 mm plate; the script on disk is the 50 mm one."""
    from cad.serve import Service

    root = write_project(tmp_path)
    svc = Service(root, port=0, poll=60).start()
    wait_idle(svc)
    (root / "model.py").write_text(SCRIPT.format(width=50))
    svc.builder._build(None, True)
    return svc, root


def test_state_revision_shows_old_geometry_while_head_and_source_move_on(tmp_path):
    svc, root = two_revisions(tmp_path)
    try:
        code, old = call(svc, "/api/state?revision=1")
        assert code == 200
        assert old["revision"] == 1
        assert old["head"] == 2, "browsing does not move HEAD"
        assert old["parts"]["plate"]["summary"]["bbox"]["size"][0] == 40

        code, head = call(svc, "/api/state")
        assert head["revision"] == 2 and head["head"] == 2
        assert head["parts"]["plate"]["summary"]["bbox"]["size"][0] == 50

        # the source on disk belongs to r2, not to the snapshot being viewed
        assert "50" in (root / "model.py").read_text()
    finally:
        svc.stop()


def test_orient_is_refused_while_viewing_history(tmp_path):
    svc, root = two_revisions(tmp_path)
    try:
        before = (root / "model.py").read_text()
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.top", "viewing": 1})
        assert code == 409, body
        assert body["ok"] is False and body["error"]["code"] == "STALE_VIEW"
        assert "return to the current revision" in body["error"]["hint"].lower()
        assert (root / "model.py").read_text() == before, "a refused edit must not touch the script"
    finally:
        svc.stop()


def test_chat_is_refused_while_viewing_history(tmp_path):
    svc, root = two_revisions(tmp_path)
    try:
        code, body = call(svc, "/api/chat", {"message": "make it wider", "viewing": 1})
        assert code == 409, body
        assert body["error"]["code"] == "STALE_VIEW"
        hist = call(svc, "/api/chat")[1]["history"]
        assert hist == [], "a refused turn must not be recorded"
    finally:
        svc.stop()


def test_returning_to_current_allows_editing(tmp_path):
    svc, root = two_revisions(tmp_path)
    try:
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.top", "viewing": 2})
        assert code == 200 and body["ok"] is True, body
        # and with no `viewing` at all (the return-to-current path) it also works
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.bottom"})
        assert code == 200 and body["ok"] is True and body.get("changed") is True
        assert 'p.faces("base.bottom")' in (root / "model.py").read_text()
    finally:
        svc.stop()


def test_post_revision_still_sets_head_for_other_consumers(tmp_path):
    svc, root = two_revisions(tmp_path)
    try:
        code, body = call(svc, "/api/revision", {"revision": 1})
        assert code == 200 and body["ok"] is True
        state = wait_idle(svc)
        assert state["head"] == 1, "the existing checkout API keeps its meaning"
    finally:
        svc.stop()
