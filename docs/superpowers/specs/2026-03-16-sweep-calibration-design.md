# Sweep Calibration Refinement — Design Spec

**Date:** 2026-03-16
**Status:** Approved

---

## Goal

Add a third calibration phase — a slow horizontal ball sweep — that passively collects hundreds of gaze samples across the full screen and fits a 2D polynomial model that maps raw normalized gaze directly to absolute desktop pixels, replacing both the homography and the 3-point gain/bias for monitors that complete the sweep.

---

## Context

### Existing calibration pipeline (per monitor)

1. **Phase 1 — Homography (5 static points):** Centro + 4 cantos → `cv2.findHomography` → `self._homographies[mid]`
2. **Phase 2 — Gain/Bias (3 static validation points):** Global linear correction per axis → `self._corrections[mid] = (gain_x, bias_x, gain_y, bias_y)`
3. **Phase 3 (new) — Sweep:** Moving ball → polynomial 2D model → `self._poly_corrections[mid] = (coeffs_x, coeffs_y)`

### Correction priority in apply()

```
poly_corrections  →  gain/bias  →  raw homography
```

When `poly_corrections` is present for a monitor, it **replaces the full mapping** (homography is not applied first). The polynomial learns the complete gaze→pixel mapping from scratch using the sweep samples. The homography is only used as a fallback when no poly exists. This is intentional: the sweep collects enough samples (~500+) for a well-conditioned full mapping, and bypassing the homography avoids compounding its residual errors.

### Key constraints

- All Tkinter ops must go through `_TkCalibrationSession._cmd_queue` + `root.after()` — never called from outside the Tk thread
- `calibration.json` must stay retrocompatible (old format: plain homography dict; new format: `{"homographies": ..., "corrections": ..., "poly_corrections": ...}`)
- No external dependencies beyond what's already in `requirements.txt` (`numpy`, `cv2`)

---

## Design

### Ball movement

- **Path:** 5 horizontal rows at normalized Y positions computed as `np.linspace(0.10, 0.90, 5)` = `[0.10, 0.325, 0.55, 0.775, 0.90]`
- **Direction:** row 0 left→right, row 1 right→left, etc. (boustrophedon / snake pattern)
- **Row transition:** ball moves diagonally from end of row N to start of row N+1 at the same constant speed — no pause, no snap
- **X margins:** 5% from each edge (`0.05` to `0.95`), row width = `0.90` normalized units
- **Speed:** constant `SWEEP_SPEED = 0.08` normalized units/second
  - Per-row duration: `0.90 / 0.08 = 11.25 seconds`
  - Diagonal transition duration: `max(|dx|, |dy|) / SWEEP_SPEED` (both axes move simultaneously, total time = slowest axis)
  - Total estimated duration: `5 × 11.25 + 4 × transition_time ≈ 60–65 seconds`
  - Progress bar uses `elapsed / total_estimated_duration` where `total_estimated_duration` is pre-computed before the loop starts by summing all row and transition durations
- **Ball appearance:** solid green circle, radius 18px, white outline — no pulsing animation during sweep (pulse loop is stopped before sweep starts via `self._pulse_active = False`)

### Data collection

- Collected at every tick (~30fps, `dt = 0.033s`) while ball moves
- Each sample: `(gaze_norm_x, gaze_norm_y, ball_norm_x, ball_norm_y)`
- Only kept when `gaze_tracker.get_gaze()` returns non-None
- Expected yield: ~500–800 valid samples for a cooperative user
- Minimum to attempt fit: `SWEEP_MIN_SAMPLES = 30`

### Correction model

2D polynomial degree-2 regression mapping **raw normalized gaze → absolute desktop pixels**, fit separately for X and Y:

```
features(gx, gy) = [1, gx, gy, gx², gx·gy, gy²]   (6 features)

x_pixel = dot(features(gx, gy), coeffs_x)
y_pixel = dot(features(gx, gy), coeffs_y)
```

Ground truth for each sample: `(l + ball_norm_x * monitor_w, t + ball_norm_y * monitor_h)` in absolute desktop pixels.

Fit via `numpy.linalg.lstsq` — no sklearn needed.

### UX flow

1. After Phase 2 completes for a monitor, `run_calibration` calls `_run_sweep`
2. `_run_sweep` calls `session.show_sweep(monitor, label)` which enqueues `"show_sweep"`
3. **Intro screen** appears (canvas fully replaced):
   - Black background, centered text: "Fase de varredura — siga a bolinha com os olhos."
   - Sub-text: "ENTER para iniciar  |  ESC para pular"
   - Monitor name label in green
   - Empty progress bar at bottom (0%)
   - Ball is NOT shown yet — canvas is instruction-only
   - `<Return>` / `<KP_Enter>` bound to `_on_sweep_start` (sets `_start_event`)
   - `<Escape>` bound to `_on_sweep_skip` (sets `_sweep_skip` + sets `_start_event` to unblock wait)
   - `show_sweep` clears both `_start_event` and `_sweep_skip` before displaying
