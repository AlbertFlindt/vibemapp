# Vibemapp — design specification

How Vibemapp works today, by feature. Release history is in `CHANGELOG.md`; open work and
the reasons behind decisions are in `PROGRESS.md`.

## 1. Goal and scope
A native, GNOME-styled Linux replacement for ZSA Keymapp, for ZSA keyboards (Voyager,
Moonlander, ErgoDox EZ, Planck EZ) running Oryx firmware.

- **In scope:** live layout view, Smart Layers (per-app layer switching), layout overlay,
  opt-in heatmap, tray icon, device handling, Arch packaging.
- **Out of scope:** firmware flashing, EEPROM writes, layout editing, Keymapp's gRPC API,
  focus sources other than Hyprland (the design allows them later).

## 2. Architecture

```
 Hyprland .socket2.sock ──► HyprlandFocusWatcher ──focus/monitor──► SmartLayersEngine ──set-layer──► OryxConnection ──► /dev/hidrawN
                                                                       ▲ rules (JSON per layout)          │ LAYER/KEYDOWN/KEYUP events
 sysfs / hidraw hotplug ──► DeviceMonitor ──────────────────────────┐   │                                  ▼
 Oryx GraphQL / cache ──► LayoutRepository ──────────────────────► ZsaKeyboardService (KeyboardService) ──signals──► UI, tray, overlay
```

- **The contract** is `src/vibemapp/models.py` + `service.py`. The UI only talks to a
  `KeyboardService`: `backend/zsa_service.py` (real) or `mock_service.py` (simulated,
  `VIBEMAPP_MOCK=1`). Every signal is emitted on the GLib main loop.
- Contract and rules-JSON changes are additive and backward compatible.
- UI: libadwaita, `Adw.ApplicationWindow` → `Adw.NavigationSplitView`. The sidebar pages are
  **Layout**, **Smart Layers**, **Heatmap** and **Overlay** (Ctrl+1–4). The window always opens
  on Layout. The header has a device menu and the primary menu (Preferences, Keyboard
  Shortcuts, About). Errors show as `Adw.Toast`s.
- App lifecycle, tray and actions live in `main.py`. Settings are GSettings
  (`io.github.albertflindt.Vibemapp`, fixed path).
- Dev runs (`./run-dev.sh`, `VIBEMAPP_DEVEL=1`) use app ID `io.github.albertflindt.Vibemapp.Devel`
  and share config, cache and GSettings with the installed app.

## 3. Device and protocol

### 3.1 Device enumeration
- Walk `/sys/class/hidraw/*/device/uevent` and match `HID_ID` vendor `00003297` (ZSA) or the
  legacy ErgoDox EZ `0000FEED`. Product IDs map to `KeyboardModel` (Voyager: 0x1977).
- A board exposes several hidraw nodes. The raw-HID one has Usage Page 0xFF60, Usage 0x61 in
  its `report_descriptor`.
- Hotplug via `Gio.FileMonitor` on `/dev`, with a polling fallback.
- Access comes from `data/udev/50-zsa.rules` (`uaccess`), not a `plugdev` group.

### 3.2 Oryx raw-HID protocol
The full, verified notes are in the `backend/oryx.py` module docstring. The source is
`zsa/qmk_modules` `oryx/oryx.c`, pinned by `zsa/qmk_firmware` branch `firmware25`
(protocol version 0x05). The essentials:
- 32-byte reports. Write a leading `0x00` report number; replies end at `0xFE`, and bytes
  after it are garbage.
- `GET_FW_VERSION` works unpaired and returns `<layoutHashId>/<revisionHashId>`.
- `PAIRING_INIT` pairs at once (RAM flag only). Older firmware that asks for key presses
  counts as "Oryx not available".
- `SET_LAYER(1, n)` is `layer_move(n)`. Returning to base is `SET_LAYER(1, 0)`.
- While paired the keyboard sends `LAYER` on every highest-layer change (including holding
  MO/LT keys) and `KEYDOWN`/`KEYUP` with **col, then row**.
- The keyboard keeps its layer when the host goes away.
- **Safety:** `oryx.ALLOWED_COMMANDS` only allows fw version, pairing, set-layer and protocol
  version. Nothing touching LEDs, EEPROM or the bootloader can be encoded.
