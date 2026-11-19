# A harness for conversational 3D design

The useful unit of work is a verified design revision: a user request, its geometry target, a candidate
script, the checks actually run, and the resulting revision or failure. A valid solid is one part of that
record. A clamp also needs to fit its dowel, a snap needs to engage, and an adjustable model needs to keep
those properties across its advertised settings.

## The editing journey

1. **Select a target.** A user selects a part, face, or named feature in the viewport and asks for a change.
   The request carries the selection's revision and identity. The visible selection chip lets the user
   inspect or clear that target before sending.
2. **Resolve against the model.** The server resolves the selection against the current revision's own
   geometry and metadata. A client-supplied label or face index alone cannot establish a target. Historical
   or stale selections require returning to the current revision and selecting again.
3. **Give the model the right context.** Supply the current source, resolved target, relevant measurements,
   and concise API guidance selected for the request. Keep the original user wording alongside that context.
   An instruction such as “make this hole larger” must retain its target through the turn and retry.
4. **Validate a candidate.** Build the proposed complete script in a temporary project before changing live
   source. Exercise bounded parameter cases and collect geometry diagnostics. Candidate failures become
   actionable feedback with the failing parameter values and check, rather than an apparent successful edit.
5. **Repair within a bound.** Give a failed candidate's diagnostics back to the model for one repair attempt.
   Stream checking and repair progress. A second failure ends the turn with a failure receipt.
6. **Publish and verify.** Confirm the live source still matches the turn's starting state, apply the candidate,
   and verify the live build. On failure, retain or restore the previous verified state. Record the produced
   revision separately from the active revision so history remains truthful after later edits.

Text-only explanations need no geometry revision. An empty response, truncated output, cancelled request,
or invalid candidate must never be presented as an applied design. Clearing a design is a valid empty build
(`return {}`), with no placeholder solid. Viewing history is read-only and does not move the live design.

## Runtime boundaries

The implementation is split so each boundary can be tested without a live model provider:

| Boundary | Responsibility |
|---|---|
| Viewport and chat UI | Display selection, exchange same-origin selection messages, submit revision-bound target, show validation progress and the final receipt |
| `selection_context.py` | Resolve part, face, or feature from authoritative revision data and reject invalid or stale targets |
| `design_skills.py` | Add concise, intent-specific guidance using supported CAD APIs |
| `design_validation.py` | Validate candidate source in a disposable project and return structured results |
| `design_checks.py` | Assert material or void at specific points in the solid; assertions run at every sampled build |
| `chat.py` and `serve.py` | Coordinate generation, bounded repair, application, verification, cancellation, and revision guards |
| `chat_retry.py` | Preserve later conversation turns when retrying earlier failures |
| `skills/cad/SKILL.md` | Reusable CLI workflow for agents operating outside the embedded chat |

The selection protocol uses `cad:selection` and `cad:request-selection` messages between the viewport and
embedded chat. A target contains `revision`, `part`, and `kind` (`part`, `face`, or `feature`), plus the
applicable `face`, `ref`, or `feature`. Both sides check message origin and source. Geometry detail is resolved
on the server. These messages convey a target, not permission to execute arbitrary text from a model label.

The chat validation event is `chat_validation`, with phase `checking`, `repairing`, `passed`, or `failed`.
The final turn receipt, rather than a transient progress label, establishes whether a change was applied
and verified. Retrying an old turn must preserve newer history and must not silently retarget an old face
index to the current model.

The current validator builds with the project's effective parameter overrides. It checks topology,
finite dimensions and volume, and positive-volume solids for non-reference parts. It then samples up to
12 endpoint combinations across at most eight bounded numeric parameters, prioritising crossed endpoint
pairs. The report lists skipped parameters and coverage. Current-state failure always rejects a candidate;
endpoint failures reject design/range requests and are advisory for clearly cosmetic edits. An explicit
one-connected-solid request checks that condition at every sampled build. Otherwise changes in part
names or solid counts are warnings. Intent routing uses whole-word matching and is therefore a heuristic.

Print diagnostics run on the current parameter state with a 256 mm bed, 0.4 mm nozzle and sparse thickness
sampling. They are advisory, and do not establish the user's actual printer settings. The subprocess has a
90-second validation timeout. Its project snapshot preserves inputs and overrides while excluding mutable
caches, with a limit of 10,000 files or 512 MiB. This isolates normal project state and process failures;
trusted model Python still executes with the caller's filesystem and network permissions.

## Acceptance checks depend on intent

The runtime's bounded candidate checks provide a useful baseline. The following checks are also part of
the design workflow and benchmark criteria; they are not all inferred or automatically enforced by chat.

| Design intent | What must be checked | Available evidence |
|---|---|---|
| Selected edit | The selected feature changed as requested; unrelated parts and required interfaces retain their dimensions | Revision-bound target, measured dimensions, named references, before/after renders |
| Assembly or fixture | Intended contacts, mating alignment, interference, assembly access | `cad clearance --min 0.5`, optional `--allow-contact`, section renders, measured interface positions |
| Moving joint | Clearances at endpoints and intermediate poses | `cad joints`, `cad sweep knee 0:90:15 --min 0.5 --render front` |
| Threaded pair | Matching thread size and pitch, axes, engagement length, manufacturing allowance | `thread`/`threaded_hole`, measured geometry and sections; a physical fit test for the process |
| Snap fit | Catch engagement, insertion/release travel, beam strain and layer direction | `snap_hook` metrics and warnings, clearance checks and section views; a physical cycle test |
| Print-in-place mechanism | Separate moving bodies, manufacturing gap, trapped supports, usable travel | Clearance and motion checks, print orientation and overhang report, slicer inspection and physical test |
| Printable part | Bed envelope in print pose, wall/ligament thickness, supported surfaces, material assumptions | `print_on`, `cad print --bed A1 --nozzle 0.4`, thickness measurements and per-face diagnostics |
| Parametric family | All promised dimensions remain usable across interacting settings | Defaults, extremes, crossed bounds, plus domain-specific geometric invariants |

