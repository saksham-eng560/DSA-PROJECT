"""Human behaviour emulation (PLAN.md §14): typing cadence, Bézier mouse paths, scroll & dwell time."""

from __future__ import annotations

import random
import string
import time
from typing import Any

from app.config import settings


def _enabled() -> bool:
    return settings.HUMAN_EMULATION


def pause(min_s: float, max_s: float) -> None:
    if _enabled():
        time.sleep(random.uniform(min_s, max_s))


def dwell() -> None:
    """Page dwell time before acting: 3-15 seconds (randomized)."""
    pause(3, 15)


def short_pause() -> None:
    pause(0.3, 1.2)


def _bezier(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], t: float) -> tuple[float, float]:
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def move_mouse_to(page: Any, locator: Any) -> None:
    if not _enabled():
        return
    try:
        box = locator.bounding_box()
    except Exception:  # noqa: BLE001
        box = None
    if not box:
        return
    target = (box["x"] + box["width"] * random.uniform(0.3, 0.7), box["y"] + box["height"] * random.uniform(0.3, 0.7))
    start = getattr(page, "_aa_mouse", (random.uniform(0, 400), random.uniform(0, 300)))
    c1 = (start[0] + (target[0] - start[0]) * random.uniform(0.2, 0.4) + random.uniform(-80, 80),
          start[1] + (target[1] - start[1]) * random.uniform(0.2, 0.4) + random.uniform(-80, 80))
    c2 = (start[0] + (target[0] - start[0]) * random.uniform(0.6, 0.8) + random.uniform(-60, 60),
          start[1] + (target[1] - start[1]) * random.uniform(0.6, 0.8) + random.uniform(-60, 60))
    steps = random.randint(18, 35)
    for i in range(1, steps + 1):
        x, y = _bezier(start, c1, c2, target, i / steps)
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.005, 0.02))
    page._aa_mouse = target  # type: ignore[attr-defined]


def human_click(page: Any, locator: Any) -> None:
    locator.scroll_into_view_if_needed()
    move_mouse_to(page, locator)
    short_pause()
    locator.click()


def human_type(page: Any, locator: Any, text: str, clear: bool = True) -> None:
    """Type with 80-150ms per character, occasional typo + backspace. Falls back to fill()."""
    text = "" if text is None else str(text)
    if not _enabled() or len(text) > 400:
        locator.fill(text)
        return
    locator.scroll_into_view_if_needed()
    move_mouse_to(page, locator)
    locator.click()
    if clear:
        locator.fill("")
    for char in text:
        if char.isalpha() and random.random() < 0.02:
            page.keyboard.type(random.choice(string.ascii_lowercase))
            time.sleep(random.uniform(0.08, 0.2))
            page.keyboard.press("Backspace")
        page.keyboard.type(char)
        time.sleep(random.uniform(0.08, 0.15))
    # Verify the value really landed (some React inputs drop keystrokes)
    try:
        if locator.input_value() != text:
            locator.fill(text)
    except Exception:  # noqa: BLE001
        pass


def human_scroll(page: Any, total: int | None = None) -> None:
    if not _enabled():
        return
    distance = total or random.randint(400, 1400)
    scrolled = 0
    while scrolled < distance:
        step = random.randint(80, 220)
        page.mouse.wheel(0, step)
        scrolled += step
        time.sleep(random.uniform(0.05, 0.25))
