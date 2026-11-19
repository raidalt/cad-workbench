"""Resolve browser picks against a specific, current CAD revision.

The browser supplies identifiers, never geometry facts.  Face indices are only
meaningful within their revision; named references are checked against the
actual face rather than being accepted as an alternative identity.
"""

from copy import deepcopy

from .errors import CadError


def _integer(value, field: str, *, minimum: int = 0) -> int:
    # bool is an int in Python, and int(float) silently truncates face indices.
    if type(value) is not int or value < minimum:
        raise CadError("INVALID_ARGUMENT", f"selection {field} must be an integer >= {minimum}")
    return value


def _name(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise CadError("INVALID_ARGUMENT", f"selection {field} must be a nonempty name")
    return value


def _stale(revision, head) -> None:
    raise CadError(
        "STALE_VIEW", f"selected geometry belongs to r{revision}, but the current revision is r{head}",
        hint="return to the current revision and select the geometry again",
        details={"selection_revision": revision, "active_revision": head},
    )


def resolve_selection(builder, selection, viewing=None) -> dict | None:
    """Return trusted model context, or ``None`` when there is no selection.

    Invalid identities raise ``INVALID_ARGUMENT``/``NOT_FOUND``; outdated picks
    raise ``STALE_VIEW``. Callers must retain ``revision`` and recheck it before
    applying edits: this snapshot cannot lock geometry for an entire chat turn.
    Unknown browser fields (including claimed area, bounds and instructions)
    are discarded. This function never silently retargets a stale selection.
    """
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise CadError("INVALID_ARGUMENT", "selection must be an object")
    revision = _integer(selection.get("revision"), "revision", minimum=1)
    name = _name(selection.get("part"), "part")
    kind = selection.get("kind")
    if kind not in ("part", "face", "feature"):
        raise CadError("INVALID_ARGUMENT", "selection kind must be part, face or feature")
    face_id = selection.get("face")
    ref = selection.get("ref")
    feature_id = selection.get("feature")
    if face_id is not None:
        face_id = _integer(face_id, "face")
    if ref is not None:
        ref = _name(ref, "ref")
    if feature_id is not None:
        feature_id = _name(feature_id, "feature")
    if kind == "part" and any(x is not None for x in (face_id, ref, feature_id)):
        raise CadError("INVALID_ARGUMENT", "part selection cannot include a face, reference or feature")
    if kind == "face" and face_id is None and ref is None:
        raise CadError("INVALID_ARGUMENT", "face selection requires a face index or named reference")
    if kind == "feature" and (feature_id is None or face_id is not None or ref is not None):
        raise CadError("INVALID_ARGUMENT", "feature selection requires a feature id without a face or reference")
    if viewing is not None:
        viewing = _integer(viewing, "viewing", minimum=1)

    with builder.model_lock:
        head = builder.session.store.head()
        if revision != head:
            _stale(revision, head)
        if viewing is not None and viewing != revision:
            _stale(viewing, head)
        resolved_revision, parts = builder.session.parts(revision)
        if resolved_revision != revision:
            _stale(revision, resolved_revision)
        if name not in parts:
            raise CadError("NOT_FOUND", f"no part named {name!r} in revision {revision}")
        part = parts[name]
        manifest = builder.session.store.manifest(revision)
        result = {"revision": revision, "part": name, "kind": kind,
                  "summary": deepcopy(manifest["parts"][name]["summary"])}
        if kind != "part":
            references = part.references()
            features = {f["id"]: f for f in builder._features(revision, name)}
            if kind == "feature":
                if feature_id not in features:
                    raise CadError("NOT_FOUND", f"no feature named {feature_id!r} in part {name!r}")
                result.update(feature=feature_id, feature_metadata=deepcopy(features[feature_id]),
                              refs=sorted(n for n, info in references.items()
                                          if info.get("created_by") == feature_id and info.get("status") == "ok"))
            else:
                faces = builder.faces(name, revision)
                matches = [f for f in faces if (face_id is None or f["id"] == face_id)
                           and (ref is None or ref in f["refs"])]
                if not matches:
                    raise CadError("NOT_FOUND", "selected face and reference do not identify a current face",
                                   hint="select the face again in the viewport")
                if len(matches) != 1:
                    raise CadError("INVALID_ARGUMENT", "named reference identifies multiple faces; include the picked face index")
                face = matches[0]
                refs = list(face["refs"])
                origins = sorted({references[n].get("created_by") for n in refs
                                  if references.get(n, {}).get("created_by") in features})
                if feature_id is not None and feature_id not in origins:
                    raise CadError("INVALID_ARGUMENT", "selected feature does not own the selected face reference")
                result.update(face=face["id"], refs=refs, area=face["area"],
                              center=list(face["center"]), feature_ids=origins)
                if ref is not None or refs:
                    result["ref"] = ref if ref is not None else refs[0]
                if feature_id is not None or len(origins) == 1:
                    result["feature"] = feature_id if feature_id is not None else origins[0]
                    result["feature_metadata"] = deepcopy(features[result["feature"]])
        # The lock serializes this process; an external CLI build may still move
        # HEAD while metadata is loading, so do not return that mixed snapshot.
        current = builder.session.store.head()
        if current != revision:
            _stale(revision, current)
        return result