4. `_run_sweep` calls `session.wait_for_sweep_start()` — blocks until ENTER or ESC
5. If ESC: `session.sweep_done()` hides canvas, `_run_sweep` returns `False`
6. If ENTER: ball sweep begins. Each tick calls `session.sweep_ball(px_abs, py_abs, progress)` which:
   - Deletes previous ball tag `"sweep_dot"` and draws new circle at canvas coords `(px_abs - monitor_left, py_abs - monitor_top)` (converted from absolute to canvas-relative internally)
   - Updates progress bar fill width
   - ESC binding remains active during sweep — `_on_sweep_skip` can still fire
7. When all rows complete (or ESC pressed): `_run_sweep` calls `session.sweep_done()` internally — canvas hides. `_run_sweep` returns `True` if poly fit succeeded, `False` if skipped/failed.
8. `run_calibration` loop structure after this change:
   ```python
   self._homographies[mid] = H
   self._run_refinement(session, monitor, gaze_tracker)  # hides canvas at end
   self._run_sweep(session, monitor, gaze_tracker)        # hides canvas at end via sweep_done
   session.hide()  # ← REMOVED: both phases now manage their own hide
   ```
   The existing `session.hide()` call (currently after `_run_refinement`) is **removed** from `run_calibration`. Each phase (`_run_refinement` and `_run_sweep`) is responsible for hiding the canvas when it finishes.

### ESC binding lifecycle

- `<Escape>` is bound in `_do_show_sweep` and remains active for the duration of the sweep
- After `sweep_done` hides the canvas, no further key events are processed (canvas withdrawn)
- `<Escape>` is NOT bound during Phase 1 or Phase 2 — only during the sweep phase
- If a subsequent monitor is calibrated, `show_sweep` clears `_sweep_skip` before binding again

### Changes to calibration.py

| What | Detail |
|------|--------|
| New `_TkCalibrationSession` event | `_sweep_skip = threading.Event()` — set when user presses ESC |
| New queue command `"show_sweep"` | Shows intro screen; clears `_start_event` + `_sweep_skip`; binds ENTER + ESC |
| New queue command `"sweep_ball"` | Draws ball at canvas-relative coords; updates progress bar |
| New queue command `"sweep_done"` | Withdraws canvas |
| New public methods | `show_sweep(monitor, label)`, `sweep_ball(px_abs, py_abs, progress)`, `sweep_done()`, `wait_for_sweep_start()`, `sweep_skipped() → bool` — returns `self._sweep_skip.is_set()` without clearing it |
| New `Calibration` attribute | `self._poly_corrections = {}` — `monitor_id → (coeffs_x, coeffs_y)` numpy arrays |
| New module-level helpers | `_poly_features(gx, gy) → np.ndarray`, `_fit_poly(gaze_pts, target_x, target_y) → (coeffs_x, coeffs_y)` |
| New `Calibration` method | `_run_sweep(session, monitor, gaze_tracker) → bool` |
| Modified `Calibration.apply` | Check `_poly_corrections` first (full mapping from raw gaze); then `_corrections`; then raw homography |
| Modified `Calibration.save` | Add `"poly_corrections"` key |
| Modified `Calibration.load` | Load `"poly_corrections"` if present; `= {}` in old-format branch |
| Modified `Calibration.run_calibration` | Call `_run_sweep` after `_run_refinement`; remove existing `session.hide()` — each phase hides its own canvas |

### Changes to config.py

```python
SWEEP_SPEED       = 0.08   # normalized units/second — ball movement speed
SWEEP_MIN_SAMPLES = 30     # minimum valid samples to attempt poly fit
# SWEEP_ROWS is not a config constant — 5 rows, Y positions = np.linspace(0.10, 0.90, 5)
```

---

## Correction priority in apply() — full pseudocode

Phase 1 (homography) is always a **prerequisite** for Phase 2 and Phase 3 — `run_calibration` only calls `_run_refinement` and `_run_sweep` after a valid homography has been stored. Therefore `_poly_corrections[mid]` can only exist when `_homographies[mid]` also exists. The `H is None` guard covers both paths safely.

```python
def apply(self, monitor_id, x_norm, y_norm, _skip_correction=False):
    H = self._homographies.get(monitor_id)
    if H is None:
        return None  # safe: poly only exists when H also exists

    if not _skip_correction and monitor_id in self._poly_corrections:
        # Poly replaces full mapping — raw gaze → pixels directly, H not used
        coeffs_x, coeffs_y = self._poly_corrections[monitor_id]
        feat = _poly_features(x_norm, y_norm)
        x = float(np.dot(feat, coeffs_x))
        y = float(np.dot(feat, coeffs_y))
        return int(x), int(y)

    # Homography path
    pt = np.float32([[[x_norm, y_norm]]])
    result = cv2.perspectiveTransform(pt, H)
    x, y = result[0][0]
    if not _skip_correction and monitor_id in self._corrections:
        gx, bx, gy, by = self._corrections[monitor_id]
        x = gx * x + bx
        y = gy * y + by
    return int(x), int(y)
```

---

## calibration.json format (new)

```json
{
  "homographies": { "0": [[...], [...], [...]] },
  "corrections":  { "0": [gain_x, bias_x, gain_y, bias_y] },
  "poly_corrections": {
    "0": {
      "coeffs_x": [c0, c1, c2, c3, c4, c5],
      "coeffs_y": [c0, c1, c2, c3, c4, c5]
    }
  }
}
```

---

## Out of scope

- Adaptive speed based on gaze tracking quality
- Replay/redo of individual rows
- Configurable number of rows (fixed at 5)
