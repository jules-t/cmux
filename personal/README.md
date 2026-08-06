# cmux Personal local release pipeline

cmux Personal uses GitHub as source and release storage. Its personal release
pipeline does not use GitHub Actions.

## What runs where

| Responsibility | Location |
| --- | --- |
| Store the fork, personal branches, candidate audit refs, tags, and releases | GitHub |
| Monitor official stable releases and fully published upstream main/nightly revisions | This Mac |
| Rebase personal commits and enforce the conflict policy | This Mac |
| Resolve eligible conflicts with Pi and DeepSeek in a Docker sandbox | This Mac |
| Independently review every automatic conflict resolution | This Mac |
| Build, run unit tests, smoke-launch, package, and install | This Mac |
| Publish the exact installed and validated archive | This Mac |

The scheduled monitor prepares candidates but never publishes a release. A
release starts only when `cmux-personal-publish` is given the receipt from a
successful local build that is still installed on the machine.

## Prerequisites

- Apple Silicon Mac
- Xcode 26.x installed under `/Applications` and opened once
- GitHub CLI authenticated with `gh auth login`
- Docker Desktop, OrbStack, or another running Docker-compatible engine
- A DeepSeek API key stored in macOS Keychain

Store the DeepSeek key without placing it in a shell profile:

```sh
personal/ci/local_sync.sh --configure-deepseek
```

Install the commands and the two user LaunchAgents:

```sh
uv run --no-project python personal/setup_local.py \
  --control-root "$PWD" \
  --config personal/config.json
```

The monitor checks upstream every six hours. The updater continues checking for
published personal releases and completes a staged install after the app closes.
The scheduled monitor runs from a self-contained copy under
`~/.local/share/cmux-personal/monitor-control` so macOS does not block it from
reading a repository under `Documents`. Rerun the setup command after changing
the local control code.

## Daily use

Check the complete local toolchain:

```sh
cmux-personal-build --check
```

Manually check upstream and prepare a candidate. Eligible conflicts are
resolved and independently reviewed automatically:

```sh
cmux-personal-sync
```

Build the best current candidate, run the release checks, and install it:

```sh
cmux-personal-build
```

Use the installed app for as long as needed. A failed build or an app you do not
like has not created a GitHub release and has not moved `personal/stable`.

When that exact installed build is ready, publish its receipt:

```sh
cmux-personal-publish /absolute/path/to/validation-receipt.json
```

Use `--channel stable` or `--channel main` to select one channel. Use
`--validate-only` to build without installing; that receipt cannot be published
until the exact build is installed.

## Safety properties

- Candidate work happens in `~/.local/share/cmux-personal`, not in the active
  source checkout.
- A clean rebase passes deterministic history and test-preservation checks.
- A conflicted rebase must pass the conflict policy, the DeepSeek resolver,
  deterministic verification, and a separate read-only DeepSeek review.
- Build receipts bind the candidate Git bundle, archive, manifest, runtime
  identity, hashes, and personal source lease.
- Publication refuses a receipt that is not the exact installed build or whose
  source branch moved after validation.
- Publication uses a private draft, verifies the uploaded asset digests, exposes
  it, then moves `personal/stable` with an exact force-with-lease.
- Failed publication is retryable with the same receipt.

Official cmux runtime manifests still require upstream GitHub provenance
attestations. Personal archives built after this migration declare
`build_origin: local`; their trust boundary is the validated local receipt plus
the authenticated personal GitHub repository used only for storage. Older
personal releases retain their historical GitHub Actions attestation check.
