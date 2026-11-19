"""Plan chat retries without deleting conversation or changing CAD state.

Only the final complete turn can be replaced. Replaying an older turn creates
a new request against the current model, retaining the original selection for
the server to validate. History changes only when a terminal receipt is saved.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .errors import CadError


@dataclass(frozen=True)
class TurnPlan:
    message: str
    turn: str
    selection: dict | None
    replace_turn: str | None = None
    retry_of: str | None = None


def _index(messages: list[dict], turn: str) -> int | None:
    return next((i for i, m in enumerate(messages)
                 if isinstance(m, dict) and m.get("id") == turn), None)


def _replaceable(messages: list[dict], index: int | None) -> bool:
    return (index is not None and index == len(messages) - 2
            and isinstance(messages[index], dict)
            and messages[index].get("role") == "user"
            and isinstance(messages[index + 1], dict)
            and messages[index + 1].get("role") == "assistant")


def plan_turn(messages: list[dict], message: str | None, *, new_turn: str,
              retry_turn: str | None = None, selection: dict | None = None) -> TurnPlan:
    """Resolve request identity and targeting without mutating persisted history.

    The caller must validate ``plan.selection`` against the current geometry
    before starting the runner. A retry never substitutes today's viewport
    selection for the original turn's target, even when its text is edited.
    """
    if message is not None and not isinstance(message, str):
        raise CadError("INVALID_ARGUMENT", "the message must be text")
    replace_turn = None
    if retry_turn is not None:
        index = _index(messages, retry_turn)
        if index is None or messages[index].get("role") != "user":
            raise CadError("INVALID_ARGUMENT", f"no user turn {retry_turn!r} in the chat history",
                           hint="reload the chat and retry a turn that is listed")
        original = messages[index]
        message = message or original.get("content") or ""
        selection = original.get("selection")
        if _replaceable(messages, index):
            replace_turn = retry_turn
    if not isinstance(message, str) or not message.strip():
        raise CadError("INVALID_ARGUMENT", "the message is empty")
    turn = replace_turn or new_turn
    if not turn or (replace_turn is None and _index(messages, turn) is not None):
        raise CadError("INVALID_ARGUMENT", "the new turn id must be unique")
    return TurnPlan(message.strip(), turn, deepcopy(selection), replace_turn, retry_turn)


def request_history(messages: list[dict], replace_turn: str | None = None) -> list[dict]:
    """Get model context while leaving the prior receipt available on disk."""
    if replace_turn is None:
        return deepcopy(messages)
    index = _index(messages, replace_turn)
    if not _replaceable(messages, index):
        raise CadError("INVALID_ARGUMENT", "the retried turn is no longer the latest complete turn",
                       hint="reload the chat and retry again; no history has been removed")
    return deepcopy(messages[:index])


def merge_turn(messages: list[dict], user: dict, assistant: dict,
               replace_turn: str | None = None) -> list[dict]:
    """Commit a completed pair; never truncate turns arriving after a retry.

    The caller serializes writes. If the history changed during generation,
    refusing the replacement preserves the intervening conversation.
    """
    if (user.get("role") != "user" or assistant.get("role") != "assistant"
            or not user.get("id")):
        raise CadError("INVALID_ARGUMENT", "a completed turn needs a user and assistant pair")
    if replace_turn is not None and user["id"] != replace_turn:
        raise CadError("INVALID_ARGUMENT", "the replacement turn id does not match")
    prefix = request_history(messages, replace_turn)
    if _index(prefix, user["id"]) is not None:
        raise CadError("INVALID_ARGUMENT", "the turn id already exists in chat history")
    return prefix + deepcopy([user, assistant])
