# Vibemapp — v1 specification

## 1. Goal
A native, GNOME-styled Linux replacement for ZSA Keymapp, for ZSA keyboards
(Voyager, Moonlander, ErgoDox EZ, Planck EZ) running Oryx firmware.

### In scope (v1)
1. **Smart Layers** — per-app layer rules. When the focused window changes, the
   keyboard is switched to the layer assigned to that app; otherwise to a fallback layer.
2. **Layout viewer** — show the connected keyboard's Oryx layout per layer, follow the
   keyboard's active layer live, highlight key presses live.
3. **Device handling** — detection, hotplug, firmware/layout info, Oryx availability.
4. **Packaging** — udev rules (uaccess), Meson install, PKGBUILD.

### Out of scope (v1)
Firmware flashing, layout editing, GNOME/KDE/X11 focus sources (design must allow them
later), Keymapp's gRPC API.

## 2. Architecture

```
 Hyprland .socket2.sock ──► HyprlandFocusWatcher ──focus──► SmartLayersEngine ──set-layer──► OryxConnection ──► /dev/hidrawN
                                                               ▲ rules (JSON per layout)          │ LAYER/KEYDOWN/KEYUP events
 sysfs / hidraw hotplug ──► DeviceMonitor ──────────────────┐   │                                  ▼
 Oryx GraphQL / cache ──► LayoutRepository ──────────────► ZsaKeyboardService (KeyboardService) ──signals──► UI
```

The contract is `src/vibemapp/models.py` + `src/vibemapp/service.py`.

## 3. Backend notes

