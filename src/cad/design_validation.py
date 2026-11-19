"""Validate proposed source in an expendable project, before publishing it.

This is process/state isolation for trusted CAD Python, not a security sandbox:
absolute paths and network access in a script retain the caller's permissions.
Parameter endpoints and sampled print diagnostics are evidence, not a proof of
mechanical fit, strength, motion, or printability over a continuous range.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable

from .errors import CadError
from .project import IGNORED_DIRS, ProjectConfig, read_overlay

VALIDATION_TIMEOUT = 90
MAX_CORNERS = 12
MAX_PARAMETERS = 8
MAX_RESULT_BYTES = 2 * 1024 * 1024
_DESIGN_INTENT = re.compile(
    r"\b(build|create|design|make|parameter\w*|dimension\w*|range|size|diameter|"
    r"radius|thick\w*|width|height|depth|length|clearance|fit|resize|scale|wider|"
    r"narrower|shorter|longer|larger|smaller|increase|decrease|mm|cm|inch\w*)\b", re.I
)
_COSMETIC_INTENT = re.compile(r"\b(colou?r|rename|comment|formatting|spelling|typo)\b", re.I)
_ONE_SOLID = re.compile(
    r"\b(?:as|into|return|produce|build|create|make|keep|ensure|must be|should be)\s+"
    r"(?:a\s+)?(?:one|single|1)\s+(?:(?:connected\s+)?(?:solid|body)|connected\s+part)\b", re.I)
_ASSEMBLY = re.compile(r"\b(assembl(?:y|ies)|separate\s+(?:parts|bodies|solids)|"
                       r"multiple\s+(?:parts|bodies|solids)|two\s+(?:parts|bodies|solids))\b", re.I)
_CONSTRUCTION = re.compile(r"\b(?:build|create|make|add)\s+(?:me\s+)?(?:a|an)\s+", re.I)
_EMPTY_INTENT = re.compile(r"\b(?:empty|blank)\b|\bclear\s+(?:the\s+)?(?:board|scene|model|all|everything)\b|"
                          r"\b(?:delete|remove)\s+(?:all|everything|every\s+part)\b", re.I)
_READ_INTENT = re.compile(r"\b(explain|review|inspect|why|how|describe|analy[sz]e)\b", re.I)


def _intent_checks(output, message: str, *, current: bool) -> None:
    """Enforce only explicit, mechanically observable requirements.

    Do not guess what object the user intended, or impose connectivity on an
    assembly. Scoped requests such as 'make the base one solid' require richer
    part grounding and are deliberately left to the agent's inspection tools.
    """
    deliverables = [p for p in output.parts.values() if not p.reference]
    if (_ONE_SOLID.search(message) and not _ASSEMBLY.search(message)
            and not _EMPTY_INTENT.search(message) and not _READ_INTENT.search(message)):
        if len(deliverables) != 1 or deliverables[0].solid_count != 1:
            raise CadError("DESIGN_INTENT_FAILED", "the request explicitly requires one connected solid",
                           hint="join the intended geometry into one solid, preserving the requested features",
                           details={"deliverable_parts": len(deliverables),
                                    "solids": [p.solid_count for p in deliverables]})
    if (current and not deliverables and _CONSTRUCTION.search(message)
            and not _EMPTY_INTENT.search(message) and not _READ_INTENT.search(message)):
        raise CadError("DESIGN_INTENT_FAILED", "the construction request produced no deliverable geometry",
                       hint="return the requested Part(s); use an empty build only when clearing or starting an empty model")


def _error(exc: Exception) -> dict:
    return exc.to_dict() if isinstance(exc, CadError) else {
        "code": "VALIDATION_FAILED", "message": str(exc) or type(exc).__name__
    }


def _snapshot(root: Path, target: Path) -> None:
    """Copy inputs, including nonstandard asset types, without mutable caches.

    Dereference file symlinks so ordinary writes cannot escape to the live
    source. Directory links are supported unless they form a recursive cycle.
    Bound resource use rather than silently omit large/unknown dependencies.
    """
    files = 0
    size = 0

    def copy_dir(src: Path, dst: Path, ancestors: frozenset[Path]) -> None:
        nonlocal files, size
        resolved = src.resolve()
        if resolved in ancestors:
            raise ValueError(f"recursive project directory symlink: {src.relative_to(root)}")
        dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(src.iterdir()):
            if item.name in IGNORED_DIRS or item.name in {".pytest_cache", ".mypy_cache", ".ruff_cache"}:
                continue
            dest = dst / item.name
            if item.is_dir():
                copy_dir(item, dest, ancestors | {resolved})
            elif item.is_file():
                files += 1
                size += item.stat().st_size
                if files > 10000 or size > 512 * 1024 * 1024:
                    raise ValueError("candidate inputs exceed 10,000 files or 512 MiB; move generated outputs outside the project")
                shutil.copy2(item, dest, follow_symlinks=True)
            elif item.is_symlink():
                raise ValueError(f"missing input referenced by symlink: {item.relative_to(root)}")

    copy_dir(root, target, frozenset())


def _corner_overrides(parameters: dict, base: dict, limit: int = MAX_CORNERS, *, focus: str = "") -> tuple[list[dict], dict]:
    bounded = []
    skipped = []
    mentioned = set(re.findall(r"\w+", focus.lower()))
    ordered = sorted(parameters.items(), key=lambda item: (
        not bool(set(re.findall(r"\w+", item[0].lower())) & mentioned), item[0]))
    for name, p in ordered:
        if isinstance(p.value, bool) or not isinstance(p.value, (int, float)):
            continue
        if (p.min is None or p.max is None
                or not math.isfinite(p.min) or not math.isfinite(p.max)):
            skipped.append(name)
            continue
        if p.min > p.max:
            raise ValueError(f"parameter {name!r} has reversed bounds [{p.min}, {p.max}]")
        if p.min != p.max:
            bounded.append((name, p.min, p.max))
    skipped.extend(p[0] for p in bounded[MAX_PARAMETERS:])
    bounded = bounded[:MAX_PARAMETERS]
    n = len(bounded)
    if not n:
        return [], {"parameters": [], "skipped_parameters": skipped, "possible_corners": 0}
    masks = list(itertools.product((0, 1), repeat=n))
    # Extremes alone miss crossed constraints such as outer > inner. Greedily
    # cover all endpoint pairs before spending the remaining bounded budget.
    selected = []
    uncovered = {(i, j, a, b) for i in range(n) for j in range(i + 1, n)
                 for a in (0, 1) for b in (0, 1)}
    while masks and len(selected) < limit:
        if not selected:
            mask = masks[0]
        elif len(selected) == 1:
            mask = masks[-1]
        else:
            mask = max(masks, key=lambda m: sum((i, j, m[i], m[j]) in uncovered
                                               for i in range(n) for j in range(i + 1, n)))
        selected.append(mask)
        masks.remove(mask)
        uncovered.difference_update((i, j, mask[i], mask[j]) for i in range(n) for j in range(i + 1, n))
    variants = []
    current = {name: p.value for name, p in parameters.items()}
    for mask in selected:
        changes = {p[0]: p[1 + bit] for p, bit in zip(bounded, mask)}
        if all(current[name] == value for name, value in changes.items()):
            continue  # The current-state check already exercises this corner.
        variants.append({**base, **changes})
    return variants, {"parameters": [p[0] for p in bounded], "skipped_parameters": skipped,
                      "possible_corners": 2 ** n, "selected_corners": len(selected),
                      "endpoint_pairs_covered": not uncovered}


def _inspect_build(output, *, print_diagnostics: bool) -> tuple[dict, list[dict]]:
    from .printability import DEFAULT_BED, report

    parts = {}
    warnings = list(output.warnings)
    for name, part in output.parts.items():
        if not part.has_body or not part.is_valid():
            raise CadError("INVALID_SHAPE", f"part {name!r} has no body or invalid topology",
                           hint="repair the failed boolean/feature before publishing")
        size = list(part.bbox.size)
        volume = float(part.volume)
        if not all(math.isfinite(x) for x in [*size, volume]):
            raise CadError("INVALID_SHAPE", f"part {name!r} has non-finite dimensions or volume")
        if not part.reference and (part.solid_count < 1 or volume <= 0 or any(x <= 0 for x in size)):
            raise CadError("INVALID_SHAPE", f"part {name!r} is not a positive-volume 3D solid",
                           details={"size_mm": size, "volume_mm3": volume, "solids": part.solid_count})
        info = {"valid": True, "size_mm": size, "volume_mm3": volume,
                "solids": part.solid_count, "reference": part.reference}
        if not part.reference and part.solid_count > 1:
            warnings.append({"code": "MULTIPLE_SOLIDS", "part": name,
                             "message": f"{part.solid_count} disconnected solids; confirm this is intentional"})
        if not part.reference:
            printed_size = list((part.as_printed() if part.print_down else part).bbox.size)
            fits = all(x <= maximum + 1e-6 for x, maximum in zip(printed_size, DEFAULT_BED))
            info["print"] = {"bed": {"size_mm": list(DEFAULT_BED), "fits": fits},
                             "geometry": {"bbox_mm": printed_size}}
            if not fits and not print_diagnostics:
                warnings.append({"code": "BED_OVERFLOW", "part": name,
                    "message": f"{printed_size} mm exceeds the assumed {list(DEFAULT_BED)} mm bed at this endpoint",
                    "hint": "confirm printer limits, split the design, or constrain the allowed parameter range"})
        if print_diagnostics and not part.reference:
            try:
                rep = report(part, per_face=1)
                info["print"] = {k: rep[k] for k in ("bed", "orientation")}
                info["print"]["thin"] = {k: v for k, v in rep["thin"].items() if k != "faces"}
                info["print"]["overhang"] = {k: v for k, v in rep["overhang"].items() if k != "faces"}
                warnings.extend({**w, "part": name} for w in rep["warnings"])
            except Exception as exc:
                warnings.append({"code": "PRINT_DIAGNOSTIC_UNAVAILABLE", "part": name, "message": str(exc)})
        parts[name] = info
    return parts, warnings


def _validate_snapshot(root: Path, message: str) -> dict:
    from .runner import _purge_project_modules, run_script

    config = ProjectConfig.load(root)
    base = read_overlay(root)
    checks = []
    strict_corners = bool(_DESIGN_INTENT.search(message)) or not _COSMETIC_INTENT.search(message)
    summary = {"corner_failures_blocking": strict_corners, "limitations": [
        "Endpoint samples do not prove interior values or conditional parameter branches.",
        "Geometry validity does not prove design intent, mating clearance, strength, or moving-part function.",
        "Print diagnostics use a 256 mm bed, 0.4 mm nozzle and sampled thickness; warnings are advisory.",
        "Trusted Python executes in a separate process, not a security sandbox."
    ]}

    def check(params: dict, kind: str):
        entry = {"kind": kind, "parameters": params, "ok": False}
        checks.append(entry)
        try:
            # Each variant starts from the same inputs even if an ordinary
            # model writes local output or modifies a helper during a build.
            with tempfile.TemporaryDirectory(prefix="cad-variant-") as temporary:
                scratch = Path(temporary) / "project"
                _snapshot(root, scratch)
                try:
                    output = run_script(scratch / config.entry, params, capture=False)
                    _intent_checks(output, message, current=kind == "current")
                    entry["parts"], entry["warnings"] = _inspect_build(output, print_diagnostics=kind == "current")
                finally:
                    _purge_project_modules(scratch)
            entry["empty_model"] = output.empty_model
            entry["ok"] = True
            return output
        except Exception as exc:
            entry["error"] = _error(exc)
            if "scratch" in locals():
                entry["error"] = json.loads(json.dumps(entry["error"]).replace(str(scratch), "<candidate>"))
            return None

    current = check(base, "current")
    if current is None:
        return {"ok": False, "error": checks[-1]["error"], "checks": checks, "summary": summary}
    if current.empty_model:
        summary.update({"empty_model": True, "tested_corners": 0})
        return {"ok": True, "checks": checks, "summary": summary}
    variants, coverage = _corner_overrides(current.parameters, base, focus=message)
    summary.update(coverage)
    for params in variants:
        check(params, "parameter_corner")
        variant = checks[-1]
        if variant["ok"]:
            before = {n: p["solids"] for n, p in checks[0]["parts"].items() if not p["reference"]}
            after = {n: p["solids"] for n, p in variant["parts"].items() if not p["reference"]}
            if before != after:
                variant["warnings"].append({"code": "PART_STRUCTURE_CHANGED", "message":
                    "Part names or solid counts change at this endpoint; confirm this is intended.",
                    "current": before, "corner": after})
    failed = [c for c in checks[1:] if not c["ok"]]
    summary.update({"tested_corners": len(variants), "failed_corners": len(failed)})
    result = {"ok": not failed or not strict_corners, "checks": checks, "summary": summary}
    if failed:
        result["error" if strict_corners else "warning"] = {
            "code": "PARAMETER_COMBINATION_FAILED",
            "message": f"{len(failed)} sampled parameter combination(s) fail; first: {failed[0]['parameters']}",
            "hint": "repair the geometry across declared ranges, constrain dependent dimensions, or narrow the advertised bounds",
            "details": {"failed_combinations": [{"parameters": c["parameters"], "error": c["error"]} for c in failed]}
        }
    return result


def validate_candidate(root: Path, source: str, *, message: str = "",
                       cancelled: Callable[[], bool] | None = None) -> dict:
    """Check source and bounded variants without changing live files or HEAD.

    Current-state errors always reject the candidate. Failed range samples
    reject design/parameter requests; unrelated edits receive those findings as
    advisory evidence so pre-existing broken ranges do not prevent repair.
    """
    try:
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise CadError("VALIDATION_CANCELLED", "candidate validation was cancelled; candidate was not published")

        check_cancelled()
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")
        entry = Path(ProjectConfig.load(root).entry)
        if entry.is_absolute() or ".." in entry.parts:
            raise ValueError("candidate entry must be a relative path inside the project")
        compile(source, str(entry), "exec")
        with tempfile.TemporaryDirectory(prefix="cad-candidate-") as temporary:
            temp = Path(temporary)
            snapshot = temp / "project"
            _snapshot(root, snapshot)
            check_cancelled()
            candidate = snapshot / entry
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(source, encoding="utf-8")
            result_file = temp / "validation.json"
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")]))
            process = subprocess.Popen(
                [sys.executable, "-m", "cad.design_validation", "--worker", str(snapshot), str(result_file), message],
                cwd=snapshot, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            tail = bytearray()
            stopped = threading.Event()

            def drain_errors():
                # Drain without buffering an unbounded stream in memory or on
                # disk. The poll deadline also handles inherited pipe handles.
                assert process.stderr is not None
                while not stopped.is_set():
                    ready, _, _ = select.select([process.stderr], [], [], 0.1)
                    if ready:
                        chunk = os.read(process.stderr.fileno(), 8192)
                        if not chunk:
                            break
                        tail[:] = (tail + chunk)[-2000:]

            reader = threading.Thread(target=drain_errors, daemon=True)
            reader.start()
            try:
                deadline = time.monotonic() + VALIDATION_TIMEOUT
                while process.poll() is None:
                    check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, VALIDATION_TIMEOUT)
                    try:
                        process.wait(timeout=min(0.2, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                check_cancelled()
            finally:
                # Stop ordinary child tasks even after a successful build.
                # Validation must never leave an asynchronous worker running.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                reader.join(timeout=0.2)
                stopped.set()
                reader.join(timeout=0.2)
                if process.stderr is not None:
                    process.stderr.close()
            stderr = tail.decode("utf-8", errors="replace")
            if process.returncode != 0 or not result_file.is_file():
                return {"ok": False, "error": {"code": "VALIDATION_PROCESS_FAILED",
                        "message": f"candidate validation exited with status {process.returncode}",
                        "details": {"stderr": stderr}}, "checks": []}
            if result_file.stat().st_size > MAX_RESULT_BYTES:
                return {"ok": False, "error": {"code": "VALIDATION_OUTPUT_LIMIT",
                        "message": "candidate diagnostics exceeded 2 MiB; simplify the model or parameter set"}, "checks": []}
            result = json.loads(result_file.read_text())
            # Scratch paths are gone by the time repair sees the result.
            return json.loads(json.dumps(result).replace(str(snapshot), "<candidate>"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "VALIDATION_TIMEOUT",
                "message": f"candidate validation exceeded {VALIDATION_TIMEOUT} seconds",
                "hint": "reduce expensive geometry or parameter combinations; candidate was not published"}, "checks": []}
    except Exception as exc:
        return {"ok": False, "error": _error(exc), "checks": []}


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--worker":
        raise SystemExit("internal candidate validation worker")
    try:
        result = _validate_snapshot(Path(sys.argv[2]), sys.argv[4])
    except Exception as exc:
        result = {"ok": False, "error": _error(exc), "checks": []}
    Path(sys.argv[3]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
