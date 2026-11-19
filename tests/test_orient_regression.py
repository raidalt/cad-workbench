"""Orientation edits must switch cleanly, and only a repeat of the effective one is a no-op."""

import json
import time
import urllib.request

from cad.chat import orient_source

SCRIPT = '''from cad import Part


def build():
    base = Part("plate")
    base.box(60, 40, 4, name="base")
    base.box(60, 4, 30, at=(0, 20, 4), align=("center", "max", "min"), name="wall")
    return base
'''


def test_switching_back_to_a_previous_face_is_a_real_change():
    """The reported regression: bottom -> top -> bottom left the top active."""
    once, changed1 = orient_source(SCRIPT, "plate", "base.bottom")
    twice, changed2 = orient_source(once, "plate", "base.top")
    thrice, changed3 = orient_source(twice, "plate", "base.bottom")

    assert (changed1, changed2, changed3) == (True, True, True)
    assert once.count("print_on") == 1
    assert twice.count("print_on") == 1, "the old orientation must be replaced, not appended"
    assert thrice.count("print_on") == 1
    assert 'base.faces("base.bottom")' in thrice
    assert 'base.faces("base.top")' not in thrice, "the previous orientation must be gone"


def test_repeating_the_effective_orientation_is_a_no_op():
    once, _ = orient_source(SCRIPT, "plate", "base.bottom")
    again, changed = orient_source(once, "plate", "base.bottom")
    assert changed is False
    assert again == once


def test_a_shadowed_earlier_call_does_not_count_as_effective():
    """A hand-written file with two calls: the last one wins, so switching back is not a no-op."""
    doubled = SCRIPT.replace(
        "    return base",
        '    base.print_on(base.faces("base.top"))\n'
        '    base.print_on(base.faces("base.bottom"))\n'
        "    return base")
    out, changed = orient_source(doubled, "plate", "base.top")
    assert changed is True
    assert out.count("print_on") == 1
    assert 'base.faces("base.top")' in out


def test_unknown_part_variable_is_rejected():
    import pytest

    from cad.errors import CadError

    with pytest.raises(CadError) as e:
        orient_source(SCRIPT, "lid", "lid.top")
    assert e.value.code == "INVALID_ARGUMENT"


def test_multiline_print_on_is_rejected_not_mangled():
    import pytest

    from cad.errors import CadError

    weird = SCRIPT.replace(
        "    return base",
        "    base.print_on(\n        base.faces(\"base.top\"),\n    )\n    return base")
    with pytest.raises(CadError) as e:
        orient_source(weird, "plate", "base.bottom")
    assert e.value.code == "INVALID_ARGUMENT"


# --------------------------------------------------------------------------- through the API

def write_project(tmp_path):
    (tmp_path / "cad.toml").write_text('[project]\nname = "orient"\nentry = "model.py"\n')
    (tmp_path / "model.py").write_text(SCRIPT)
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


def test_api_orientation_round_trip_keeps_the_last_one_effective(tmp_path):
    from cad.serve import Service

    with Service(write_project(tmp_path), port=0, poll=60) as svc:
        wait_idle(svc)
        seen = []
        for ref in ("base.bottom", "base.top", "base.bottom"):
            code, body = call(svc, "/api/orient", {"part": "plate", "ref": ref})
            assert code == 200 and body["ok"] is True, body
            assert body["changed"] is True, ref
            state = wait_idle(svc)
            seen.append(state["parts"]["plate"]["print_down"])
            # exactly one orientation line, and it names the ref just applied
            source = (tmp_path / "model.py").read_text()
            assert source.count("print_on") == 1
            assert f'base.faces("{ref}")' in source

        assert seen[0] == [0.0, 0.0, -1.0], "the bottom face points down"
        assert seen[1] == [0.0, 0.0, 1.0], "the top face points up in the design pose"
        assert seen[2] == seen[0], "switching back must really switch back"

        # a repeat of what is effective now changes nothing
        code, body = call(svc, "/api/orient", {"part": "plate", "ref": "base.bottom"})
        assert code == 200 and body.get("changed") is False
