# Security model

**Model scripts are arbitrary Python code.** `cad build`, `cad run`, the model
worker and the eval grader execute them with the full privileges of the user
running `cad`. Process isolation in this system exists for *robustness*
(a kernel crash or runaway computation does not take down the daemon or other
projects); it is **not** a security boundary.

Current guarantees:

- The daemon socket lives in a per-user directory created with mode `0700`
  (`$XDG_RUNTIME_DIR/cad`, or `$TMPDIR/cad-<uid>`; very long paths fall back to
  `/tmp/cad-<uid>/`), so other local users cannot send commands.
- Each project runs in its own worker process; optional `CAD_WORKER_MAX_MEM_MB`
  (RLIMIT_AS, effective on Linux) and `CAD_WORKER_CPU_SECS` (RLIMIT_CPU) bound
  resource use, and per-request timeouts kill stuck workers.
- `cad query` evaluates its expression with Python `eval` (builtins removed) in
  the worker. This is a convenience for trusted local use, not a sandbox.

The downloaded workspace uses the local account's permissions. Its project workers use private Unix sockets and independently recycle after idle time. Run local model files from trusted sources.

The [hosted execution service](../hosted/README.md) authenticates the caller before it loads a private checkpoint. Its fixed Sandbox snapshot receives only job data. Each job uses a disposable VM with denied network egress, no exposed port and a two-minute execution limit. The same boundary applies to validation, the final build, analysis and export, including operations that implicitly load or regenerate a model. Blob credentials and the service token stay in the controller. Only a successful explicit build or Undo can publish a candidate. The controller chooses the project storage paths and conditionally updates the version pointer.

A service token grants its holder access to all projects in that deployment. Keep it in a trusted backend. The local daemon, local HTTP worker and evaluation runner execute with the privileges of their launching account.
