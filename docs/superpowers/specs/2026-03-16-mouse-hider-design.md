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
   - `SetCursorPos(gaze_x, gaze_y)` — teleports cursor to current gaze position **before** `ShowCursor(True)` to avoid one-frame flicker at old position
   - `ShowCursor(True)` — makes cursor visible
   - Timer resets to 2s
3. **Tracking off (`Ctrl+Alt+G`):** `set_enabled(False)` is called — cursor is restored, timer cancelled. Resumes when tracking turns back on via `set_enabled(True)`. `set_enabled` is idempotent — safe to call multiple times in the same state.
4. **No gaze signal:** If gaze is lost (face not detected), timer still runs. On wake, cursor teleports to last known gaze position. If no gaze position has ever been set (`_gaze_pos` is `None`), `SetCursorPos` is skipped and the cursor reappears in place.
5. **Calibration:** `set_enabled(False)` is called when calibration starts (cursor must be visible during calibration UI). `set_enabled(True)` is called when calibration ends.
6. **App exit:** `set_enabled(False)` is called in `quit_app()` to restore the cursor before the process exits — prevents a hidden cursor persisting in the OS after Glaze closes.
7. **Listener failure:** If the `pynput.mouse.Listener` fails to start, the error is logged and `MouseHider` disables itself permanently (`_enabled = False`) — it will never hide the cursor without a recovery path.

---

## Architecture

### New class: `MouseHider` in `focus_controller.py`

```
pynput.mouse.Listener  →  _on_move()
                            ↓ any movement
                           SetCursorPos(gaze_x, gaze_y)  ← before ShowCursor
                           ShowCursor(True)
                           cancel + restart 2s timer

FocusController.update() →  set_gaze_pos(ax, ay)
                            (called every frame when ax/ay are not None)

threading.Timer (2s)     →  _hide()
                            ShowCursor(False)
```

### Public interface

```python
class MouseHider:
    def set_gaze_pos(self, ax: int, ay: int) -> None:
        """Update current gaze position. Called every frame when gaze is valid."""

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable. Idempotent. When disabled, restores cursor and cancels timer."""

    def stop(self) -> None:
        """Restore cursor and stop listener. Called on app exit."""
```

### Internal state

| Field | Type | Description |
|---|---|---|
| `_gaze_pos` | `tuple[int,int] \| None` | Last known gaze position; `None` until first valid gaze |
| `_hidden` | bool | Whether cursor is currently hidden |
| `_enabled` | bool | Whether MouseHider is active |
| `_timer` | `threading.Timer \| None` | Active hide timer |
| `_lock` | `threading.Lock` | Protects all mutable state including `_hidden` |
| `_listener` | `pynput.mouse.Listener \| None` | Mouse listener; `None` if failed to start |

### `ShowCursor` safety

Windows `ShowCursor` uses a reference counter, not a boolean. Calling `ShowCursor(False)` multiple times decrements the counter below -1 — cursor stays hidden even after one `ShowCursor(True)`. The implementation guards with `_hidden` flag. Both `_hide()` and `_show()` must acquire `_lock` before checking and mutating `_hidden` to avoid a TOCTOU race between the timer thread and the pynput listener thread:

- `_hide()`: acquires `_lock`; only calls `ShowCursor(False)` if `_hidden == False`, then sets `_hidden = True`
- `_show()`: acquires `_lock`; only calls `ShowCursor(True)` if `_hidden == True`, then sets `_hidden = False`

### Integration in `FocusController`

```python
# __init__
self.mouse_hider = MouseHider()

# update() — called every frame
# Only update gaze pos when both coordinates are valid
if ax is not None and ay is not None:
    self.mouse_hider.set_gaze_pos(ax, ay)
# When ax/ay are None (gaze lost), set_gaze_pos is not called —
# MouseHider retains last known position
```

---

## Integration point in `main.py`

`main.py` manages the `tracking_enabled` flag. The following calls are added:

```python
# Ctrl+Alt+G toggle
focus_controller.mouse_hider.set_enabled(tracking_enabled)

# Ctrl+Alt+C calibration start
focus_controller.mouse_hider.set_enabled(False)
# ... calibration runs ...
# Ctrl+Alt+C calibration end
focus_controller.mouse_hider.set_enabled(True)

# Ctrl+Alt+Q quit
focus_controller.mouse_hider.stop()
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
| `glaze-app/main.py` | Call `set_enabled()` on tracking toggle, calibration start/end, and `stop()` on quit |
| `glaze-app/config.py` | Add `MOUSE_HIDE_DELAY_S = 2.0` |

No new dependencies. `pynput` already in `requirements.txt`.

---

## Tests

Unit tests in `glaze-app/tests/test_mouse_hider.py`. Win32 calls (`SetCursorPos`, `ShowCursor`) are mocked.

- `test_hide_called_after_delay` — timer fires, `ShowCursor(False)` called once, `_hidden` becomes True
- `test_show_on_move_teleports_cursor` — movement triggers `SetCursorPos` **then** `ShowCursor(True)` (order verified)
- `test_no_double_hide` — calling `_hide()` twice only calls `ShowCursor(False)` once
- `test_no_double_show` — calling `_show()` twice only calls `ShowCursor(True)` once
- `test_disabled_prevents_hide` — `set_enabled(False)` cancels timer, restores cursor
- `test_gaze_pos_used_on_wake` — cursor teleports to last `set_gaze_pos` coordinates
- `test_no_teleport_when_no_gaze` — if `set_gaze_pos` was never called, wake skips `SetCursorPos`
- `test_stop_restores_cursor` — `stop()` calls `ShowCursor(True)` if hidden

---

## Out of scope

- Visual transition / fade on hide/show
- Per-app exclusion list
- Separate hotkey for MouseHider (uses existing `Ctrl+Alt+G`)
