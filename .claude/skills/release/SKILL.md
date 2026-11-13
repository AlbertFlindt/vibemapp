---
name: release
description: Set the Vibemapp version in a PR (every merge that changes the app publishes a pre-release), and make a stable release when asked. Use when preparing a PR for merge, or when asked for a release, a version bump or whether it's time to release.
---
`main` carries the version being worked towards, with a `## X.Y.Z (unreleased)` section at the
top of `CHANGELOG.md`. On every merge that changes the app, `.github/workflows/release.yml`
publishes the pre-release `vX.Y.Z-pre.N` (GitHub marks it pre-release; "latest" stays stable).
A stable release is a release PR that dates the section. CI's "Version and changelog" check fails
a PR that changes the app without a version above the latest stable one and a CHANGELOG entry.

## Versions
- **Patch** (1.9.0 → 1.9.1): fixes and small changes.
- **Minor** (1.9.x → 1.10.0): a new feature.
- **Major** (1.x → 2.0.0): a milestone in `ROADMAP.md` is done. Never for anything else.

## In every PR that changes the app
1. If the top CHANGELOG section is dated (the last version is out), start the next one:
   `python3 build-aux/version.py bump X.Y.Z` with the patch or minor above it. If it's
   "(unreleased)" and this PR adds a feature to a patch version, bump it to the minor; the
   script renames the section. Otherwise leave the version alone.
2. Add one user-facing bullet under Added, Changed or Fixed in that section.
3. `./check.sh`, commit, push. No "Release" commit.

## Stable release (only when asked)
When asked whether it's time, check these and say which don't hold:
- CI is green on `main`, and the latest pre-release has been used on the Voyager
  for **3 days** with no new bug. A fix for a bug that hurts daily use may go as soon as it's
  verified on the keyboard.
- Every entry in the section is verified, or listed under "Not verified" in `PROGRESS.md`.
- No feature in the section is half done.
- For a major: every item of its milestone in `ROADMAP.md` is checked.

Then, on a release branch:
1. `python3 build-aux/version.py release`: dates the section and sets the PKGBUILD (`pkgrel=1`),
   the metainfo screenshot URLs and "Latest release" in `PROGRESS.md`.
2. By hand: a `<release>` entry first in `<releases>` in
   `data/io.github.albertflindt.Vibemapp.metainfo.xml`, one short paragraph.
3. Prune `PROGRESS.md`: delete what this release made true or obsolete (shipped next steps,
   fixed issues, items now verified), and move a decision that's just how the code works to
   `docs/SPEC.md`. In `ROADMAP.md`, check the shipped items; drop a finished milestone.
4. `./check.sh`, commit "Release X.Y.Z: <summary>", open the PR.
5. After the merge, confirm it appeared (`mcp__github__get_release_by_tag`, or
   `gh release view vX.Y.Z`). If the workflow failed,
   rerun it from the Actions tab.
6. Install: `cd packaging/arch && makepkg -si`, then replug the keyboard. The PKGBUILD builds
   stable tags only; to use a pre-release day to day, quit the installed app and run
   `./run-dev.sh` on `main`.
