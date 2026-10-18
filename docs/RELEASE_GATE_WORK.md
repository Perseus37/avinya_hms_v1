# Release gate work log

## Purpose

Establish repeatable CI release checks for the Avinya HMS backend, frontend,
database migrations, and Docker Compose configuration. Keep this work
backend/operations-focused; do not build a tenant-facing hospital UI here.

This file is the continuation record. Update each status and record exact
commands/results as work proceeds so another session can resume without
repeating completed investigation.

## Starting point

- The production-readiness roadmap reports that the tracked workflow files
  `.github/workflows/backend-ci.yml` and `.github/workflows/ci.yml` are deleted
  in the current worktree.
- The roadmap reports 228 Python tests passing and 2 opt-in PostgreSQL 17 tests
  skipped in the latest full local run.
- PostgreSQL 17 integration coverage exists but requires its documented
  opt-in environment.
- Frontend build and Compose validation need to be checked against the current
  manifests before defining CI steps.
- The worktree contains many unrelated modifications and deletions. Preserve
  them; do not restore or overwrite existing workflow content without
  inspecting the exact diff and understanding whether the deletion is
  intentional.

## Scope and acceptance criteria

1. Inspect existing tracked/untracked changes, CI manifests, dependency files,
   PostgreSQL test setup, frontend build scripts, and Compose files.
2. Agree on the smallest safe workflow change that respects existing worktree
   edits and runs:
   - Python tests and compile/type/lint checks only where repository tooling
     already supports them.
   - Frontend production build.
   - Docker Compose configuration validation without requiring production
     secrets or live infrastructure.
   - PostgreSQL migration integration tests where the CI runner can provide a
     disposable PostgreSQL 17 service.
3. Keep opt-in or infrastructure-dependent checks explicit; do not claim they
   ran unless CI/local evidence confirms it.
4. Validate workflow syntax and run the feasible local checks.
5. Update `docs/PRODUCTION_READINESS_ROADMAP.md` with verified outcomes and
   remaining deployment-only gaps.

## Progress

- [x] Created this continuation record before implementation.
- [x] Inspect workflow deletions and current project test/build/Compose
      commands.
- [x] Implement the agreed CI release gates without disturbing unrelated
      worktree changes.
- [x] Validate workflow syntax and feasible local checks.
- [x] Record final evidence and remaining gaps in this file and the roadmap.
- [x] Confirm the new workflow is the sole intended workflow and add dependency
      remediation/next-step work to this log before proceeding.
- [x] Remediate known Python and UI dependency findings with compatible
      upgrades; avoid skipping or suppressing unresolved advisories.
- [x] Add blocking dependency audit gates after the manifests audited clean.
- [x] Run targeted audits, UI build, Python tests, and workflow/config checks;
      update remaining operational gaps.
- [x] Verify Docker Desktop availability and build the production image locally.
- [x] Remediate packaging-tool vulnerabilities discovered in the built image
      and rerun the build and configured vulnerability scan.
- [x] Confirm GitHub Actions API access, publish the approved worktree through
      a review branch/PR, resolve hosted-only failures, and pass all release
      gates.

## Investigation findings

- The two old workflow files are deleted in the worktree as intended. The new
  `.github/workflows/release-gates.yml` is the only workflow in the current
  `.github/workflows` directory; do not restore either legacy workflow.
- Python is 3.11; `pytest.ini` discovers the full `tests/` suite. The suite
  uses SQLite fixtures for API tests. `tests/test_postgres17_integration.py`
  has an opt-in live migration-parity test that creates/drops disposable DBs;
  the backup round-trip test additionally requires Docker inside its test and
  will remain opt-in/skipped in the service-container job.
- `requirements.txt` includes PyTorch. Match the Dockerfile's CPU-only PyTorch
  install in CI to avoid pulling CUDA packages.
- The onboarding UI has a package lock and a `build` script, with no existing
  lint or type-check script.
- Both Compose files require environment interpolation and matching `env_file`
  files. CI can copy the checked-in example files to their expected local
  names before validating `docker compose ... config --quiet`; these are
  templates only and must not be used to start a deployment.
- Local validation tools available: Python 3.11.9, Node 22.16.0, npm 11.14.0,
  and Docker Compose 5.5.1.
- CI jobs: Python suite plus live PostgreSQL 17 migration/schema parity;
  Python dependency audits for both runtime and integration manifests;
  onboarding UI production build and npm audit; development and production
  Compose config validation; production Docker image build and Trivy scan. Use
  read-only repository permissions. Protected-branch enforcement remains a
  repository settings responsibility.

