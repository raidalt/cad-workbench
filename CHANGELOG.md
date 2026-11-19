# Changelog

Each release has a `## X.Y.Z (date)` section; the release workflow publishes it as the release notes.
Versions follow [semver](https://semver.org/). Before 1.0, a minor version (0.x.0) may change the scripting
API or the CLI; patch versions do not.

## Unreleased

- **Live view:** `cad serve [--port N] [--host H] [--no-open] [--project DIR]` starts a local browser
  viewport for a project. It watches `model.py`, `cad.toml` and `params.toml`, rebuilds on save and streams
  the build over Server-Sent Events, keeping a coarse preview mesh per feature so a timeline can replay the
  part growing. The page adds orbit/pan/zoom, part visibility, section planes, face picking with area,
  centre and reference names, parameter sliders, revision switching, and stress overlays for declared
  `Study`s. One process, standard library only, engine in-process (~50 ms rebuilds; unchanged inputs come
  from the revision cache), works offline. A failed build keeps the last good model on screen and shows the
  error with its script line. It is for humans; the agent workflow (`cad build`/`inspect`/`render`/`export`)
  is unchanged.
- **Print readiness:** `cad print [PART] [--material M] [--bed B|A1 mini] [--angle DEG] [--min-wall MM]`
  checks a part for printing without a slicer: mass from the solid volume and the material's density, bed fit
  in the print pose (exit 4 when it does not fit, with axis-aligned sizes that would), thin walls from
  ray-cast local thickness, and overhangs from face normals, each reported per face with its reference names
  and face id. The same report is overlaid live in `cad serve` (thin-wall and overhang face highlighting),
  and `--json` returns it whole. Estimates; `cad slice` is still the real number.
- **Chat in the viewport:** a chat drawer drives a local Ollama model (`CAD_CHAT_URL`/`CAD_CHAT_MODEL`) with
  no key and no cloud. The model gets the current script and a short API card, and answers with prose plus,
  when needed, the complete new script in one code block; the server compiles it, backs up the old file to
  `.cad/backups`, writes it and lets the ordinary source watcher build a revision, so the viewport updates
  as if the file had been edited. Undo reverts the last applied edit. A reply without a code block changes
  nothing, and a script that does not parse never reaches disk.
- **WebGPU viewport:** the page renders through three.js' WebGPU backend when the browser exposes
  `navigator.gpu`, and falls back to WebGL2 otherwise; every viewport feature (edges, sections, picking,
  print and stress overlays) works on both, and the status bar names the backend in use.
- **Chat reliability:** every chat turn now carries an authoritative build receipt (`verified`, `revision`,
  `reverted`, error) that survives a reload, and a script that does not build is reverted byte-for-byte
  instead of leaving a broken model behind. Failed turns stay in the conversation and can be retried
  in place (same request, no duplicate turn, no second write). A revision may now legitimately have **no
  parts** (`build()` returns `{}`), which the viewer shows as an empty-model state; `POST /api/new-model`
  starts a fresh script — clearing stale parameter overrides — without deleting older revisions, and
  `POST /api/orient` lays a part on a named face with build verification and rollback.

## 0.1.2 (2026-11-11)

- Renamed: the repository is now [andrewimm/cli-cad](https://github.com/andrewimm/cli-cad) and the package
  `cli-cad` (it was `clicd`). The command is still `cad`. Old links redirect, and the installer replaces an
  existing `clicd` install: re-run it to upgrade. Uninstall with `uv tool uninstall cli-cad`.

## 0.1.1 (2026-11-11)

- `snap_hook`: `catch_face_at` is now where the catch face leaves the beam (its end nearest the root). For a
  hook with `retention_angle` below 90 it used to give the tip end, so a ledge placed there overlapped the
  sloped catch face.
- README: a "Get started" prompt to paste into a coding agent, which installs the CLI and the agent skill and
  starts designing a first print.

## 0.1.0 (2026-11-08)

First release. `cad` turns a Python script into exact solid geometry (Open CASCADE BRep), checks it with
numbers, renders it headlessly, and exports manufacturing files. It is built for coding agents: every
command has `--json` output, stable error codes and hints.

- **Modelling:**
  - boxes, cylinders and spheres;
  - extrude and revolve of 2D profiles, including text;
  - lofts, sweeps along paths, drafts;
  - holes, fillets, chamfers, shells, booleans and transforms;
  - named faces and edges that survive edits, plus geometric selectors.
- **Design for printing:**
  - screw holes M2–M8 (clearance, counterbore, countersunk, tap, heat-set insert, nut pocket);
  - threads, snap-fit hooks, elephant's-foot chamfers;
  - minimum wall thickness;
  - a material catalogue.
- **Assemblies:**
  - joints (revolute, slider, cylindrical, fixed) driven by parameters;
  - clearance and interference checks;
  - `cad sweep` over a parameter range;
  - imported vendor parts (STEP, IGES, STL) as reference envelopes.
- **Stress analysis** (`cad fea`, optional install):
  - load cases on named faces;
  - safety factor, including across print layers;
  - hot spots traced to the features and parameters that cause them;
  - stress, safety and displacement renders.
- **Output:**
  - STEP, STL and BREP;
  - 3MF with materials, which loads correctly in Bambu Studio, OrcaSlicer, PrusaSlicer (`--3mf-for prusa`
    for multi-material) and Cura;
  - `cad slice` through Bambu Studio: print time, grams per filament, a ready-to-print file.
- **Runtime:**
  - a Rust client and per-installation daemon with warm Python workers;
  - immutable revisions, parameter overrides and diffs;
  - deterministic software renders;
  - a standalone mode for sandboxes.
- **Install:** prebuilt for macOS (Apple silicon, Intel) and Linux (x86_64, aarch64) with
  `curl -LsSf https://github.com/andrewimm/cli-cad/releases/latest/download/install.sh | sh`. Stress analysis
  is not available on Linux aarch64, because gmsh publishes no wheels for it.
