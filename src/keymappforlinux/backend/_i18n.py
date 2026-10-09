"""gettext helper for backend user-visible strings (domain ``keymappforlinux``).

Kept separate from ``ui.i18n`` so the backend never imports UI modules.
"""

from __future__ import annotations

import gettext
import os

DOMAIN = "keymappforlinux"

_localedir = None
_pkgdatadir = os.environ.get("KEYMAPP_PKGDATADIR")
if _pkgdatadir:
    _localedir = os.path.join(os.path.dirname(_pkgdatadir.rstrip("/")), "locale")

_translation = gettext.translation(DOMAIN, localedir=_localedir, fallback=True)
_ = _translation.gettext

__all__ = ["_", "DOMAIN"]