## Implemented

- Added `.github/workflows/release-gates.yml` without restoring or modifying
  the two deleted legacy workflows. It is the sole workflow and runs on
  pushes/PRs to `main`, `dev`, and `release/**`, with manual dispatch.
- Added jobs for the Python suite and live PostgreSQL 17 migration/schema
  parity, the locked onboarding UI build, development and production Compose
  config validation using example env files, and the production Docker image
  build. Workflow token permissions are read-only.
- Do not treat Compose config validation as a deployment, image build, or
  production-env validation. The template files are used only to resolve
  Compose syntax and required interpolation.
- Added `.github/dependabot.yml` for weekly pip, npm, and GitHub Actions
  dependency-update PRs (up to five open PRs per ecosystem). Repository
  settings must separately enable Dependabot security updates/alerts.
- Updated the onboarding UI's Vite dependency from 5.x to patched Vite 6.4.4.
  The installed React plugin declares Vite 6 compatibility.
- Replaced `python-jose` JWT calls with PyJWT 2.15.1 and its compatible
  `InvalidTokenError` handling. This removes the vulnerable `ecdsa` dependency
  from the runtime dependency graph.
- Upgraded Python packages with published fixes: pytest 9.0.3, PyJWT 2.15.1,
  PyTorch 2.14.1 (CPU-only in Docker/CI), Transformers 5.20.1, and Pillow
  12.3.0.
- Migrated Tailwind CSS 3 to Tailwind CSS 4 with the Vite integration and
  corresponding CSS/PostCSS configuration updates.
- Added blocking `pip-audit` checks for both Python manifests and `npm audit`
  for the UI. Both dependency trees report no known advisories as of this
  validation run.
- Added a Trivy image scan to the production image job. It blocks on fixable
  HIGH/CRITICAL OS or library vulnerabilities and leaves unfixed findings
  visible for follow-up without failing on issues with no available fix.
- Pinned setuptools 84.0.0 in the Docker image after Trivy traced fixable
  findings to stale vendored `jaraco.context`/`wheel` metadata in the base
  image's original setuptools package.

## Validation evidence

- Parsed the workflow YAML locally and confirmed its four jobs; confirmed
  there is only one workflow file in `.github/workflows`.
- `npm audit --audit-level=moderate` in `onboarding-ui`: passed, 0
  vulnerabilities.
- `npm run build` in `onboarding-ui`: passed (Vite 6 / Tailwind 4 production
  build).
- `.venv\Scripts\python.exe -m pip_audit -r requirements.txt --desc=off`:
  passed, no known vulnerabilities.
- `.venv\Scripts\python.exe -m pip_audit -r requirements-integrations.txt
  --desc=off`: passed, no known vulnerabilities.
- `.venv\Scripts\python.exe -m pytest -q`: **228 passed, 2 skipped**.
- Focused auth and WebSocket tests after the PyJWT migration: **18 passed**.
- Procurement parser module imports successfully with PyTorch 2.14.1+cpu,
  Transformers 5.20.1, and Pillow 12.3.0. The external model download and
  invoice extraction itself were not exercised.
- `docker compose --env-file .env -f docker-compose.yml config --quiet`:
  passed using the pre-existing local `.env`; it was not modified.
- `docker compose --env-file .env.production -f docker-compose.prod.yml config
  --quiet`: passed using a temporary copy of `.env.production.example`; the
  temporary `.env.production` was removed afterward.
- `git diff --check`: passed.
- **Continuation update:** Docker Desktop is available (`desktop-linux`,
  Engine 29.8.2). `docker build --target production --tag avinya-hms-api:ci .`
  passed after pinning setuptools. The rebuilt image is about 2.25 GB, and
  `backend.main` plus JWT/model package imports succeeded with `.env.example`.
- Trivy 0.70.0 initially found two fixable HIGH issues in the base image's
  vendored setuptools metadata (jaraco.context 5.3.0 and wheel 0.45.1).
  After the setuptools pin and rebuild, the exact configured scan
  (`--ignore-unfixed --pkg-types os,library --severity HIGH,CRITICAL`)
  reported **0 HIGH/CRITICAL findings with fixes available**. The scan uses
  `--ignore-unfixed`; this does not establish that no unfixed vulnerabilities
  exist. The workflow's Trivy action will perform its own scan on GitHub.
- At the time of this initial validation, GitHub Actions still showed the two
  legacy workflows because the local replacement had not yet been pushed.
  This state was superseded by PR #1 and the hosted runs documented below.
