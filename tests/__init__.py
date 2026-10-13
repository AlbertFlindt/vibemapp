# Load gtk4-layer-shell before any test module imports GTK, like the app does.
import vibemapp._preload  # noqa: F401
