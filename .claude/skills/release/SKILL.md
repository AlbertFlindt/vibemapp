---
name: release
description: Cut a Vibemapp release (version bump, tag, push, GitHub release). Use only when the owner asks for a release or a version bump.
---
Release Vibemapp version $ARGUMENTS (X.Y.Z).

1. Run `./check.sh`; stop if it fails.
2. Bump the version in:
   - `meson.build` (`version`)
   - `packaging/arch/PKGBUILD` (`pkgver`, reset `pkgrel=1`)
   - `src/vibemapp/ui/resources.py` (dev fallback version)
   - `data/io.github.albertflindt.Vibemapp.metainfo.xml`: add a `<release>` entry, and point every
     screenshot URL at the new tag (`.../vibemapp/vX.Y.Z/data/screenshots/`)
   - `CHANGELOG.md`: new section for this version
3. Update the release line in `PROGRESS.md`.
4. Run `./check.sh` again, then commit ("Release X.Y.Z: <summary>"), `git tag vX.Y.Z`,
   `git push origin main --tags`.
5. Write that version's CHANGELOG section to `NOTES.md` in the scratchpad (not the repo) and run
   `gh release create vX.Y.Z --title "Vibemapp X.Y.Z" --notes-file NOTES.md`.
6. Install is the owner's step: `cd packaging/arch && makepkg -si`, then replug the keyboard.
