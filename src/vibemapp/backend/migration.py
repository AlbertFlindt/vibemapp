"""One-time migration of KeyMapp for Linux data to Vibemapp (SPEC 6.1).

``migrate_legacy_dirs()`` moves

* ``$XDG_CONFIG_HOME/keymappforlinux/`` → ``$XDG_CONFIG_HOME/vibemapp/``
* ``$XDG_CACHE_HOME/keymappforlinux/``  → ``$XDG_CACHE_HOME/vibemapp/``

Rules:

* If the new directory doesn't exist, the old one is renamed (or moved, if
  it's on another filesystem).
* If the new directory already exists (for example the UI created
  ``~/.cache/vibemapp/schemas`` first), only the old top-level entries that
  are missing from the new directory are moved. Entries with the same name
  are left in the old directory, so nothing is overwritten.
* The old directory is removed only if it ends up empty.
* Errors are logged and never raised.

It's idempotent and cheap once the old directories are gone, so it's safe
to call on every start. Call it as early as possible, before anything
creates ``~/.cache/vibemapp`` or ``~/.config/vibemapp``.
``ZsaKeyboardService.__init__`` calls it too.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

OLD_NAME = "keymappforlinux"
NEW_NAME = "vibemapp"


@dataclass
class MigrationResult:
    moved: list[tuple[str, str]] = field(default_factory=list)     # (src, dst)
    skipped: list[str] = field(default_factory=list)               # conflicting paths left in place
    errors: list[str] = field(default_factory=list)


def _xdg(env: dict[str, str], var: str, fallback: str) -> str:
    value = env.get(var)
    if value and os.path.isabs(value):
        return value
    return os.path.join(env.get("HOME") or os.path.expanduser("~"), fallback)


def _move(src: str, dst: str) -> None:
    if os.path.lexists(dst):
        raise FileExistsError(dst)
    try:
        os.rename(src, dst)
    except OSError:
        # e.g. EXDEV: copy then delete. dst doesn't exist, so nothing is overwritten.
        shutil.move(src, dst)


def migrate_dir(old: str, new: str, result: MigrationResult | None = None) -> MigrationResult:
    result = result if result is not None else MigrationResult()
    try:
        if not os.path.isdir(old) or os.path.islink(old):
            return result
        if not os.path.lexists(new):
            os.makedirs(os.path.dirname(new), exist_ok=True)
            _move(old, new)
            result.moved.append((old, new))
            log.info("Migrated %s -> %s", old, new)
            return result
        if not os.path.isdir(new):
            result.skipped.append(old)
            return result
        for name in sorted(os.listdir(old)):
            src, dst = os.path.join(old, name), os.path.join(new, name)
            if os.path.lexists(dst):
                result.skipped.append(src)
                continue
            try:
                _move(src, dst)
                result.moved.append((src, dst))
            except OSError as exc:
                result.errors.append(f"{src}: {exc}")
        try:
            os.rmdir(old)        # only succeeds if empty
        except OSError:
            pass
    except OSError as exc:
        result.errors.append(f"{old}: {exc}")
        log.warning("Migration of %s failed: %s", old, exc)
    return result


def migrate_legacy_dirs(environ: dict[str, str] | None = None) -> MigrationResult:
    """Migrate config and cache dirs. Never raises."""
    env = dict(os.environ) if environ is None else environ
    result = MigrationResult()
    try:
        for var, fallback in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache")):
            base = _xdg(env, var, fallback)
            migrate_dir(os.path.join(base, OLD_NAME), os.path.join(base, NEW_NAME), result)
    except Exception as exc:   # noqa: BLE001 - never fatal
        log.exception("legacy migration failed")
        result.errors.append(str(exc))
    return result


__all__ = ["migrate_legacy_dirs", "migrate_dir", "MigrationResult"]
