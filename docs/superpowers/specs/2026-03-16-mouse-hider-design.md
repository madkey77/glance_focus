# MouseHider — Design Spec

**Date:** 2026-03-16
**Feature:** Hide mouse cursor after 2s of inactivity; reappear at gaze position on movement.

---

## Goal

When the user is not touching the mouse, the cursor should disappear to reduce visual noise. When the user moves the mouse again, the cursor teleports to the current gaze position before reappearing — so the mouse "starts from where the eyes are."

---

## Behavior

1. **Idle timer:** After 2 seconds with no mouse movement, the cursor is hidden.
2. **Wake on movement:** Any mouse movement (delta ≠ 0) triggers wake:
   - `SetCursorPos(gaze_x, gaze_y)` — teleports cursor to current gaze position
   - `ShowCursor(True)` — makes cursor visible
   - Timer resets to 2s
3. **Tracking off (`Ctrl+Alt+G`):** MouseHider pauses — cursor always visible, timer not running. Resumes when tracking turns back on.
4. **No gaze signal:** If gaze is lost (face not detected), timer still runs but on wake the cursor teleports to last known gaze position.

---

## Architecture

### New class: `MouseHider` in `focus_controller.py`

```
pynput.mouse.Listener  →  _on_move()
                            ↓ any movement
                           SetCursorPos(gaze_x, gaze_y)
                           ShowCursor(True)
                           cancel + restart 2s timer

FocusController.update() →  set_gaze_pos(ax, ay)
                            (called every frame with latest gaze coords)

threading.Timer (2s)     →  _hide()
                            ShowCursor(False)
```

### Public interface

```python
class MouseHider:
    def set_gaze_pos(self, ax: int, ay: int) -> None
        """Update current gaze position. Called every frame."""

    def set_enabled(self, enabled: bool) -> None
        """Enable/disable. When disabled, cursor is restored and timer cancelled."""
```

### Internal state

| Field | Type | Description |
|---|---|---|
| `_gaze_x`, `_gaze_y` | int | Last known gaze position |
| `_hidden` | bool | Whether cursor is currently hidden |
| `_enabled` | bool | Whether MouseHider is active |
| `_timer` | `threading.Timer \| None` | Active hide timer |
| `_lock` | `threading.Lock` | Protects all mutable state |

### `ShowCursor` safety

Windows `ShowCursor` uses a reference counter, not a boolean. Calling `ShowCursor(False)` multiple times decrements the counter below -1 — cursor stays hidden even after one `ShowCursor(True)`. The implementation guards with `_hidden` flag:
- `_hide()`: only calls `ShowCursor(False)` if `_hidden == False`, then sets `_hidden = True`
- `_show()`: only calls `ShowCursor(True)` if `_hidden == True`, then sets `_hidden = False`

### Integration in `FocusController`

```python
# __init__
self.mouse_hider = MouseHider()

# update() — after _last_valid_pos is set
if ax is not None and ay is not None:
    self.mouse_hider.set_gaze_pos(ax, ay)
```

`Ctrl+Alt+G` (tracking toggle) already calls into `FocusController`. The toggle will additionally call `self.mouse_hider.set_enabled(tracking_on)`.

---

## Integration point in `main.py`

`main.py` manages the `tracking_enabled` flag. When toggling via `Ctrl+Alt+G`, it will call:

```python
focus_controller.mouse_hider.set_enabled(tracking_enabled)
```

---

## Config

```python
MOUSE_HIDE_DELAY_S = 2.0   # seconds of inactivity before cursor hides
```

Added to `config.py`.

---

## Files changed

| File | Change |
|---|---|
| `glaze-app/focus_controller.py` | Add `MouseHider` class; wire into `FocusController.__init__` and `update()` |
| `glaze-app/main.py` | Call `mouse_hider.set_enabled()` on tracking toggle |
| `glaze-app/config.py` | Add `MOUSE_HIDE_DELAY_S = 2.0` |

No new dependencies. `pynput` already in `requirements.txt`.

---

## Tests

Unit tests in `glaze-app/tests/test_mouse_hider.py`:

- `test_hide_called_after_delay` — timer fires, `_hidden` becomes True
- `test_show_on_move_teleports_cursor` — movement triggers `SetCursorPos` + show
- `test_no_double_hide` — calling hide twice doesn't double-decrement ShowCursor
- `test_disabled_prevents_hide` — `set_enabled(False)` cancels timer, restores cursor
- `test_gaze_pos_used_on_wake` — cursor teleports to last `set_gaze_pos` coordinates

Win32 calls (`SetCursorPos`, `ShowCursor`) are mocked in tests.

---

## Out of scope

- Visual transition / fade on hide/show
- Per-app exclusion list
- Separate hotkey for MouseHider (uses existing `Ctrl+Alt+G`)
