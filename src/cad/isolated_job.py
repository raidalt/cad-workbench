"""One native CAD job inside a disposable VM. Only the controller may publish a result."""
from __future__ import annotations

import base64
import json
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlencode

from .edits import apply_script, undo
from .design_validation import validate_candidate
from .engine import ProjectSession
from .errors import CadError
from .project import ProjectConfig, init_project, read_overlay
from .serve import ProjectAPI, Service


def execute(work: Path, request: dict):
    project = work / "project"
    project.mkdir()
    archive = work / "project.tar.gz"
    if archive.is_file():
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(project, filter="data")
    else:
        init_project(project, "workspace")
    session = ProjectSession(project)
    action = request["action"]
    args = request.get("args") or {}
    entry = ProjectConfig.load(project).entry
    if action in {"build", "validate"}:
        for filename, encoded in args.get("files", {}).items():
            rel = Path(filename)
            dest = project / rel
            if (rel.is_absolute() or ".." in rel.parts or not rel.parts or ".cad" in rel.parts
                    or not dest.resolve().is_relative_to(project.resolve())):
                raise ValueError("Invalid input path")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(encoded, validate=True))
        entry = ProjectConfig.load(project).entry
        if Path(entry).is_absolute() or ".." in Path(entry).parts:
            raise ValueError("Invalid project entry")
        source = args.get("source", (project / entry).read_text() if (project / entry).is_file() else "")
        if "params" in args:
            from .project import write_overlay
            write_overlay(project, args["params"])
        validation = validate_candidate(project, source, message=args.get("message", ""))
        if action == "validate" or not validation["ok"]:
            return (200 if validation["ok"] else 422), "application/json", json.dumps(validation).encode()
        if not (project / entry).exists() or (project / entry).read_text() != source:
            apply_script(project, source, name=entry)
        result = session.build()
    elif action == "undo":
        undo(project)
        result = session.build()
    elif action == "clearance":
        result = session.clearance(rev=args.get("rev"))
        return 200, "application/json", json.dumps(result).encode()
    else:
        if action not in {"state", "mesh", "faces", "print", "fea", "export", "preview"}:
            raise ValueError("Unknown native operation")
        # Any implicit build requested by a reader also runs inside this VM.
        if session.store.head() is None:
            session.build(read_overlay(project))
        api = ProjectAPI(Service(project))
        status, body, mime, _ = api.request("GET", "/api/" + action, urlencode(args))
        return status, mime, body
    # A proposed checkpoint is opaque to the host. Only explicit writes can advance HEAD.
    with tarfile.open(work / "candidate.tar.gz", "w:gz", dereference=True) as output:
        for path in sorted(project.iterdir()):
            if path.name not in {"out", "renders", "__pycache__"}:
                output.add(path, arcname=path.name)
    return 200, "application/json", json.dumps(result).encode()


def main():
    work = Path(sys.argv[1]).resolve()
    try:
        code, mime, data = execute(work, json.loads((work / "request.json").read_bytes()))
    except (CadError, ValueError, OSError) as error:
        info = error.to_dict() if isinstance(error, CadError) else {"code": "JOB_FAILED", "message": str(error)}
        code, mime, data = 422, "application/json", json.dumps({"error": info}).encode()
    (work / "result.bin").write_bytes(data)
    (work / "result.json").write_text(json.dumps({"status": code, "type": mime}))


if __name__ == "__main__":
    main()