- Key index order is the whole left half, then the right half (not QMK's row interleave).
  Geometry, layouts, key events, heatmap and overlay all use it.

### 3.3 Layout fetch
- Oryx GraphQL `https://oryx.zsa.io/graphql`, by layout hash, geometry and revision.
- QMK/Oryx keycodes map to short labels; tap and hold labels; layer titles and colours. A hold
  with modifiers is labelled with them (`⌃C`), and `KeyBinding.hold_combo` names a hold that
  sends a key (`Ctrl+C`; empty for layer keys, mod-taps and lone modifiers).
- Tapping terms: each key object's `tappingTerm` (`KeyBinding.tapping_term_ms`) and the
  revision's `config` JSON (`Layout.tapping_term_ms`). `config` holds only settings changed from
  Oryx's defaults; the name of a changed global term in it (`tappingTerm`) is a guess, since no
  layout with one has been seen. Unset means QMK's 200 ms.
- Cached at `$XDG_CACHE_HOME/vibemapp/layouts/<layout>-<revision>.json` and used offline.
- **Transparent keys:** Oryx stores them as all-null key objects (`KC_TRNS` counts too,
  `KC_NO` doesn't); `KeyBinding.transparent` marks them. They fall through to the base layer
  only (`ui/transparency.py`), because set-layer is `layer_move`.
- Board geometry for all four models is in `backend/geometries.py` (from QMK data; only the
  Voyager is checked on hardware).

### 3.4 Focus source (Hyprland)
- Sockets live in `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/`. The env var may be
  unset, so the code globs `$XDG_RUNTIME_DIR/hypr/*/` and picks the newest live socket.
- Events from `.socket2.sock`: `activewindow>>class,title` (split on the first comma only),
  `activewindowv2>>`, `closewindow>>`, empty class for no focus, and `focusedmon>>` /
  `focusedmonv2>>` for the overlay's monitor.
- Read-only queries on `.socket.sock`: `j/clients`, `j/activewindow`, `j/monitors`. Vibemapp
  never changes Hyprland state.
- Reconnects with backoff. `FocusWatcher` is the base class for future GNOME/KDE/X11 sources.
- Hyprland config is Lua (`hl.on`, `hl.exec_cmd`, `hl.bind`, `hl.dsp.exec_cmd`).

## 4. Features

### 4.1 Live layout
A custom `KeyboardView` (GtkSnapshot) draws keys as rounded rects in libadwaita colours:
tap label centred, hold label small at the bottom, accent for pressed keys. The layer selector
follows `layer-changed` with a crossfade. Clicking a layer only changes the view (optionally
requests it on the keyboard). Per-key tooltips use `Gtk.Tooltip.set_tip_area()` so they
follow the pointer. Transparent keys are faint dashed caps with the dimmed fall-through
label and ▽. Key and layer wiring is shared with the overlay (`ui/view_binder.py`).

No keyboard: an `Adw.StatusPage` "Connect Your Keyboard", with a udev hint when a ZSA device
exists but can't be opened.

### 4.2 Smart Layers
- Rules per layout id in `$XDG_CONFIG_HOME/vibemapp/smart-layers.json`
  (`{"version":1,"layouts":{"<id>":{"fallback":0,"rules":[...]}}}`, atomic writes).
- `AppRule.match` (`RuleMatch`; a missing field means `APP`):
  - `APP`: exact window class, optional title regex (`re.search`).
  - `CLASS_PATTERN`: regex that must `re.fullmatch` the class.
  - `STEAM_GAME`: empty `app_class` means any Steam game; an app id means that game (still
    matched and editable, no longer creatable from the UI).
- First enabled matching rule wins; no match → fallback layer (`None` = don't change, the
  default). Set-layer is only sent when the target differs from the keyboard's layer; focus
  flips are debounced ~50 ms.
- A title-only change (same class) sends a layer only when the matched target changes, so a
  shell setting its title per command doesn't undo a layer toggled by hand. Focusing another
  app, enabling, connecting and rule edits re-assert the target.
- While any key is held, switching waits until the last key is released (10 s timeout in case
  a KEYUP is lost). Set-layer is `layer_move`, which would drop a held MO/LT layer, e.g. when
  Super+arrow on a held layer moves focus.
- Our own window (both app IDs) is ignored, so editing rules doesn't reset the layer.
- Paused when disabled or disconnected; re-evaluates on enable/connect. Restores layer 0 on
  quit, SIGTERM or disconnect if Smart Layers moved it. Firmware keys TOGGLE_SMART_LAYER and
  TRIGGER_SMART_LAYER toggle the feature and force a re-evaluation.
- **Notifications** (opt-in, `notify-layer-changes`, off by default): `ui/layer_notifier.py`
  listens to `rule-applied` and sends one notification with the fixed id `layer-changed`
  (each replaces the last), low priority. Title: the layer ("Layer 2 · Media"); body:
  "Switched for Firefox" (focused app or Steam game name) or "No rule for Firefox, using the
  fallback layer". Skipped when the layer doesn't actually change or our window is active.
  `notify-excluded-layers` (`ai`, default `[]`) lists layers that never notify, e.g. a mouse
  layer the firmware turns on when the trackpad is touched: switches to them are silent, and
  while one is active the target is compared with the last active layer that isn't excluded
  (tracked from `layer-changed`), so Base → Mouse → a rule for Base is silent but a rule for
  Code notifies. With no such layer known it notifies. The decision is the pure `layer_notice`.
- **Steam detection:** class `steam_app_<id>` (Proton) or `SteamAppId`/`SteamGameId` in
  `/proc/<pid>/environ` (native). The `steam` client is never a game. Names come from
  `appmanifest_<id>.acf` in every library in `libraryfolders.vdf`; Proton, runtimes and
  redistributables are filtered out.
- **UI:** master `Adw.SwitchRow`, focus status row ("Focused: kitty → Layer 1", Steam game
  names), rules list (layer combo, enable switch, remove), fallback combo, `Adw.Banner` when
  Hyprland isn't found. Add Rule offers **App** (running or installed apps), **All Steam
  Games** and **Window Class Pattern** (live regex validation and "matches: …" preview). New
  specific rules go above catch-all rules.

### 4.3 Overlay
A see-through, click-through drawing of the active layer with live key presses. Code:
`ui/overlay.py` (surfaces, `OverlayController`), `ui/overlay_state.py` (pure logic, see its
docstring for sizing and half-spacing rules), `ui/overlay_page.py`/`.ui`.
- **Requires `gtk4-layer-shell`** (optional package, no fallback window). `vibemapp/_preload.py`
  loads the library before any Gtk import. Without it the page shows "Overlay Unavailable"
  with the install command, the tray item is hidden and `--toggle-overlay` prints a hint.
- Surfaces: plain `Gtk.Window`s not attached to the application, layer OVERLAY (above
  fullscreen windows), keyboard mode NONE, namespace `vibemapp-overlay`, exclusive zone -1,
  empty input region. One surface for a joined board, two when the halves are spaced apart.
- **Hiding never unmaps.** On Hyprland, unmapping a layer surface recomputes keyboard focus and
  takes it from another app's focused panel (e.g. Noctalia's session menu opened on a held
  layer). A hidden surface fades to transparent and parks on the BACKGROUND layer (below all
  windows, so not over fullscreen ones); showing moves it back to OVERLAY. A hidden surface
  switches layers without the crossfade, so the old layer never flashes. Surfaces are still
  rebuilt when placement changes (monitor, size, anchor, spacing).
- **Visibility** (`overlay-mode`): Off, On Selected Layers (default; `overlay-layers`, none
  ticked by default), Always. Optional hide after N idle seconds. Fades in over `overlay-fade-ms` (default 200 ms, 0 = instant)
  and out in three quarters of that. Vibemapp animates the fade itself; the compositor's layer
  animations never apply, since the surface is never unmapped.
- **Appearance:** anchor (bottom, top and the four corners, 24 px margin); monitor (focused
  via Hyprland, or a fixed connector); size as % of the real keyboard (19.05 mm per key unit,
  default 80 %, 96 DPI fallback); half spacing 0–100 %; at-rest opacity (pressed keys are
  full strength); fade duration. Drawing: outlines and tap labels only, white with a dark halo, no hold labels
  or tooltips; a layer-name pill.
- **Control:** a Preview switch on the page (`app.overlay-preview`), a tray checkbox,
  `app.toggle-overlay` / `vibemapp --toggle-overlay` for a Hyprland bind (the page shows the
  snippet). Toggle remembers the last non-off mode in memory.
- Additive contract: `get_pressed_keys()`, `get_focused_monitor()`, `focused-monitor-changed`.

#### 4.3.1 Hold-action pill
A short on-screen pill (`Ctrl+C`) when a key's hold action fires, e.g. hold C = Ctrl+C. Code:
`ui/hold_actions.py` (pure detection), `ui/hold_pill.py` (surface, `HoldPillController`), set
up by the application next to the overlay; settings in the Overlay page's Hold Actions group.
- **Detection is inferred.** Oryx key events are raw matrix events sent before QMK decides tap
  or hold (`pre_process_record_oryx`). Oryx builds "hold for another key" as a tap dance, which
  resolves as a hold when the key is still down one tapping term after its press and no other
  key was pressed meanwhile; `HoldDetector` watches for exactly that. A press within a term of
  the same key's tap continues the dance and doesn't count. Term: the key's, else the
  layout's, else 200 ms. Only `hold_combo` keys count, so mod-taps (permissive hold makes them
  uncertain) and layer keys never show a pill. A transparent key uses the base layer's key.
