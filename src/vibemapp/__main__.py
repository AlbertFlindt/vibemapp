"""``python -m vibemapp``."""

from __future__ import annotations

import sys

from . import _preload  # noqa: F401  (gtk4-layer-shell must load before GTK)
from .main import main

sys.exit(main(sys.argv))
