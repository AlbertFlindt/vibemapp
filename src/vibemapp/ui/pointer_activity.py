"""Pointer activity (ZSA Navigator trackpad or trackball) as text and state.

Pure Python (no GTK), so it's unit-testable without a display.

The service reports every action change (``PointerActivity``, see ``models.py``)
and re-sends TOUCH/MOVE/SCROLL frames with fresh contacts while fingers are
down, then IDLE once everything stopped. From that:

* ``pill_label`` is the short text (the pill's accessible label): "Click", "Tap",
  "Two-Finger Tap", "Scroll ↓", "Move". Taps say what the fingers did, not
  which button libinput turns them into: that mapping is the compositor's
  setting (left-right-middle or left-middle-right), so the gesture is the
  one thing known for sure, and it matches the Taps switch.
* ``PointerTracker`` keeps the pill's text across events: a click or tap isn't
  replaced by the motion that follows within ``KEEP_MS`` (or while a button is
  down, a drag), and it knows when the gesture ended so the pill can linger.
* ``live_label`` is the Trackpad page's livelier wording ("Two-Finger Scroll ↓").
* ``PadModel`` is what the pad drawing (``ui/pointer_view.py``) shows: finger
  dots, their trails, tap ripples, click flashes, a rolling trackball.
* ``want_pointer`` turns ``KeyboardService.pointer_tracking`` on while at least
  one part of the UI shows the activity.
"""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass

from ..models import PointerAction, PointerActivity, PointerKind
from .i18n import _

# pointer-pill-actions values, in the order the page lists them.
ACTIONS = ("click", "tap", "scroll", "move")
# A click or tap stays on the pill this long even if motion follows at once.
KEEP_MS = 500

ARROWS = {"up": "↑", "down": "↓", "left": "←", "right": "→"}

_CATEGORY = {
    PointerAction.CLICK: "click", PointerAction.RELEASE: "click",
    PointerAction.TAP: "tap", PointerAction.SCROLL: "scroll",
    PointerAction.MOVE: "move", PointerAction.TOUCH: "move",
}


def action_category(action: PointerAction) -> str | None:
    """The ``pointer-pill-actions`` value an action belongs to (None: IDLE)."""
    return _CATEGORY.get(action)


def _fingers_prefix(fingers: int) -> str:
    if fingers == 2:
        return _("Two-Finger")
    if fingers == 3:
        return _("Three-Finger")
    if fingers >= 4:
        return _("Four-Finger")
    return ""


def click_label(button: int) -> str:
    return {2: _("Right Click"), 3: _("Middle Click")}.get(button, _("Click"))


def tap_label(fingers: int) -> str:
    prefix = _fingers_prefix(fingers)
    # Translators: e.g. "Two-Finger Tap"
    return _("{fingers} Tap").format(fingers=prefix) if prefix else _("Tap")


def scroll_label(direction: str) -> str:
    arrow = ARROWS.get(direction, "")
    # Translators: "Scroll ↓"; the arrow is the direction
    return _("Scroll {arrow}").format(arrow=arrow).strip() if arrow else _("Scroll")


def pill_label(activity: PointerActivity) -> str | None:
    """The pill's text for ``activity``; None when it adds none (RELEASE, IDLE)."""
    action = activity.action
    if action == PointerAction.CLICK:
        return click_label(activity.button)
    if action == PointerAction.TAP:
        return tap_label(activity.fingers)
    if action == PointerAction.SCROLL:
        return scroll_label(activity.direction)
    if action in (PointerAction.MOVE, PointerAction.TOUCH):
        return _("Move")
    return None


def live_label(activity: PointerActivity) -> str | None:
    """The Trackpad page's live text; None keeps the previous one (RELEASE)
    and IDLE is the caller's hint text."""
    action = activity.action
    prefix = _fingers_prefix(activity.fingers)
    if action == PointerAction.TOUCH:
        # Translators: e.g. "Two-Finger Touch"
        return _("{fingers} Touch").format(fingers=prefix) if prefix else _("Touch")
    if action == PointerAction.MOVE and activity.kind == PointerKind.TRACKBALL:
        return _("Roll")
    if action == PointerAction.MOVE:
        return _("{fingers} Move").format(fingers=prefix) if prefix else _("Move")
    if action == PointerAction.SCROLL:
        if prefix:
            # Translators: e.g. "Two-Finger Scroll ↓"
            return _("{fingers} {scroll}").format(fingers=prefix,
                                                 scroll=scroll_label(activity.direction))
        return scroll_label(activity.direction)
    if action in (PointerAction.CLICK, PointerAction.TAP):
        return pill_label(activity)
    return None


def idle_hint(kind: PointerKind | None) -> str:
    if kind == PointerKind.TRACKBALL:
        return _("Move the trackball to try it")
    return _("Touch the trackpad to try it")


def device_title(kind: PointerKind | None) -> str:
    """Sidebar and page title: the device, or both names when there's none."""
    if kind == PointerKind.TRACKPAD:
        return _("Trackpad")
    if kind == PointerKind.TRACKBALL:
        return _("Trackball")
    return _("Trackpad & Trackball")


