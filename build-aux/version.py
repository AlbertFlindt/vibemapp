#!/usr/bin/env python3
"""Release version helper. Every merge to main that changes the app ships as a release.

  version.py get          print the version in meson.build
  version.py check        every file names the same version (run by check.sh)
  version.py bump X.Y.Z   set the version in the mechanical places; CHANGELOG.md and the
                          metainfo <release> entry are written by hand, then run check
  version.py notes        print the current version's CHANGELOG section (release notes)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METAINFO = "data/io.github.albertflindt.Vibemapp.metainfo.xml"
SEMVER = r"\d+\.\d+\.\d+"

# file -> (pattern with one version group, replacement template)
MECHANICAL = {
    "meson.build": (rf"^(  version: ')({SEMVER})(',)", r"\g<1>{v}\g<3>"),
    "packaging/arch/PKGBUILD": (rf"^(pkgver=)({SEMVER})()", r"\g<1>{v}\g<3>"),
    "src/vibemapp/ui/resources.py": (rf'(or ")({SEMVER})(-dev")', r"\g<1>{v}\g<3>"),
    "PROGRESS.md": (rf"(\*\*Latest release:\*\* )({SEMVER})()", r"\g<1>{v}\g<3>"),
}
SCREENSHOTS = rf"(vibemapp/v)({SEMVER})(/data/screenshots/)"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def get():
    return re.search(MECHANICAL["meson.build"][0], read("meson.build"), re.M).group(2)


def notes(version):
    m = re.search(rf"^## {re.escape(version)} \(.*?\)\n(.*?)(?=^## |\Z)",
                  read("CHANGELOG.md"), re.M | re.S)
    return m.group(1).strip() if m else None


def check():
    v = get()
    errors = []
    for rel, (pattern, _) in MECHANICAL.items():
        found = re.search(pattern, read(rel), re.M)
        if not found or found.group(2) != v:
            errors.append(f"{rel}: expected {v}, found {found.group(2) if found else 'nothing'}")
    meta = read(METAINFO)
    first = re.search(rf'<release version="({SEMVER})"', meta)
    if not first or first.group(1) != v:
        errors.append(f"{METAINFO}: the first <release> must be {v}")
    stale = {m.group(2) for m in re.finditer(SCREENSHOTS, meta)} - {v}
    if stale:
        errors.append(f"{METAINFO}: screenshot URLs point at v{', v'.join(sorted(stale))}")
    if not notes(v):
        errors.append(f"CHANGELOG.md: no '## {v} (date)' section")
    for e in errors:
        print(f"    {e}", file=sys.stderr)
    return not errors


def bump(v):
    if not re.fullmatch(SEMVER, v):
        sys.exit(f"not a version: {v}")
    for rel, (pattern, repl) in MECHANICAL.items():
        path = ROOT / rel
        path.write_text(re.sub(pattern, repl.format(v=v), read(rel), count=1, flags=re.M),
                        encoding="utf-8")
    pkgbuild = ROOT / "packaging/arch/PKGBUILD"
    pkgbuild.write_text(re.sub(r"^pkgrel=.*$", "pkgrel=1", read("packaging/arch/PKGBUILD"),
                               flags=re.M), encoding="utf-8")
    (ROOT / METAINFO).write_text(re.sub(SCREENSHOTS, rf"\g<1>{v}\g<3>", read(METAINFO)),
                                 encoding="utf-8")
    print(f"Set {v}. Now add the CHANGELOG.md section and the metainfo <release> entry.")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "get":
        print(get())
    elif cmd == "check":
        return 0 if check() else 1
    elif cmd == "bump" and len(argv) == 3:
        bump(argv[2])
    elif cmd == "notes":
        text = notes(get())
        if not text:
            sys.exit(f"CHANGELOG.md has no section for {get()}")
        print(text)
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
