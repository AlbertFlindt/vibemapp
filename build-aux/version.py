#!/usr/bin/env python3
"""Release version helper. main carries the next version; every merge that changes the app
publishes a pre-release of it, and a release PR dates it as a stable release.

  version.py get          print the version in meson.build (the one being worked on)
  version.py stable       print the latest stable version (the newest dated CHANGELOG section)
  version.py state        print "unreleased" or "stable" for the current version
  version.py check        every file names the right version (run by check.sh)
  version.py bump X.Y.Z   work towards X.Y.Z: set meson.build and resources.py, and name the
                          top "(unreleased)" CHANGELOG section X.Y.Z (or add one)
  version.py release      date the current version's CHANGELOG section and set the files that
                          name the stable version; the metainfo <release> entry is by hand
  version.py notes        print the current version's CHANGELOG section (release notes)
"""
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METAINFO = "data/io.github.albertflindt.Vibemapp.metainfo.xml"
SEMVER = r"\d+\.\d+\.\d+"
SECTION = rf"^## ({SEMVER}) \((unreleased|\d{{4}}-\d{{2}}-\d{{2}})\)$"

# file -> (pattern with one version group, replacement template)
WORKING = {
    "meson.build": (rf"^(  version: ')({SEMVER})(',)", r"\g<1>{v}\g<3>"),
    "src/vibemapp/ui/resources.py": (rf'(or ")({SEMVER})(-dev")', r"\g<1>{v}\g<3>"),
}
# These name the latest stable release: the PKGBUILD builds its tag.
STABLE = {
    "packaging/arch/PKGBUILD": (rf"^(pkgver=)({SEMVER})()", r"\g<1>{v}\g<3>"),
    "PROGRESS.md": (rf"(\*\*Latest release:\*\* )({SEMVER})()", r"\g<1>{v}\g<3>"),
}
SCREENSHOTS = rf"(vibemapp/v)({SEMVER})(/data/screenshots/)"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel, text):
    (ROOT / rel).write_text(text, encoding="utf-8")


def get():
    return re.search(WORKING["meson.build"][0], read("meson.build"), re.M).group(2)


def sections():
    """(version, "unreleased" or date) for every CHANGELOG section, newest first."""
    return re.findall(SECTION, read("CHANGELOG.md"), re.M)


def state(version=None):
    found = dict(sections()).get(version or get())
    return None if found is None else "unreleased" if found == "unreleased" else "stable"


def stable():
    return next((v for v, when in sections() if when != "unreleased"), None)


def notes(version):
    m = re.search(rf"^## {re.escape(version)} \(.*?\)\n(.*?)(?=^## |\Z)",
                  read("CHANGELOG.md"), re.M | re.S)
    return m.group(1).strip() if m else None


def check():
    v = get()
    errors = []
    for rel, (pattern, _) in WORKING.items():
        found = re.search(pattern, read(rel), re.M)
        if not found or found.group(2) != v:
            errors.append(f"{rel}: expected {v}, found {found.group(2) if found else 'nothing'}")
    if not state(v) or not notes(v):
        errors.append(f"CHANGELOG.md: no '## {v} (unreleased)' or '## {v} (YYYY-MM-DD)' "
                      "section with entries")
    s = stable()
    for rel, (pattern, _) in STABLE.items():
        found = re.search(pattern, read(rel), re.M)
        if not found or found.group(2) != s:
            errors.append(f"{rel}: expected the latest stable {s}, "
                          f"found {found.group(2) if found else 'nothing'}")
    meta = read(METAINFO)
    first = re.search(rf'<release version="({SEMVER})"', meta)
    if not first or first.group(1) != s:
        errors.append(f"{METAINFO}: the first <release> must be the latest stable {s}")
    stale = {m.group(2) for m in re.finditer(SCREENSHOTS, meta)} - {s}
    if stale:
        errors.append(f"{METAINFO}: screenshot URLs point at v{', v'.join(sorted(stale))}")
    for e in errors:
        print(f"    {e}", file=sys.stderr)
    return not errors


def set_files(files, v):
    for rel, (pattern, repl) in files.items():
        write(rel, re.sub(pattern, repl.format(v=v), read(rel), count=1, flags=re.M))


def bump(v):
    if not re.fullmatch(SEMVER, v):
        sys.exit(f"not a version: {v}")
    set_files(WORKING, v)
    if state(v) is None:
        log = read("CHANGELOG.md")
        top = re.search(SECTION, log, re.M)
        if top and top.group(2) == "unreleased":
            log = log[:top.start(1)] + v + log[top.end(1):]
        else:
            at = top.start() if top else len(log)
            log = f"{log[:at]}## {v} (unreleased)\n\n{log[at:]}"
        write("CHANGELOG.md", log)
    print(f"Working towards {v}. Add the entries under '## {v} (unreleased)' in CHANGELOG.md.")


def release():
    v = get()
    if state(v) != "unreleased":
        sys.exit(f"{v} has no '(unreleased)' CHANGELOG section to release")
    today = datetime.date.today().isoformat()
    write("CHANGELOG.md",
          read("CHANGELOG.md").replace(f"## {v} (unreleased)", f"## {v} ({today})", 1))
    set_files(STABLE, v)
    write("packaging/arch/PKGBUILD",
          re.sub(r"^pkgrel=.*$", "pkgrel=1", read("packaging/arch/PKGBUILD"), flags=re.M))
    write(METAINFO, re.sub(SCREENSHOTS, rf"\g<1>{v}\g<3>", read(METAINFO)))
    print(f"Dated {v} as {today}. Now add the metainfo <release> entry and run check.")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "get":
        print(get())
    elif cmd == "stable":
        print(stable())
    elif cmd == "state":
        print(state() or "missing")
    elif cmd == "check":
        return 0 if check() else 1
    elif cmd == "bump" and len(argv) == 3:
        bump(argv[2])
    elif cmd == "release":
        release()
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
