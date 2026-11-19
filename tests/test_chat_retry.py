"""A retry must never erase later design instructions or silently retarget."""

from copy import deepcopy

import pytest

from cad.chat_retry import merge_turn, plan_turn, request_history
from cad.errors import CadError


def pair(turn, *, failed=False, selection=None):
    user = {"id": turn, "role": "user", "content": f"change {turn}"}
    if selection is not None:
        user["selection"] = selection
    return [user, {"id": f"reply-{turn}", "role": "assistant", "content": "done",
                   "failed": failed, "outcome": {"applied": not failed}}]


@pytest.mark.parametrize("failed", [False, True])
def test_latest_retry_replaces_only_on_terminal_commit(failed):
    messages = pair("first") + pair("last", failed=failed)
    original = deepcopy(messages)
    plan = plan_turn(messages, "", new_turn="unused", retry_turn="last")
    assert plan.turn == plan.replace_turn == "last"
    assert plan.message == "change last"
    assert request_history(messages, plan.replace_turn) == pair("first")
    assert messages == original  # failed/stopped generation cannot erase its receipt
    revised = pair("last")
    revised[1]["content"] = "repaired"
    committed = merge_turn(messages, *revised, plan.replace_turn)
    assert committed == pair("first") + revised
    assert messages == original


@pytest.mark.parametrize("failed", [False, True])
def test_old_retry_appends_and_preserves_every_later_turn(failed):
    messages = pair("first", failed=failed) + pair("later")
    plan = plan_turn(messages, None, new_turn="new", retry_turn="first")
    assert plan.turn == "new"
    assert plan.replace_turn is None
    assert plan.retry_of == "first"
    assert request_history(messages, plan.replace_turn) == messages
    assert merge_turn(messages, *pair(plan.turn), plan.replace_turn) == messages + pair("new")


def test_edited_old_prompt_is_a_new_request_with_original_target():
    target = {"revision": 4, "part": "clamp", "features": ["jaw"]}
    messages = pair("first", selection=target) + pair("later")
    plan = plan_turn(messages, "  make this 3 mm thicker  ", new_turn="new",
                     retry_turn="first", selection={"revision": 5, "part": "lid"})
    assert plan.message == "make this 3 mm thicker"
    assert plan.selection == target
    plan.selection["features"].append("other")
    assert messages[0]["selection"] == target


def test_retry_without_original_selection_does_not_use_current_viewport():
    plan = plan_turn(pair("last"), "", new_turn="unused", retry_turn="last",
                     selection={"part": "unrelated"})
    assert plan.selection is None


def test_new_request_carries_its_current_selection():
    target = {"revision": 7, "part": "lid"}
    plan = plan_turn([], "  thicken this  ", new_turn="new", selection=target)
    assert plan.message == "thicken this"
    assert plan.selection == target
    assert plan.selection is not target
    assert plan.retry_of is plan.replace_turn is None


@pytest.mark.parametrize("retry_turn", ["missing", "reply-last"])
def test_invalid_retry_preserves_history(retry_turn):
    messages = pair("last")
    original = deepcopy(messages)
    with pytest.raises(CadError):
        plan_turn(messages, "edit", new_turn="new", retry_turn=retry_turn)
    assert messages == original


@pytest.mark.parametrize("message", [None, "", " \n ", 42, {"text": "hello"}])
def test_invalid_new_message_is_rejected(message):
    with pytest.raises(CadError):
        plan_turn([], message, new_turn="new")


def test_history_advanced_during_retry_cannot_be_truncated():
    messages = pair("original")
    plan = plan_turn(messages, "retry", new_turn="unused", retry_turn="original")
    messages += pair("arrived-later")
    original = deepcopy(messages)
    with pytest.raises(CadError, match="no longer the latest"):
        merge_turn(messages, *pair("original"), plan.replace_turn)
    assert messages == original


def test_incomplete_or_nonstandard_tail_is_never_deleted():
    messages = pair("first") + [{"id": "incomplete", "role": "user", "content": "pending"}]
    plan = plan_turn(messages, "edit", new_turn="new", retry_turn="incomplete")
    assert plan.replace_turn is None
    assert request_history(messages) == messages


def test_append_cannot_accidentally_reuse_existing_turn_id():
    messages = pair("first") + pair("second")
    with pytest.raises(CadError, match="unique"):
        plan_turn(messages, "retry", new_turn="second", retry_turn="first")
    with pytest.raises(CadError, match="already exists"):
        merge_turn(messages, *pair("first"))


def test_replacement_cannot_write_a_different_turn_id():
    with pytest.raises(CadError, match="does not match"):
        merge_turn(pair("last"), *pair("other"), replace_turn="last")


@pytest.mark.parametrize("edit", [False, True])
def test_http_old_retry_preserves_later_turn_and_current_context(tmp_path, monkeypatch, edit):
    from test_chat_turns import FakeGateway, Reply, call, point_at, run_turn, wait_idle, write_project
    from cad.serve import Service

    root = write_project(tmp_path)
    with FakeGateway(lambda message: Reply(chunks=("No model changes needed.",))) as fake:
        point_at(monkeypatch, fake, tmp_path)
        with Service(root, port=0, poll=60) as svc:
            wait_idle(svc)
            original, _ = run_turn(svc, "describe the plate")
            run_turn(svc, "keep its mounting hole exactly where it is")
            before = call(svc, "/api/chat")[1]["history"]
            revised_prompt = "describe only the top face" if edit else ""
            retried, _ = run_turn(svc, revised_prompt, retry_turn=original["turn"])
            after = call(svc, "/api/chat")[1]["history"]

            assert retried["turn"] != original["turn"]
            assert retried["replace_turn"] is None
            assert retried["retry_of"] == original["turn"]
            assert after[:len(before)] == before
            assert len(after) == len(before) + 2
            assert after[-2]["content"] == (revised_prompt or "describe the plate")
            sent = fake.seen[-1]["messages"]
            assert any(m["content"] == "keep its mounting hole exactly where it is" for m in sent)