### 3.1 Device enumeration
- Walk `/sys/class/hidraw/*/device/uevent`; match `HID_ID` vendor `00003297` (ZSA) and
  legacy ErgoDox EZ `0000FEED`. Map product IDs to `KeyboardModel` (Voyager is 0x1977 —
  verify the others against ZSA's QMK fork / official udev rules).
- A ZSA keyboard exposes several hidraw nodes. Choose the raw-HID one by parsing
  `/sys/class/hidraw/hidrawN/device/report_descriptor` for Usage Page 0xFF60, Usage 0x61.
- Hotplug: monitor `/dev` for `hidraw*` (Gio.FileMonitor) with a polling fallback.
- On this dev machine: Voyager at `/dev/hidraw9`–`13`; `hidraw10` user-writable (uaccess).

### 3.2 Oryx raw-HID protocol
Source of truth: ZSA's QMK fork, `keyboards/zsa/common/oryx.h` / `oryx.c`
(github.com/zsa/qmk_firmware, current firmware branch). **Verify every command/event
ID and payload layout from that source**; do not rely on memory. Things to confirm:
- Report size (32 bytes) and whether a leading report ID byte is needed when writing to hidraw.
- Pairing / handshake requirements on current firmware (older firmware used pairing
  commands; newer uses a simpler init). Must not trigger anything persistent.
- `GET_FW_VERSION` response → string containing layout id and revision id.
- Set-layer command semantics (layer_move vs layer_on/off). Smart Layers needs
  "make layer N the active layer" and "return to base".
- Events: layer change, key down/up (row/col → Oryx key index mapping per model).
- Behaviour when the host app disconnects (does the keyboard keep the layer?).

### 3.3 Layout fetch
- Oryx GraphQL endpoint `https://oryx.zsa.io/graphql`, query layout by hash id +
  geometry + revision (verify the query shape; ZSA's open-source `kontroll` and
  community projects are useful references).
- Map QMK/Oryx keycodes to short labels/glyphs; tap + hold labels; layer titles/colours.
- Cache JSON at `$XDG_CACHE_HOME/vibemapp/layouts/<layout>-<revision>.json`;
  use the cache when offline.

### 3.4 Focus source (Hyprland)
- Socket dir: `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/`. If the env var is
  missing (it was in our dev shell), glob `$XDG_RUNTIME_DIR/hypr/*/.socket2.sock`
  and pick the newest live one.
- Events socket `.socket2.sock`: lines like `activewindow>>kitty,Some title`
  (title may contain commas — split on the first comma only). Also handle
  `activewindowv2>>address`, `closewindow>>`, empty class (no focused window).
- Command socket `.socket.sock`: send `j/clients` to list windows (JSON),
  `j/activewindow` for the initial state.
- Reconnect with backoff if Hyprland restarts.
- `FocusWatcher` base class so GNOME/KDE/X11 implementations can be added later.

### 3.5 Smart Layers engine
- Rules per layout id in `$XDG_CONFIG_HOME/vibemapp/smart-layers.json`:
  `{"version":1,"layouts":{"<layout_id>":{"fallback":0,"rules":[...AppRule...]}}}`.
  Atomic write (temp file + rename).
- First enabled matching rule wins (exact class, optional title regex via `re.search`).
- No match → fallback layer (`None` = leave alone). Only send set-layer if the target
  differs from the last layer *we* sent or from the keyboard's reported layer.
- Debounce rapid focus flips (~50 ms) to avoid spamming the keyboard.
- Ignore focus events for our own window (class `io.github.albertflindt.Vibemapp`),
  so opening the app to edit rules doesn't reset the layer.
- Paused when disabled or disconnected; re-evaluate the current focus on enable/connect.

## 4. UX (GNOME HIG)
- `Adw.ApplicationWindow` → `Adw.NavigationSplitView`; sidebar: **Layout**, **Smart Layers**.
  Header bar: device menu and primary menu (Preferences, Keyboard Shortcuts, About).
- **No keyboard**: `Adw.StatusPage` "Connect Your Keyboard", with a udev-rules hint if
  a ZSA device was found but couldn't be opened.
- **Layout page**: custom `KeyboardView` (GtkSnapshot), keys as rounded rects using
  libadwaita colours (dark/light, accent for pressed and current layer); tap label
  centred, hold label small at the bottom. Layer selector (linked toggle buttons or
  dropdown) follows `layer-changed` with a crossfade; clicking a layer only changes the
  view (with an option to request that layer on the keyboard).
- **Smart Layers page**: `Adw.SwitchRow` master toggle; status row
  "Focused: kitty → Layer 1"; rules group (icon, app name, layer `Adw.ComboRow`,
  enable switch, remove button); "Add Rule" → `Adw.Dialog` choosing from running apps or
  installed apps (`Gio.AppInfo`; class from `StartupWMClass` or desktop id) with an
  optional title-pattern entry; fallback layer combo (includes "Don't change").
  `Adw.Banner` when Hyprland isn't detected.
- Background: closing the window keeps the app running when "Run in background" is on
  (`Gio.Application.hold`); `--background` starts without a window. Preferences show
  the Hyprland Lua autostart hint
  (`hl.on("hyprland.start", function () hl.exec_cmd("vibemapp --background") end)`).
- Preferences (`Adw.PreferencesDialog`): run in background, enable Smart Layers on
  startup, auto-connect. Stored in GSettings.
- Errors as `Adw.Toast`. About via `Adw.AboutDialog`.

## 5. Packaging
- `meson.build` installs the package to `$datadir/vibemapp/`, a launcher
  `$bindir/vibemapp`, desktop file, metainfo, icons, gschema (compiled), udev
  rules to `/usr/lib/udev/rules.d/50-zsa.rules`.
- `packaging/arch/PKGBUILD`: `depends=(python python-gobject gtk4 libadwaita)`, `makedepends=(meson)`.

## 6. Release 0.2 additions

### 6.1 Rename to Vibemapp
App ID `io.github.albertflindt.Vibemapp`, command/package/gettext domain `vibemapp`,
config `$XDG_CONFIG_HOME/vibemapp/`, cache `$XDG_CACHE_HOME/vibemapp/`, data
`$XDG_DATA_HOME/vibemapp/`, env vars `VIBEMAPP_*`. One-time migration on first start:
- Backend: move `~/.config/keymappforlinux/` → `~/.config/vibemapp/` and
  `~/.cache/keymappforlinux/` → `~/.cache/vibemapp/` if the new dirs don't exist.
- Frontend: copy GSettings values from the old dconf path
  `/io/github/albertflindt/KeyMappForLinux/` once, guarded by the
  `migrated-from-keymappforlinux` key. (Opening our fixed-path schema at another path
  aborts GIO, so the app compiles a relocatable copy of the schema into a temp dir and
  reads the old path through it.)
- Backend: if a new dir already exists, only missing entries are moved across.

### 6.2 Tray (StatusNotifierItem)
Hyprland has no minimise; "minimise to tray" means: when the tray is available and
"Show in tray" is on, closing the window hides it to a tray icon instead of quitting;
`--background` starts in the tray. Implemented as a StatusNotifierItem
(`org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`) over Gio D-Bus — the user's
shell (Noctalia) runs `org.kde.StatusNotifierWatcher`. Left click toggles the window.
Menu: Show Vibemapp · Smart Layers (checkbox) · current layer (disabled label) ·
Quit. Tooltip shows device + active layer. Re-registers if the watcher restarts.

### 6.3 Key layout tooltips
Per-key tooltips must follow the pointer: set `Gtk.Tooltip.set_tip_area()` to the
hovered key's bounding box so GTK repositions/re-queries when moving between keys.

### 6.4 Heatmap
Sidebar entry **Heatmap**. The window always opens on the **Layout** page. Opt-in (`heatmap-recording`, default off). Stores only
per-(local day, layer, key index) press counts per layout in
`$XDG_DATA_HOME/vibemapp/heatmap/<layout_id>.json` (atomic writes, flushed at most
every ~30 s and on shutdown). Contract: `heatmap_recording` property,
`get_key_counts(period, layer)`, `reset_key_counts()`, `key-counts-changed` signal,
`HeatmapPeriod` (Today / 7 days / All time).
UI: opt-in `Adw.StatusPage` when off; when on, the keyboard drawn as a heatmap
(sequential colour ramp readable in light and dark, counts on keys, legend), period
toggle, layer filter (All layers + each layer), total presses and top keys, pause and
reset (confirmation `Adw.AlertDialog`).

## 7. Release 0.3 — group rules (Steam games)
Modelled on the user's Hyprland rule `match = { class = ".*steam_app_.*" }`.
`AppRule.match` (`RuleMatch`):
- `APP` (default, existing behaviour): exact window class.
- `CLASS_PATTERN`: `app_class` is a regex that must `re.fullmatch` the window class.
- `STEAM_GAME`: `app_class == ""` → any Steam game; otherwise a Steam app id → that game.

Steam detection (backend): a window is a Steam game when its class is `steam_app_<id>`
(Proton) or its process (Hyprland client `pid`) has `SteamAppId`/`SteamGameId` in
`/proc/<pid>/environ` (native games). The Steam client (class `steam`) is never a game.
Names come from `appmanifest_<id>.acf` in every library listed in
`~/.local/share/Steam/steamapps/libraryfolders.vdf` (also `~/.steam/steam`); tools such as
Proton, Steam Linux Runtime and Steamworks Common Redistributables are filtered out.
Rules JSON stays backwards compatible (missing `match` = `APP`).

UI: Add Rule offers **App**, **All Steam Games** (one click, Steam icon) and
**Window Class Pattern** (advanced, live regex
validation and a live "matches: …" preview against running windows). New specific rules
are inserted above catch-all rules; the rules list shows group rules with a distinct
icon/subtitle ("Any Steam game"). The focus status row shows the Steam game name.

0.3.1: the single **Steam Game** picker was removed from Add Rule (too Steam-focused);
existing single-game rules still display, edit and match.

1.0.0: first stable release (version bump only; no functional changes since 0.3.1).