def device_icon(kind: PointerKind | None) -> str:
    return "input-mouse-symbolic" if kind == PointerKind.TRACKBALL else "input-touchpad-symbolic"


class PointerTracker:
    """The pill's pointer text and whether the gesture is still going.

    ``feed(activity, now_ms, allowed)`` returns True when ``text`` or ``active``
    changed. ``allowed`` holds the ``ACTIONS`` to show; others are ignored,
    except that their end (RELEASE, IDLE) still ends a shown gesture."""

    def __init__(self) -> None:
        self.text: str | None = None
        self.active = False
        self._category: str | None = None
        self._since = 0.0

    def reset(self) -> None:
        self.text = None
        self.active = False
        self._category = None

    def feed(self, activity: PointerActivity, now_ms: float,
             allowed: set[str] | frozenset[str]) -> bool:
        before = (self.text, self.active)
        action = activity.action
        if action == PointerAction.IDLE:
            if self.text is not None:
                self.active = bool(activity.buttons) and self._category == "click"
            return (self.text, self.active) != before
        if action == PointerAction.RELEASE:
            if self.text is not None and not activity.buttons and self._category == "click":
                self.active = False
                self._since = now_ms     # KEEP_MS counts from the release
            return (self.text, self.active) != before
        category = action_category(action)
        if category not in allowed:
            return False
        if category == "move" and self._category in ("click", "tap") and self.text and (
                activity.buttons or now_ms - self._since < KEEP_MS):
            # A drag, or motion right after a click: keep showing the click.
            self.active = True
            return (self.text, self.active) != before
        text = pill_label(activity)
        if text != self.text or category != self._category or action == PointerAction.TAP:
            self._since = now_ms
        self.text = text
        self._category = category
        # A tap is over when it's reported; everything else lasts until IDLE.
        self.active = action != PointerAction.TAP
        return (self.text, self.active) != before or action in (PointerAction.TAP,
                                                                 PointerAction.CLICK)


# --- the drawing's state --------------------------------------------------------

TRAIL_MS = 220        # a moving finger's trail fades over this
RIPPLE_MS = 450       # a tap's ripple grows and fades over this
FLASH_MS = 260        # a click's flash fades over this (while not held)
FINGER_FADE_MS = 160  # lifted fingers fade over this
BALL_RAD_PER_COUNT = 0.035   # trackball: rotation per motion count
BALL_SCROLL_RAD = 0.25       # trackball wheel: rotation per wheel step
# Surface marks on the ball, as (latitude, longitude) in degrees.
BALL_MARKS = ((0, 0), (35, 60), (-30, 120), (50, 200), (-45, 260), (10, 320),
              (-10, 170), (70, 90), (-70, 300), (20, 25))


def to_disc(x: float, y: float) -> tuple[float, float]:
    """A contact in the pad's square 0..1 range (0, 0 top left) -> the unit disc
    (-1..1, centre 0, 0), clamped onto the disc."""
    u, v = 2 * x - 1, 2 * y - 1
    r = math.hypot(u, v)
    return (u / r, v / r) if r > 1 else (u, v)


def ball_marks(rot_x: float, rot_y: float) -> list[tuple[float, float, float]]:
    """``BALL_MARKS`` on a ball rolled by ``rot_x`` (surface moves right for
    positive) and ``rot_y`` (down), as (x, y, z) on the unit sphere seen from
    above; z > 0 faces the viewer."""
    out = []
    for lat, lon in BALL_MARKS:
        la, lo = math.radians(lat), math.radians(lon)
        x, y, z = math.cos(la) * math.sin(lo), math.sin(la), math.cos(la) * math.cos(lo)
        x, z = x * math.cos(rot_x) + z * math.sin(rot_x), -x * math.sin(rot_x) + z * math.cos(rot_x)
        y, z = y * math.cos(rot_y) + z * math.sin(rot_y), -y * math.sin(rot_y) + z * math.cos(rot_y)
        out.append((x, y, z))
    return out


@dataclass(frozen=True)
class Ripple:
    x: float
    y: float
    start: float

    def progress(self, now: float) -> float:
        return max(0.0, min(1.0, (now - self.start) / RIPPLE_MS))


