"""gettext setup (domain ``keymappforlinux``). No GTK imports."""

from __future__ import annotations

import gettext
import os

DOMAIN = "keymappforlinux"

_localedir = None
_pkgdatadir = os.environ.get("KEYMAPP_PKGDATADIR")
if _pkgdatadir:
    # $prefix/share/keymappforlinux -> $prefix/share/locale
    _localedir = os.path.join(os.path.dirname(_pkgdatadir.rstrip("/")), "locale")

_translation = gettext.translation(DOMAIN, localedir=_localedir, fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext

__all__ = ["_", "ngettext", "DOMAIN"]