- Retrieved the latest remote legacy backend run log: it failed while loading
  `tests/conftest.py` with 13 missing required Settings values (pytest exit
  code 4). This is an old run against the remote branch from Oct 8; after the
  local workflow changes are pushed, verify the new workflow's configured
  environment reaches the full suite successfully.
- **Account switch update (Oct 18, 2026):** `gh auth status` now reports
  `ravipatisheethal-alt`; repository metadata and Actions API access succeed.
  Remote inventory remains unchanged: `.github/workflows/backend-ci.yml` and
  `.github/workflows/ci.yml` are active, and no `release-gates.yml` run exists
  because local changes remain unpushed. The `main` branch-protection endpoint
  returns 404; rulesets query returns 403 because rulesets require GitHub Pro
  or a public repository. Required status checks are therefore not configured/
  verifiable through these endpoints.

## Next steps

1. Review PR #1 and merge it through the repository's normal review process.
   Its latest release-gate run is green; the base branch still has legacy
   workflows until the PR is merged.
2. In repository settings, protect release branches and require the Python/
   PostgreSQL, UI, Compose, and production-image jobs. Current GitHub API
   protection lookup returned 404, and rulesets are unavailable on the
   repository's current plan/visibility.
3. Hosted Trivy passed on the PR image under the workflow's fixable
   HIGH/CRITICAL threshold. Continue periodic image and dependency review.
4. Continue P0 operational verification in `docs/PRODUCTION_READINESS_ROADMAP.md`:
   test attached/off-host backups and restores, confirm least-privilege
   database roles and tenant isolation, and exercise production secrets,
   identity controls, DNS/TLS, and privacy/clinical sign-off.
5. Only after gates and operational controls pass, stage a production-like
   load run and monitoring/alerting exercise; no live patient-data readiness
   is implied by dependency cleanup or passing tests.

## Repository publication checkpoint (Oct 18, 2026)

- Active GitHub account `ravipatisheethal-alt` can access the repository and
  Actions API. Remote `main`/`dev` still point to commit
  `dcc11ccdf1b0b95c598954845379ef68376ebe89`; local branch `dev` is at the
  same commit and has no new commit to push.
- At the time of the original publication checkpoint, the worktree contained a
  broad backend/control-plane restructure, prior HMS features, release gates,
  dependency changes, migrations, tests, and documentation. The user
  subsequently approved publishing all pending changes on a review branch,
  rather than pushing directly to `main` or `dev`; see the current continuation
  below.
- Branch-protection API returned 404; repository rulesets API returned 403 on
  the current plan/visibility.

## Current continuation

The complete approved worktree was committed on
`feature/release-gates-and-security`, pushed to origin, and opened as PR #1
targeting `dev`:
https://github.com/sai1-crypto/avinya_hms/pull/1
The latest full hosted validation, run `39098168408` against PR head
`fd4e699` on `feature/release-gates-and-security`, passed all four jobs:
Python tests plus
PostgreSQL 17 migration parity and both dependency audits, UI audit/tests/build,
development and production Compose validation, and production image build plus
Trivy scan. Earlier runs found and resolved the ignored UI source directory,
core dependencies needed by report imports, and optional mail SDKs needed by
their integration tests. PR #1 has been merged into `dev`.

The user confirmed that `deployments/apollo.env` contained dummy values and
authorized rewriting history. `git-filter-repo` removed this path from all
remote branch histories (`main`, `dev`, and the feature branch), all six local
version tags, and the one published GitHub tag (`v4.5.0`); the five other
version tags existed only in the local clone and were not published. Remote
branches/tags were force-updated and verified clean. GitHub still exposes the
old PR #1 head through its read-only `refs/pull/1/head` reference; only GitHub
Support can purge retained PR/cached commit objects if desired. A local backup
mirror of the pre-rewrite refs is retained in the session files temporarily.
Opened PR #2 to promote `dev` to `main`:
https://github.com/sai1-crypto/avinya_hms/pull/2
Both push-triggered and PR-triggered gates passed on its initial head
`7957c2f`. The continuation-doc update itself will trigger another run; check
that run before merging. Remove the temporary mirror and expire local
unreachable objects after confirming the remote refs and final PR checks.

## Resume instructions

Start by inspecting this file and `docs/PRODUCTION_READINESS_ROADMAP.md`, then
inspect the current `.github/workflows` status and manifests. Do not assume the
workflow deletions are accidental. Continue at the first unchecked progress
item, updating this log as meaningful phases complete.