- **Surface:** one layer-shell surface like the overlay's (OVERLAY layer, keyboard mode NONE,
  exclusive zone -1, empty input region, never unmapped once shown; hidden it parks on
  BACKGROUND), namespace `vibemapp-hold-pill`, anchored to the bottom edge only so it's centred.
  10 % of the monitor height up, or 12 px above the overlay while that is shown on the bottom
  edge. Same monitor as the overlay. Shows 900 ms (100 ms fade in, 250 ms out); another hold
  replaces the text and restarts the timer. Shown also while the Vibemapp window is active.
- **Settings:** `hold-pill` (off by default) and `hold-pill-excluded` (`"layer:index"` ids; the
  Keys expander lists every key with a hold action, all on by default). Needs gtk4-layer-shell.

### 4.4 Heatmap
Opt-in (`heatmap-recording`, default off). Stores only per-(local day, layer, key index)
counts per layout in `$XDG_DATA_HOME/vibemapp/heatmap/<layout_id>.json`, flushed at most every
~30 s and on shutdown. File version 2 adds, beside the unchanged `days` totals, `app_days`
(the same counts per focused app: window class, or `steam:<app id>`) and `app_names` (Steam game
names). Version 1 files load as All Apps only. Window titles are never stored. Contract:
`heatmap_recording`, `get_key_counts(period, layer, app)`, `list_heatmap_apps(period)`
(`HeatmapApp`), `reset_key_counts()` (clears per-app data too), `key-counts-changed`,
`HeatmapPeriod` (Today / 7 days / All time).
UI: opt-in status page when off; when on, a heatmap keyboard (sequential ramp readable in
light and dark, counts on keys, legend), period toggle, layer filter, totals and top keys,
pause and reset (confirmation dialog), and an App filter (hidden while no app has counts).

