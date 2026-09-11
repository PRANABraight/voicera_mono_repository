# One-command install via prebuilt images

## Problem

`scripts/install.sh` (added in commits dd4d429 / 33ee252, later reverted)
fetches only 4 files from GitHub — `docker-compose.yaml`, `.env.example`, and
the two application lifecycle scripts — then runs
`docker compose up --build`. That `--build` fails: 4 of the compose services
(`api`, `arq-worker`, `campaign-orchestrator`, `runtime`, `frontend`) build
from Dockerfiles + source under `apps/` and `frontend/`, none of which the
installer downloads. A true no-clone, single-command install requires
prebuilt images the installer can just `pull`.

## Goals

- `curl ... | bash` starts the full stack with no `git clone` and no local
  build step.
- Images are versioned and reproducible: a given release tag always resolves
  to the same image digests.
- Local development (`make application-up`, `docker compose up --build`)
  keeps working exactly as it does today — this change is additive for the
  installer path, not a replacement for the dev workflow.

## Non-goals

- Publishing images for every commit/branch (only tagged releases trigger a
  build).
- Changing anything about the 5 services that already pull prebuilt images
  (`postgres`/ferretdb-base, `ferretdb`, `minio`, `minio-init`, `redis`) —
  those are untouched.
- Multi-arch builds, image signing, SBOMs — out of scope for v1.

## Current state (confirmed by inspection + a local `make application-up` run)

10 containers total. 5 already pull from public registries and need no
change:

| service | image |
|---|---|
| postgres (ferretdb base) | `ghcr.io/ferretdb/postgres-documentdb:17-0.107.0-ferretdb-2.7.0` |
| ferretdb | `ghcr.io/ferretdb/ferretdb:2.7.0` |
| minio | `minio/minio:latest` |
| minio-init | `minio/mc:latest` |
| redis | `redis:7` |

5 are built locally today, from 3 distinct Dockerfile/context pairs:

