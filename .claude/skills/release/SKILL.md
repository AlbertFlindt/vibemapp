---
name: release
description: Bump the Vibemapp version for a release (every PR that changes the app ships as one on merge). Use when preparing a PR for merge, or when the owner asks for a release or a version bump.
---
Every merge to main that changes the app is a release. The version bump goes **in the PR**,
as its last commit; on merge, `.github/workflows/release.yml` tags `vX.Y.Z` and publishes the
GitHub release from the CHANGELOG section. CI's "Version bumped" check fails a PR that changes
the app without a bump (docs, CI and `.claude/` changes alone are exempt).

Release version $ARGUMENTS (X.Y.Z). Without one, pick it from the changes since the last tag:
patch for fixes, minor for new features, major for breaking changes.

1. On the PR branch: `python3 build-aux/version.py bump X.Y.Z`. It sets `meson.build`, the
   PKGBUILD (`pkgrel=1`), the dev fallback in `ui/resources.py`, the metainfo screenshot URLs
   and the release line in `PROGRESS.md`; update its "Last updated" date too.
2. Write by hand:
   - `CHANGELOG.md`: a `## X.Y.Z (YYYY-MM-DD)` section (user-facing, Added/Changed/Fixed).
   - `data/io.github.albertflindt.Vibemapp.metainfo.xml`: a `<release>` entry first in
     `<releases>`, one short paragraph.
3. `./check.sh` (it runs `build-aux/version.py check`), commit "Release X.Y.Z: <summary>", push.
4. After the merge, confirm the release appeared (`gh release view vX.Y.Z`). If the workflow
   failed, release by hand on main: `git tag vX.Y.Z && git push origin vX.Y.Z`, then
   `gh release create vX.Y.Z --title "Vibemapp X.Y.Z" --notes-file <(python3 build-aux/version.py notes)`.
5. Install is the owner's step: `cd packaging/arch && makepkg -si`, then replug the keyboard.
