# Hosted CAD execution

This directory is the Vercel project root for the single-owner CAD job API. A trusted backend holds the bearer token and submits jobs. Every geometry operation runs in a fresh Sandbox using the same Python engine as the local workspace. The function stores private, immutable project checkpoints in Blob and uses a conditional write to advance the project version.

## Configure a deployment

1. Import this repository into Vercel, set the root directory to `hosted`, and connect a private Blob store. Configure `CAD_SERVICE_TOKEN` with a randomly generated secret. Keep it on the calling server.
2. In this directory, run `npm ci`, link the project with `vercel link`, and run `vercel env pull .env.local`. Project authentication is required for Sandbox operations.
3. From a trusted checkout run `node --env-file=.env.local prepare-runtime.mjs`. This provisions a Sandbox, installs the locked Python runtime, and prints the snapshot identifier. Set `CAD_SANDBOX_SNAPSHOT` to that identifier in Vercel and deploy. Refresh it when the engine, lockfile, or snapshot lifetime changes.
4. Run `npm test` for the controller and publication tests. Set `CAD_TEST_PYTHON` to an absolute CAD interpreter path to include the native controller integration test. The native integration test is in `tests/test_isolated_job.py` at the repository root.

## Call the API

Send a JSON POST to `/api/cad` with an `Authorization: Bearer <service token>` header.

```json
{"project":"bracket","action":"build","base":null,"args":{"source":"from cad import Part\ndef build():\n    p = Part('plate')\n    p.box(30, 20, 4)\n    return p\n"}}
```

The response includes the published version in `X-CAD-Version`. For later builds or Undo, send that exact value as `base`. A stale write returns HTTP 409. A validation error returns 422. Build accepts optional parameter overrides and a map of relative input filenames to base64-encoded bytes.

Read actions are `state`, `mesh`, `faces`, `preview`, `clearance`, `print`, `fea`, and `export`. Use `validate` to check a candidate without publishing it. Export a print-in-place assembly with `{"format":"3mf","mode":"multimaterial"}` to keep the bodies in their design positions. Omitting mode uses the revision’s project settings. Name a part for STL and STEP. Large results return a redirect to a short-lived signed download URL. Treat that URL as private.

Each job has a two-minute VM deadline and a 128 MiB limit per worker result or compressed checkpoint. Repeated mesh, preview, and export reads reuse an artifact keyed by the published project version and request arguments.

The downloaded local application continues to open with `pnpm run dev`. The hosted endpoint serves backend jobs. Browser user accounts, per-user permissions and hosted chat require a trusted application frontend.
