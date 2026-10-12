"""Application ID selection (no GTK imports, shared by backend and UI).

The installed app uses ``io.github.albertflindt.Vibemapp``. Runs from the
source tree (``run-dev.sh`` sets ``VIBEMAPP_DEVEL=1``) use the GNOME-style
``io.github.albertflindt.Vibemapp.Devel`` instead, so they get their own
D-Bus name and single-instance slot and don't just raise the installed
window.

Only the *application* ID changes. The GSettings schema ID, icon name and
the config/cache directories stay the same, so a dev run sees the user's
real rules, layout cache and preferences.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BASE_APP_ID = "io.github.albertflindt.Vibemapp"
DEVEL_APP_ID = f"{BASE_APP_ID}.Devel"
DEVEL_ENV = "VIBEMAPP_DEVEL"

# Window classes (Hyprland reports the GTK app ID) that belong to Vibemapp
# itself, installed or dev. Focusing either never triggers Smart Layers.
OWN_APP_CLASSES = frozenset({BASE_APP_ID, DEVEL_APP_ID})


def is_devel(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(DEVEL_ENV) == "1"


def application_id(environ: Mapping[str, str] | None = None) -> str:
    return DEVEL_APP_ID if is_devel(environ) else BASE_APP_ID


def is_own_app_class(app_class: str | None) -> bool:
    return app_class in OWN_APP_CLASSES


__all__ = ["BASE_APP_ID", "DEVEL_APP_ID", "DEVEL_ENV", "OWN_APP_CLASSES",
           "application_id", "is_devel", "is_own_app_class"]
