"""Focus sources for Smart Layers."""

from __future__ import annotations

import os

from .base import FocusWatcher, NullFocusWatcher
from .hyprland import HyprlandFocusWatcher, find_hyprland_instance


def create_focus_watcher(runtime_dir: str | None = None,
                         environ: dict[str, str] | None = None) -> FocusWatcher:
    """Pick the best available focus source.

    We use Hyprland if an instance socket exists or we're inside a Hyprland
    session. Otherwise we still return a Hyprland watcher when
    $XDG_RUNTIME_DIR/hypr exists, since it will reconnect once Hyprland
    starts, and fall back to a no-op watcher if not.
    """
    env = os.environ if environ is None else environ
    runtime = runtime_dir or env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if (find_hyprland_instance(runtime, env.get("HYPRLAND_INSTANCE_SIGNATURE"))
            or env.get("HYPRLAND_INSTANCE_SIGNATURE")
            or os.path.isdir(os.path.join(runtime, "hypr"))):
        return HyprlandFocusWatcher(runtime_dir=runtime,
                                    signature=env.get("HYPRLAND_INSTANCE_SIGNATURE"))
    return NullFocusWatcher()


__all__ = ["FocusWatcher", "NullFocusWatcher", "HyprlandFocusWatcher",
           "create_focus_watcher", "find_hyprland_instance"]