class PadModel:
    """What the pad drawing shows, fed with ``PointerActivity`` and the time in ms.

    Fingers are dots on the unit disc that follow the contacts; moving or
    scrolling fingers leave a trail (``TRAIL_MS``); a tap leaves a ripple
    (``RIPPLE_MS``); a click flashes (``FLASH_MS``) and stays pressed while a
    button is down. A trackball rolls by its motion (``rot_x``, ``rot_y``).
    ``animating(now)`` says whether anything still changes without new input,
    so the widget runs its frame clock only then."""

    def __init__(self, kind: PointerKind | None = None) -> None:
        self.kind = kind
        self.reset()

    def reset(self) -> None:
        self.contacts: tuple[tuple[float, float], ...] = ()
        self.trails: list[list[tuple[float, float, float]]] = []
        self.ripples: list[Ripple] = []
        self.lifted: tuple[tuple[float, float], ...] = ()
        self.lifted_at = -1e9
        self.pressed = False
        self.flash_at = -1e9
        self.scrolling = False
        self.rolling_at = -1e9
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.last_roll = (0.0, 0.0)     # the latest rotation step, for streaks

    def feed(self, activity: PointerActivity, now: float) -> None:
        self.kind = activity.kind
        action = activity.action
        self.pressed = bool(activity.buttons)
        if action == PointerAction.CLICK:
            self.flash_at = now
        elif action == PointerAction.TAP:
            spots = [to_disc(*c) for c in activity.contacts] or [(0.0, 0.0)]
            self.ripples += [Ripple(x, y, now) for x, y in spots]
            self._lift(now)
        elif action == PointerAction.IDLE:
            self._lift(now)
        elif action in (PointerAction.TOUCH, PointerAction.MOVE, PointerAction.SCROLL):
            self.scrolling = action == PointerAction.SCROLL
            if activity.kind == PointerKind.TRACKBALL:
                self._roll(activity, now)
            else:
                self._touch(activity, now)
        self.prune(now)

    def _lift(self, now: float) -> None:
        if self.contacts:
            self.lifted, self.lifted_at = self.contacts, now
        self.contacts = ()
        self.scrolling = False

    def _touch(self, activity: PointerActivity, now: float) -> None:
        contacts = tuple(to_disc(*c) for c in activity.contacts)
        if len(contacts) != len(self.contacts):
            self.trails = []            # finger count changed: trails don't line up
        self.contacts = contacts
        moving = activity.action != PointerAction.TOUCH
        if not moving:
            return
        while len(self.trails) < len(contacts):
            self.trails.append([])
        for trail, (x, y) in zip(self.trails, contacts):
            trail.append((x, y, now))

    def _roll(self, activity: PointerActivity, now: float) -> None:
        dx, dy = activity.motion
        if activity.action == PointerAction.SCROLL:
            if not (dx or dy):
                dx, dy = {"up": (0, 1), "down": (0, -1), "left": (-1, 0),
                          "right": (1, 0)}.get(activity.direction, (0, 0))
            # Wheel: positive is up/right; the ball's top then moves up/right.
            step = (dx * BALL_SCROLL_RAD, -dy * BALL_SCROLL_RAD)
        else:
            step = (dx * BALL_RAD_PER_COUNT, dy * BALL_RAD_PER_COUNT)
        if step != (0, 0):
            self.rot_x += step[0]
            self.rot_y += step[1]
            self.last_roll = step
            self.rolling_at = now

    def prune(self, now: float) -> None:
        for trail in self.trails:
            while trail and now - trail[0][2] > TRAIL_MS:
                trail.pop(0)
        self.ripples = [r for r in self.ripples if now - r.start < RIPPLE_MS]
        if now - self.lifted_at >= FINGER_FADE_MS:
            self.lifted = ()

    def flash(self, now: float) -> float:
        """Click strength 0..1: full while a button is down, else fading."""
        if self.pressed:
            return 1.0
        return max(0.0, 1.0 - (now - self.flash_at) / FLASH_MS)

    def lifted_alpha(self, now: float) -> float:
        return max(0.0, 1.0 - (now - self.lifted_at) / FINGER_FADE_MS) if self.lifted else 0.0

    def rolling(self, now: float) -> float:
        """Trackball: 1 right after it rolled, fading with the trail time."""
        return max(0.0, 1.0 - (now - self.rolling_at) / TRAIL_MS)

    def lit(self, now: float) -> float:
        """How strongly the pad is lit, 0..1 (touched, rolling or clicked)."""
        if self.contacts or self.pressed:
            return 1.0
        return max(self.flash(now), self.rolling(now), self.lifted_alpha(now),
                   max((1 - r.progress(now) for r in self.ripples), default=0.0))

    def animating(self, now: float) -> bool:
        return bool(any(self.trails) or self.ripples or self.lifted
                    or (not self.pressed and now - self.flash_at < FLASH_MS)
                    or now - self.rolling_at < TRAIL_MS)


# --- who wants pointer_tracking ------------------------------------------------

_demand: "weakref.WeakKeyDictionary[object, set[str]]" = weakref.WeakKeyDictionary()


def want_pointer(service, owner: str, on: bool) -> None:
    """Record whether ``owner`` ("pill", "overlay", "page") shows pointer
    activity, and set ``service.pointer_tracking`` while any of them does."""
    owners = _demand.setdefault(service, set())
    if on:
        owners.add(owner)
    else:
        owners.discard(owner)
    tracking = bool(owners)
    if bool(service.props.pointer_tracking) != tracking:
        service.props.pointer_tracking = tracking


__all__ = ["ACTIONS", "ARROWS", "KEEP_MS", "PadModel", "PointerTracker", "Ripple",
           "action_category", "ball_marks", "click_label",
           "device_icon", "device_title", "idle_hint", "live_label", "pill_label",
           "scroll_label", "tap_label", "to_disc", "want_pointer"]