| service | dockerfile | context | command |
|---|---|---|---|
| api | `apps/api/Dockerfile` | `.` | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` |
| arq-worker | `apps/api/Dockerfile` | `.` | `python -m arq app.tasks.arq.WorkerSettings` |
| campaign-orchestrator | `apps/api/Dockerfile` | `.` | `python -m app.services.campaign.campaign_orchestrator` |
| runtime | `apps/runtime/Dockerfile` | `.` | (default CMD) |
| frontend | `frontend/Dockerfile` | `./frontend` | (default CMD) |

`api`, `arq-worker`, and `campaign-orchestrator` share one Dockerfile/context
and differ only by `command:`. Locally, `docker compose build` currently
tags them as 3 separate image names (`voicera-api`, `voicera-arq-worker`,
`voicera-campaign-orchestrator`) even though most layers are cache-shared.
For publishing we collapse these three into **one** built image
(industry-standard pattern for same-codebase, different-entrypoint
services — e.g. Celery/Django, Sidekiq/Rails), keyed by `command:` override
per service.

`api`/`arq-worker`/`campaign-orchestrator` also bind-mount live source today
(`./apps/api:/app`, `./apps:/app/apps:ro`) for hot-reload. Those mounts are
incompatible with a prebuilt-image install: mounting an empty host
directory over `/app` would erase the baked-in code. The prod path must not
carry these volumes.

## Design revision (post-review)

An external review caught a critical flaw in the override direction below
and three smaller issues. Verified each against actual `docker compose`
behavior (v5.3.1) before adopting:

- **Volume merge is additive, not replacing, for list-type keys.**
  Empirically confirmed: if the base file has
  `volumes: [./apps/api:/app]` and an override omits `volumes:` entirely,
  the merged config still carries the bind-mount — the original design's
  plan to "not repeat volumes in the override to drop them" does not work.
  Scalar keys (`image:`, `command:`) *do* fully replace on override, which
  is why that part of the original design was fine — only the list-type
  `volumes:` assumption was wrong.
- **Fix: invert which file is base vs. override.** Make
  `docker-compose.yaml` the production baseline (`image:`, no bind-mounts) —
  this is what plain `docker compose up` and `install.sh` both use with zero
  `-f` flags. Move `build:` and the dev bind-mounts into
  `docker-compose.override.yaml`, which Compose auto-loads next to the main
  file for anyone running bare `docker compose up`/`make application-up` in
  a checked-out repo — no flag needed, confirmed via `docker compose config`
  in both directions (override adding volumes back on top of a base with
  none merges cleanly; `build:` + `image:` both present resolves to
  pull-with-local-build-fallback, tagged as the `image:` name).
- **`GITHUB_TOKEN` needs explicit `packages: write`.** It does not default
  to write access to GHCR; the workflow must declare
  `permissions: { contents: read, packages: write }` at the job level or
  the push step 403s.
- **Parameterize the registry namespace.** Hardcoding
  `ghcr.io/pranabraight/...` in the compose file breaks the pipeline for
  any other fork. Use `${IMAGE_NAMESPACE:-ghcr.io/pranabraight}/voicera-api:...`.
- **Don't require `VOICERA_VERSION` to be set by hand.** Forcing
  `export VOICERA_VERSION=v1.0.0 && curl ... | bash` defeats the
  single-command goal. Confirmed the originally-suggested
  `/releases/latest` API requires a published GitHub Release object, which
  a bare tag push does not create — that endpoint 404s on a repo with only
  tags (checked live: `PRANABraight/voicera_mono_repository` currently has
  zero tags, `/releases/latest` isn't populated by a tag push alone).
  `install.sh` should instead default to `/repos/<repo>/tags` (works from
  tags alone, no Release needed). Confirmed live against
  `torvalds/linux/tags` that the endpoint's default ordering usually puts
  the newest tag first, but a second review round correctly called out
  that this order is by creation/commit-graph, not guaranteed semver —
  resolved by sorting the extracted tag names with `sort -V | tail -1`
  instead of blindly taking index `[0]`; verified `sort -V` handles
  multi-digit versions correctly (`v1.10.0` sorts after `v1.2.0`, not
  string-before it). (A third review round below replaces the initial
  `jq`-based extraction with a POSIX-only pipeline — see that section for
  why.)

## Second review round (two further fixes, confirmed empirically)

- **FATAL: `-f docker-compose.yaml` suppresses auto-loaded overrides.**
  Verified directly: `docker compose -f docker-compose.yaml config` in a
  test dir with a `docker-compose.override.yaml` present resolves to the
  base file *alone* — no `build:`, no volumes merged in. Only a bare
  `docker compose config` (zero `-f` flags) auto-loads the override.
  This invalidates the earlier claim that
  `scripts/start-application-services.sh` (which already hardcodes
  `-f docker-compose.yaml`) could stay untouched — it was *already*
  silently defeating override auto-load before this change, and would
  keep doing so after. Fix: that script's compose invocation must drop
  the explicit `-f docker-compose.yaml` and call bare
  `docker compose up --build -d` so the dev override actually applies.
  `install.sh`, which deliberately wants the base file *without* the
  override, is correct to keep its explicit `-f docker-compose.yaml`.
- **`install.sh`'s target subdirectory must exist before curl writes into
  it.** Verified directly: `curl -fsSL -o scripts/foo.txt <url>` in an
  empty directory fails with exit 56 ("Failure writing output to
  destination") because `scripts/` doesn't exist yet. Fix: either
  `mkdir -p scripts` before that fetch, or (simpler, taken here) fetch
  `stop-application-services.sh` to the install directory's own root
  instead of a `scripts/` subpath, avoiding the extra directory
  entirely.
- **Checked and cleared: shared image entrypoint.** `apps/api/Dockerfile`
  ends in `CMD ["uvicorn", ...]` with no `ENTRYPOINT` set at all — so
  Compose's `command:` on `arq-worker`/`campaign-orchestrator` fully
  replaces it, no argument-appending risk. Confirmed by reading the
  Dockerfile directly; no change needed here.

## Third review round (three further fixes, one edge case, confirmed empirically)

- **`jq` cannot be assumed present on the install target.** Verified
  directly: fresh `ubuntu:24.04` and `debian:12-slim` containers have no
  `jq` on PATH (this development machine does, which is exactly why the
  gap wasn't caught earlier — it masks the problem during local testing).
  Since `install.sh` has to run on an arbitrary bare VM, not this machine,
  it must not depend on `jq`. Fix: extract tag names with
  `grep -o '"name": *"[^"]*"' | cut -d'"' -f4` instead — verified this
  produces the identical result to the `jq` version against the live
  tags API, then feeds the same `grep '^v' | sort -V | tail -1`.
- **An unexported shell variable is invisible to `docker compose`.**
  Verified directly: with `VOICERA_VERSION=3.19` set but not exported,
  `docker compose config` resolves `${VOICERA_VERSION:-latest}` to
  `latest` anyway — no error, no warning, just silently the wrong image.
  Fix: the resolved version must be assigned via `export VOICERA_VERSION=...`
  (or written into the fetched `.env` file) before invoking `docker compose`,
  not just a plain shell assignment.
- **Downloaded scripts aren't executable by default.** Verified directly:
  a file written by `curl -fsSL -o` lands as mode `644`. Fix:
  `chmod +x stop-application-services.sh` after fetching it.
- **Edge case, correctly scoped as minor rather than critical:**
  unauthenticated GitHub API requests are capped at 60/hour per source IP
  (confirmed via `X-RateLimit-*` response headers). This realistically
  only bites behind a shared NAT or in an automated loop, not a single
  interactive install — so the fix taken here is a clear failure message
  telling the user to set `VOICERA_VERSION` explicitly and retry, not a
  baked-in fallback version number (which would silently go stale and is
  more complexity than this edge case warrants).

## Fourth review round (two fixes, one self-inconsistency caught)

- **`export`-only pins the running shell, not future restarts.** A
  server reboot, or a plain `docker compose down && docker compose up -d`
  run later without going through `install.sh` again, has no exported
  `VOICERA_VERSION` — Compose would silently fall back to `:latest` per
  the `${VOICERA_VERSION:-latest}` default, drifting away from the
  version that was actually tested and installed. Fix: `install.sh` also
  appends `VOICERA_VERSION=<resolved>` to the fetched `.env` file.
  Verified directly that Compose reads `.env` in the working directory
  automatically for variable substitution with no flags needed — a
  `.env`-only value with the shell variable unset still resolves
  correctly. Keep the `export` too: it's what makes the value visible to
  the *current* `install.sh` process before `.env` is even written to
  disk, while `.env` is what makes it durable across future invocations.
  Both matter, for different lifetimes.
- **Fetch URLs must be built from the resolved tag, not a hardcoded
  branch.** The `install.sh` currently committed on disk (predating this
  spec) still hardcodes `BRANCH="${VOICERA_BRANCH:-dev-doc}"` and fetches
  from that branch ref — confirms this ordering isn't automatic and has
  to be stated explicitly: version resolution must run *before* any
  `curl` call, and every fetch URL must interpolate the resolved tag
  (`raw.githubusercontent.com/<namespace>/<repo>/<resolved-tag>/...`),
  not a branch name. This guarantees the fetched `docker-compose.yaml`
  matches the schema the resolved image tags were actually built against.
- **Self-inconsistency caught: the round-2 fix for `stop-application-services.sh`
  conflicts with that script's own path assumptions.** Read the actual
  file: it computes
  `ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` — i.e. it
  assumes it lives one directory *below* repo root (`scripts/`) and climbs
  `..` to find `docker-compose.yaml`. Round 2's fix (fetch it to the
  install directory's own root, to dodge the `mkdir -p scripts`
  requirement) breaks exactly that assumption: from the install root,
  `dirname/..` climbs one level *too far*, landing outside the install
  directory entirely. Resolved by not fetching this script at all — the
  same precedent already applied to `start-application-services.sh`.
  `install.sh` needs only one line, `docker compose -f docker-compose.yaml down`,
  to stop the stack; a path-fragile wrapper script buys nothing for a
  directory that only ever holds the 3 fetched files. This also means
  `install.sh` fetches only 2 files now, not 3: `docker-compose.yaml` and
  `.env.example`.

## Fifth review round (one fix, confirmed with a portability nuance)

- **GHCR (and OCI generally) reject uppercase in image references.**
  Verified directly: `docker pull ghcr.io/AliceDev/x` fails immediately
  with `invalid reference format: repository name (...) must be
  lowercase`, before any network call. Since `IMAGE_NAMESPACE` derives
  from a GitHub owner/repo name, and GitHub handles can be mixed case
  (e.g. `PRANABraight`, already this fork's actual handle), both the
  publish workflow and `install.sh` must lowercase it before it reaches
  any `image:`/`docker pull` reference.
  - In `.github/workflows/publish-images.yml`: lowercase
    `github.repository_owner` with `${IMAGE_NAMESPACE,,}` into
    `$GITHUB_ENV` — fine here specifically because GitHub-hosted runners
    are Linux with bash 5+, where that expansion is supported.
  - In `install.sh`: **must not** use the same `${var,,}` syntax — verified
    directly that it's a hard syntax error (`bad substitution`) on this
    machine's bash 3.2 (macOS's shipped default), and `install.sh` has to
    run on whatever bash a user's machine has, unlike the GitHub Actions
    runner. Use the portable `tr '[:upper:]' '[:lower:]'` instead, which
    was verified to produce the same result on both bash versions. This
    mirrors the `jq`-avoidance reasoning from the third review round:
    default local tooling isn't a safe assumption for a script that has
    to run anywhere.

## Sixth review round (two fixes, one constraint confirmed already satisfied, one documented limitation)

- **Re-running `install.sh` to upgrade must not clobber existing secrets.**
  A user upgrading in place (same install directory, newer
  `VOICERA_VERSION`) would have `.env` blindly overwritten if install.sh's
  `.env` setup ever did an unconditional `cp .env.example .env` — that
  would wipe the real `SECRET_KEY`/`INTERNAL_API_KEY`/DB password already
  in use, breaking the running database's stored credentials against the
  freshly-generated ones. Rather than inventing new merge logic for this
  (as a straightforward `sed`-based fix would), reuse what already
  exists and is already exercised by the dev path: read
  `scripts/start-application-services.sh`'s `set_dotenv_value` /
  `dotenv_value` functions — they already do exactly this correctly
  (copy `.env.example` → `.env` only if `.env` is absent; update a key
  in place if present, append if not, via a temp-file rewrite). `install.sh`
  should lift this same pair of functions rather than re-deriving
  equivalent logic with `sed -i`, which also sidesteps a real portability
  wrinkle: BSD `sed` (macOS) and GNU `sed` (Linux) take the `-i` in-place
  flag differently in some forms, another entry in this spec's running
  list of "don't assume this machine's tools/shell are what the install
  target has."
- **Hardcoded fork identity defeats the point of forking.** Confirmed:
  this spec parameterizes `IMAGE_NAMESPACE` (which images get pulled) but
  never parameterizes *which GitHub repo `install.sh` itself fetches its
  own config files and resolves tags from* — those are two separate
  concerns and only one was addressed. As written, a fork maintainer who
  copies this same `install.sh` unmodified would still fetch
  `pranabraight`'s upstream config and tags, not their own. Fix:
  `install.sh` takes `GITHUB_OWNER`/`GITHUB_REPO` env vars (defaulting to
  this fork's own identity), used to build both the tags-API URL and the
  raw-content fetch URLs.
- **Checked and confirmed already satisfied: no interactive prompt in the
  `curl | bash` path.** The concern (a `read` call would consume the
  piped script as its own input under `curl | bash`, since stdin is the
  pipe) is valid in general, and `start-application-services.sh` already
  has exactly one `read -r -p` — but it's already correctly guarded with
  `[[ ! -t 0 ]]` (skip the prompt when stdin isn't a terminal), and more
  importantly, this spec's `install.sh` doesn't call that script or
  contain any `read` of its own at all. Recorded here as a constraint to
  hold going forward — if any future change adds an interactive prompt
  to `install.sh`, it must carry the same non-interactive guard — not as
  a bug in the current design.
- **Documented limitation, not fixed:** the GitHub tags API defaults to
  30 results per page (confirmed live: `torvalds/linux/tags` returns
  exactly 30 entries and a `Link` header pointing to 32 total pages), so
  `sort -V` only ever considers whatever lands on page 1. Once this
  repository accumulates more than 30 tags, an old hotfix tag pushed
  after a newer major version could in principle end up on page 1 while
  the true latest tag sits on page 2, invisible to this pipeline. Given
  the fork currently has zero tags, this is far from an immediate
  concern, and full pagination traversal is more complexity than
  justified for v1 — documented as a known limitation. Users who need
  correctness past that point should pin `VOICERA_VERSION` explicitly.

## Design

### 1. Image publishing (new: `.github/workflows/publish-images.yml`)

- Trigger: push of a tag matching `v*` (e.g. `v1.0.0`).
- Job-level permissions: `contents: read`, `packages: write`.
- Lowercases the namespace before use:
  `echo "IMAGE_NAMESPACE=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}" >> "$GITHUB_ENV"`
  — GHCR (and OCI generally) reject any uppercase in an image reference,
  and GitHub owner handles can be mixed case.
- Builds and pushes 3 images to `${IMAGE_NAMESPACE:-ghcr.io/pranabraight}/`:
  - `voicera-api:<tag>` (also re-tagged `:latest`) — from
    `apps/api/Dockerfile`, context `.`
  - `voicera-runtime:<tag>` (+ `:latest`) — from `apps/runtime/Dockerfile`,
    context `.`
  - `voicera-frontend:<tag>` (+ `:latest`) — from `frontend/Dockerfile`,
    context `./frontend`
- Auth: `GITHUB_TOKEN` (built-in, ghcr.io same-account, no new secret,
  given the explicit `packages: write` permission above).
- One-time manual step (not automatable via `GITHUB_TOKEN`): set each
  package's visibility to public in the fork's package settings, so
  `install.sh` users can pull without `docker login`.

### 2. `docker-compose.yaml` becomes the production baseline

- `api`, `arq-worker`, `campaign-orchestrator`: replace `build:` with
  `image: ${IMAGE_NAMESPACE:-ghcr.io/pranabraight}/voicera-api:${VOICERA_VERSION:-latest}`;
  drop their `volumes:` source bind-mounts; `api`'s `command:` drops
  `--reload`.
- `runtime`: `image: ${IMAGE_NAMESPACE:-ghcr.io/pranabraight}/voicera-runtime:${VOICERA_VERSION:-latest}`.
- `frontend`: `image: ${IMAGE_NAMESPACE:-ghcr.io/pranabraight}/voicera-frontend:${VOICERA_VERSION:-latest}`.
- Everything else (ports, env, healthchecks, depends_on, the 5
  already-prebuilt services) is unchanged.

### 3. `docker-compose.override.yaml` (new, dev-only)

Auto-loaded by Compose whenever someone runs `docker compose up` or
`make application-up` from a full checkout — no `-f` flag needed. Carries
only the dev diffs:

- `api`, `arq-worker`, `campaign-orchestrator`: `build:` pointing at
  `apps/api/Dockerfile` (context `.`), and the `volumes:` bind-mounts
  (`./apps/api:/app`, `./apps:/app/apps:ro`).
- `api`'s `command:` restores `--reload`.
- `runtime`: `build:` pointing at `apps/runtime/Dockerfile`.
- `frontend`: `build:` pointing at `frontend/Dockerfile`, context
  `./frontend`.

`make application-up` / bare `docker compose up --build` inside a checkout
keeps working exactly as today, **provided** `start-application-services.sh`
drops its hardcoded `-f docker-compose.yaml` (see point 5 below) — Compose
then merges the override in automatically. With that flag still in place,
the override would be silently ignored, defeating the entire point of this
file.

### 4. `install.sh` changes

- **Repo identity is parameterized, not hardcoded**: `GITHUB_OWNER`
  (default this fork's own owner) and `GITHUB_REPO` (default this fork's
  own repo name) env vars, used to build both the tags-API URL and the
  raw-content fetch URLs. Distinct from `IMAGE_NAMESPACE` (which images
  get pulled) — a fork maintainer who reuses this script unmodified needs
  both to point at their own fork, and only one was parameterized in
  earlier revisions of this spec.
- **Step order matters and must be exactly this**: (1) resolve version,
  (2) fetch files using that resolved version in the URL, (3) write
  secrets + version into `.env` (idempotently — see below), (4)
  `docker compose up -d`. The `install.sh` committed to disk today gets
  step 1 and 2 wrong — it hardcodes `BRANCH="${VOICERA_BRANCH:-dev-doc}"`
  and fetches from that branch, not a resolved release tag — confirming
  this ordering needs to be stated explicitly rather than assumed
  obvious.
- If `IMAGE_NAMESPACE` is user-supplied (not just the built-in default),
  lowercase it before writing to `.env` with
  `IMAGE_NAMESPACE="$(printf '%s' "$IMAGE_NAMESPACE" | tr '[:upper:]' '[:lower:]')"`
  — not bash's `${var,,}`, which was confirmed to be a hard syntax error
  on bash 3.2 (macOS's shipped default); `install.sh` can't assume bash 5,
  unlike the GitHub Actions runner used for the publish workflow.
- Resolves a version: honors `VOICERA_VERSION` if the caller set it;
  otherwise fetches `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/tags`
  and picks the highest `^v` tag by `sort -V`. Parsed with POSIX tools, not
  `jq` — verified directly that fresh `ubuntu:24.04` and `debian:12-slim`
  containers have no `jq` on PATH, and this script has to run on whatever
  bare VM a user points it at, unlike this development machine (which does
  have `jq`, masking the gap during local testing). Pipeline:
  `grep -o '"name": *"[^"]*"' | cut -d'"' -f4 | grep '^v' | sort -V | tail -1`
  — verified this produces the same result as the `jq` version against the
  live tags API. Not index `[0]`, since the API's ordering is by
  creation/commit-graph, not semver. Documented limitation (see sixth
  review round): this endpoint defaults to 30 results per page with no
  pagination traversal here, so on a repo with 30+ tags the true latest
  could be invisible to this pipeline — acceptable for v1, moot today
  since this fork has zero tags.
  If the tags API call itself fails or returns no matching tag (including
  when rate-limited — unauthenticated requests are capped at 60/hour per
  IP, confirmed via response headers, realistically only hit behind a
  shared NAT/CI loop rather than a single interactive install), fail with
  a clear message telling the user to set `VOICERA_VERSION` explicitly and
  retry, rather than silently proceeding with an empty version string or
  guessing a hardcoded fallback.
- Fetches from `raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/<resolved-tag>/...`
  — the resolved tag, never a branch name, so the fetched compose file
  always matches the schema the resolved image tags were built against.
  Only 2 files (not the original 4, and not the 3 from earlier revisions
  of this spec — see the fourth review round for why the stop script is
  dropped): `docker-compose.yaml`, `.env.example`.
  `start-application-services.sh` isn't needed since install.sh handles
  secret generation + `up` itself; `stop-application-services.sh` isn't
  fetched either — its `cd "$(dirname ...)/.."` logic assumes it lives
  in a `scripts/` subdirectory one level below repo root, which doesn't
  hold for a flat 2-file install directory, and a path-fragile wrapper
  buys nothing here anyway.
  **Must `export` the resolved value** before invoking `docker compose` —
  verified directly that an unexported shell variable is invisible to a
  child process, so `docker compose` silently falls back to the
  `${VOICERA_VERSION:-latest}` default in the compose file with no error
  at all.
  **`.env` setup must be idempotent, safe to re-run for an in-place
  upgrade**: lift the `dotenv_value`/`set_dotenv_value` functions
  verbatim from `scripts/start-application-services.sh` rather than
  writing new logic — they already copy `.env.example` → `.env` only
  when `.env` is absent, and already update-in-place-or-append a single
  key correctly. Use `set_dotenv_value VOICERA_VERSION "<resolved>"` (and
  likewise for `IMAGE_NAMESPACE` if user-supplied) instead of a blind
  `>>` append or a fresh `sed` one-liner — verified directly that
  `sed -i` differs in argument form between BSD sed (macOS) and GNU sed
  (Linux), one more machine-specific assumption this script can't make.
  This also protects the secrets `start-application-services.sh` itself
  writes (`SECRET_KEY`, etc.) from ever being clobbered by a naive
  `cp .env.example .env` on a second run.
  Verified separately that Compose reads `.env` in the working directory
  automatically, with no flags, so this `.env` write (on top of the
  `export`) is what keeps the deployment pinned to the resolved version
  across a server reboot or a manual `docker compose down && up -d` run
  later, when no shell `export` from the original install is still in
  scope. The `export` and the `.env` write serve different lifetimes —
  process-local now, durable-on-disk after — and both are needed.
- Runs `docker compose -f docker-compose.yaml up -d` directly — the
  explicit `-f` here is intentional and correct (unlike in
  `start-application-services.sh`): it deliberately excludes the
  (not-downloaded) override file, since the installer only ever has the
  prod baseline. To stop the stack later, the installer's printed
  instructions tell the user to run
  `docker compose -f docker-compose.yaml down` directly from the install
  directory — one line, no fetched wrapper script needed.
- No `--build`, no wrapper script invocation.

### 5. `scripts/start-application-services.sh` changes

Currently runs `docker compose -f docker-compose.yaml up --build -d`.
That explicit `-f` must be dropped — verified directly that Compose only
auto-loads `docker-compose.override.yaml` when invoked with zero `-f`
flags; keeping the flag (even just to name the one file that's already
the default) suppresses the override and silently reverts local dev back
to pulling `image:` refs with no hot-reload. New invocation:
`docker compose up --build -d` (bare, no `-f` at all).

## Testing

- `docker compose config` (no flags, from a full checkout) — confirm the
  override still restores `build:`/volumes/`--reload` and matches today's
  dev behavior.
- `docker compose -f docker-compose.yaml config` (override file
  explicitly excluded via `-f`) — confirm it resolves to `image:` refs
  with no bind-mounts, no YAML errors. This is the same invocation style
  `install.sh` uses, so this check doubles as its dry run.
- Tag a test release (e.g. `v0.0.1-test`) on the fork, confirm the Actions
  workflow builds and pushes 3 images with `packages: write` succeeding.
- Run `install.sh` end-to-end (unset `VOICERA_VERSION`, confirm it resolves
  the tag via `sort -V`) in a scratch directory, confirm all 10 containers
  come up healthy and the 5 URLs (frontend/API/docs/runtime/MinIO)
  respond.
- After that run, `cat .env` and confirm `VOICERA_VERSION=<resolved-tag>`
  is present; then `docker compose -f docker-compose.yaml down` followed
  by `docker compose -f docker-compose.yaml up -d` **in a fresh shell**
  (no exported `VOICERA_VERSION`) and confirm the same image tags come
  back up — this is the actual regression test for the reboot/Day-2
  scenario the fourth review round raised.
- Confirm `make application-up` / bare `docker compose up --build` still
  builds locally and hot-reloads, unchanged for local dev.