#### 4.4.1 Key pairs, ergo score and the Insights page
- **Finger map:** `geometries.FINGERS[model]` (one `Finger` per Oryx index) and `HOME_KEYS[model]`
  for all four boards, from the usual column convention (outer two columns pinky, then ring,
  middle, the rest index; rotated thumb-cluster keys and the Planck's centre bottom row are
  thumbs). Home row is the 3rd row (Planck: 2nd); the thumb home is the thumb key nearest the
  index home key.
- **Key pairs:** a separate opt-in (`pair-recording`, default off; the service property
  `pair_recording` only records while `heatmap_recording` is on too). A pair is two presses less
  than `PAIR_WINDOW_S` = 1 s apart, by (layer, key index). The previous press is kept in memory
  only and dropped on idle gaps, focus or layout change, disconnect and when either opt-in
  toggles. Stored per day as `pairs` (all apps) and `app_pairs` in the same v2 heatmap file.
- **Score** (`backend/ergo.py`, pure; all tunables in `WEIGHTS`): finger load and hand split,
  finger overload above a per-finger capacity, hand imbalance, same-finger pair rate (target
  under 1%), reach cost (distance from the finger's home key / finger strength) and layer-switch
  rate. Each part becomes a penalty; `score` = 100 − Σ penalties, clamped to 0..100. Contract:
  `get_ergo_report(period, app)` → `ErgoReport` (None without a finger map).
- The mock's own Voyager geometry is row-interleaved, so it maps through `MOCK_TO_ORYX` before scoring. Report
  keys (`worst_same_finger`, which carries the `finger`) use the service's own indices, so the mock maps
  them back.
- **Insights page** (sidebar, Ctrl+5): Finger Load only (since 1.5.0), with its own Period toggle and
  App filter: a bar per finger with a capacity line (warning colour above it) and the hand split, from
  `ErgoReport.finger_load` and `left_share`. A Recording group holds the key-press opt-in. States: no
  layout, recording off with no data (opt-in status page), no finger map, no presses yet.
- **Not shown since 1.5.0:** same-finger pairs, reach, layer switches, the score and Alternative
  Layouts. The backend still computes them (`ErgoReport`, `get_layout_comparisons`) so the contract
  stays additive. `pair-recording` has no switch and the app no longer binds it, so the service
  keeps its default (off) and no pairs are recorded; the GSettings key and stored pairs stay.
- **Letters** (`backend/keykinds.py`, pure): a key is a letter when its tap resolves to one alphabetic
  character (incl. language variants and Æ Ø Å; home-row mods stay letters), from `KeyBinding.tap_code`
  (additive, filled by `oryx_api`), the label only as a fallback.
- **Alternative layouts** (`backend/layouts.py`, pure): single letters are never moved, so the
  letters are compared as whole layouts. The base layer's 30-key letter block is found by QWERTY letter
  keycodes (the `;` slot right of L, `, . /` right of M; none found → no comparison). QWERTY, Colemak,
  Colemak-DH, Workman and Dvorak (its `'` on the `/` slot) are simulated by moving the block's layer-0
  presses and pairs with their character, then scored with `ergo.report`. Non-ASCII letters (Æ on the
  `;` slot) stay put; a letter a layout wants there goes to that layout's `;` slot. Contract:
  `get_layout_comparisons(period, app)` → `list[LayoutComparison]`, best first; nothing below `COMPARISON_MIN_PRESSES` = 2,000 presses.
  No UI since 1.5.0 (it was an Alternative Layouts group on the Insights page).

### 4.5 Tray and background
Hyprland has no minimise, so with "Show in tray" on and a tray host present, closing the
window hides it to a StatusNotifierItem (`org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`
over Gio D-Bus, no GTK). `--background` starts in the tray; without a tray, "Run in
background" keeps the app alive with `Gio.Application.hold`. Left click toggles the window.
Menu: status header (keyboard and layer) / Show|Hide Vibemapp / Smart Layers / Show Overlay /
Preferences / Quit. Re-registers if the watcher restarts.

### 4.6 Preferences
`Adw.PreferencesDialog`: run in background, show in tray, notify on layer changes and a Silent Layers
expander (one switch per layer of the loaded layout, insensitive while notifications are off,
a hint row with no layout; writes `notify-excluded-layers`) (Smart Layers group), Smart Layers on startup, auto-connect, plus the Hyprland Lua autostart hint
(`hl.on("hyprland.start", function () hl.exec_cmd("vibemapp --background") end)`).

## 5. Rename and migration
Formerly KeyMapp for Linux. On first start, `~/.config/keymappforlinux/` and
`~/.cache/keymappforlinux/` move to the `vibemapp` dirs (only missing entries if the new dirs
exist), and GSettings values are copied once from `/io/github/albertflindt/KeyMappForLinux/`
(guarded by `migrated-from-keymappforlinux`). Obsolete keys are reset once
(`obsolete-keys-version`). Both read old paths through a relocatable schema copy compiled to a
temp dir, because opening a fixed-path schema at another path aborts GIO.

## 6. Packaging
- `meson.build` installs the package to `$datadir/vibemapp/`, the `vibemapp` launcher, desktop
  file, metainfo, icons, the compiled gschema and `/usr/lib/udev/rules.d/50-zsa.rules`.
- `packaging/arch/PKGBUILD` builds the `v$pkgver` tag from GitHub:
  `depends=(python python-gobject gtk4 libadwaita glib2)`, `makedepends=(meson git)`,
  optdepends `hyprland` and `gtk4-layer-shell`. Its `check()` runs the test suite.