`cad print` samples wall thickness and estimates geometric print risks. It does not simulate extrusion or
certify retention, fatigue, friction, or layer adhesion. `assert_valid(solids=1)` establishes topology and
solid count, not those design properties. A successful bounded sample does not prove every point in a
continuous parameter range.

For example, a cable holder can remain a single valid solid while neighbouring seats merge at maximum
cable diameter and minimum pitch. Check seat count and minimum web separately. Its maximum count and
maximum pitch can also exceed a printer's bed. For three interacting bounded parameters, the eight corners
are a practical regression set, in addition to defaults and representative interior cases.

A model can encode geometric intent with `require_points` from `cad.design_checks`. Call
`require_points(part, clearance_samples, material=False, label="open cable seat")` for samples that
must lie in void, and use `material=True` for samples that must lie in a web. Compute the samples from
the current parameter values and place them away from exact boundaries. A failing sample identifies
the point and expected condition in the candidate report.

`cad sweep` changes one parameter per run. Use a disposable project copy for combined `cad set a=... b=...`
cases, keeping the live project's source, overrides and active revision intact. Counts need unitless
parameters and explicit integrality handling. Restrict or derive ranges when independent sliders cannot
preserve a required inequality.

## Manual smoke checklist

Run in a disposable project with the same gateway configuration as the app. Record the prompt, starting
revision, selection, candidate result and final receipt. Use a fixed model response in automated tests and
the live provider for exploratory runs.

- [ ] Start `cad serve --no-open`; open the reported local URL. Build a two-part model and verify both parts
  appear in the viewport and inspector.
- [ ] Select a distinctive face, ask “make this wall 3 mm thick,” and check the selection chip, submitted
  target, actual wall measurement, and preservation of the other part.
- [ ] Clear the selection and send an unambiguous whole-model request. Confirm a former target is not reused.
- [ ] Select a target, change revision or inspect history, then submit. Confirm stale targeting is rejected
  with an actionable message and no source change. Return current and reselect successfully.
- [ ] Ask for a 22 mm dowel clamp. Check the actual seat/bore and stated clearance, attachment or retention
  mechanism, print pose, and dimensional evidence in the response.
- [ ] Ask for a threaded cap and a snap closure in separate turns. Inspect actual thread geometry and snap
  engagement in section; check that the model uses the supported API calls.
- [ ] Force a candidate that builds at its defaults but fails a crossed parameter bound. Confirm checking,
  one repair attempt, and either a verified repaired revision or an honest failure preserving the old model.
- [ ] Force a second invalid candidate, an empty provider response and an output-limit response. Confirm
  none produces a success receipt or replaces the last verified geometry.
- [ ] Change source during generation, then let the turn finish. Confirm the pending candidate cannot
  overwrite the concurrent change. Stop a turn and confirm a later turn can run normally.
- [ ] Retry an earlier failed turn after a later successful turn. Confirm later messages survive and the
  old selection is either resolved safely or rejected, never silently redirected.
- [ ] Clear the design, verify zero parts and no old geometry, reload, and build a new design. Test “hide
  parts” separately so visibility changes do not masquerade as deleting the design.
- [ ] Vary interacting parameters at their limits. Check fit, count, minimum webs and bed envelope in
  addition to topology. Export STL/STEP/3MF from the intended revision and verify the chosen part set.

Slicer integration is a separate check. Geometric readiness and export checks do not require launching
Bambu Studio. If the installed slicer crashes, preserve its diagnostic and report slicing as unverified
while continuing geometry tests.

## Further capabilities

The following are extensions, not guarantees of the current chat harness:

- A persisted design contract containing target, preserved interfaces, fit assumptions, manufacturing
  process and executable acceptance checks, updated explicitly when the user changes the requirement.
- Tool calls for selective measurement, section rendering and geometry queries during planning and repair,
  with those observations attached to the resulting revision.
- Stable semantic feature identity across rebuilds, with explicit ambiguity when topology splits or merges.
  Revision-local face indices should continue to expire even if semantic matching is added.
- Intent-specific validators for thread engagement, snap travel, print-in-place clearance, cable-seat
  separation and other functional invariants. Add negative examples that pass BRep validity but fail function.
- Budgeted adaptive parameter exploration with a reported coverage set and a reproducible failing case.
- A revision comparison showing which requirements passed, changed or remain untested, plus physical
  fit-test feedback tied to material and printer settings.

Expand the benchmark by keeping real failed prompts and minimal reproductions. Grade geometry and user
intent independently from the model's prose. Track first-pass success, repaired success, false success,
targeting mistakes, preserved-state failures and elapsed time. Passing a build alone must not earn a
functional-design pass.
