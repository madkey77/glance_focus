# Sweep Calibration Refinement — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Phase 3 sweep calibration to `calibration.py` — a slow moving ball that collects gaze samples across the full screen and fits a 2D polynomial correction model.

**Architecture:** The `_TkCalibrationSession` gains new commands (`show_sweep`, `sweep_ball`, `sweep_done`) and a skip event. `Calibration` gains `_run_sweep()` and `_poly_corrections` dict. `apply()` checks poly first, then gain/bias, then raw homography. `calibration.json` gains a `poly_corrections` key (retrocompatible).

**Tech Stack:** Python, Tkinter (existing thread model), numpy (lstsq), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-16-sweep-calibration-design.md`

---

## Chunk 1: config + poly helpers + unit tests

### Task 1: Add sweep constants to config.py

**Files:**
- Modify: `glaze-app/config.py`

- [ ] Add at the end of `config.py`:

```python
SWEEP_SPEED       = 0.08   # normalized units/second — ball speed
SWEEP_ROWS        = 5      # number of horizontal rows
SWEEP_MIN_SAMPLES = 30     # minimum valid samples to attempt poly fit
```

- [ ] Commit:
```bash
git add glaze-app/config.py
git commit -m "feat(calibration): add sweep config constants"
```

---

### Task 2: Add _poly_features helper + unit test

**Files:**
- Modify: `glaze-app/calibration.py` (top-level helper, before `_TkCalibrationSession`)
- Modify: `glaze-app/tests/test_calibration.py`

- [ ] Write the failing test first in `tests/test_calibration.py`:

```python
def test_poly_features_shape():
    from calibration import _poly_features
    import numpy as np
    f = _poly_features(0.5, 0.3)
    assert f.shape == (6,)
    assert f[0] == 1.0
    assert abs(f[1] - 0.5) < 1e-9   # gx
    assert abs(f[2] - 0.3) < 1e-9   # gy
    assert abs(f[3] - 0.25) < 1e-9  # gx²
    assert abs(f[4] - 0.15) < 1e-9  # gx·gy
    assert abs(f[5] - 0.09) < 1e-9  # gy²
```

- [ ] Run test to confirm it fails:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_poly_features_shape -v
```
Expected: ImportError or AttributeError

- [ ] Add `_poly_features` to `calibration.py` after the imports, before `_COLLECT_SECS`:

```python
def _poly_features(gx: float, gy: float) -> "np.ndarray":
    """Degree-2 polynomial feature vector: [1, gx, gy, gx², gx·gy, gy²]."""
    return np.array([1.0, gx, gy, gx * gx, gx * gy, gy * gy])
```

- [ ] Run test to confirm it passes:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_poly_features_shape -v
```

- [ ] Commit:
```bash
git add glaze-app/calibration.py glaze-app/tests/test_calibration.py
git commit -m "feat(calibration): add _poly_features helper + test"
```

---

### Task 3: Add _fit_poly helper + unit test

**Files:**
- Modify: `glaze-app/calibration.py`
- Modify: `glaze-app/tests/test_calibration.py`

- [ ] Write failing test:

```python
def test_fit_poly_identity():
    """Poly fit on perfect data should reproduce targets exactly."""
    from calibration import _poly_features, _fit_poly
    import numpy as np

    # 10 random points where target == input (identity correction)
    rng = np.random.default_rng(42)
    gaze_pts = rng.uniform(0.1, 0.9, (20, 2))
    # target_x = gaze_x * 1000 + 100  (simulates a known linear mapping)
    target_x = gaze_pts[:, 0] * 1000 + 100
    target_y = gaze_pts[:, 1] * 800  + 50

    coeffs_x, coeffs_y = _fit_poly(gaze_pts, target_x, target_y)

    # Predict on a new point
    gx, gy = 0.5, 0.5
    feat = _poly_features(gx, gy)
    pred_x = float(np.dot(feat, coeffs_x))
    pred_y = float(np.dot(feat, coeffs_y))
    assert abs(pred_x - (0.5 * 1000 + 100)) < 5
    assert abs(pred_y - (0.5 * 800  + 50))  < 5
```

- [ ] Run to confirm it fails:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_fit_poly_identity -v
```

- [ ] Add `_fit_poly` to `calibration.py` after `_poly_features`:

```python
def _fit_poly(gaze_pts: "np.ndarray", target_x: "np.ndarray",
              target_y: "np.ndarray"):
    """
    Fits degree-2 polynomial correction via least squares.

    gaze_pts: (N, 2) array of normalized gaze (gx, gy)
    target_x, target_y: (N,) arrays of ground-truth desktop coords

    Returns (coeffs_x, coeffs_y) — each a (6,) numpy array.
    """
    A = np.array([_poly_features(gx, gy) for gx, gy in gaze_pts])
    coeffs_x, _, _, _ = np.linalg.lstsq(A, target_x, rcond=None)
    coeffs_y, _, _, _ = np.linalg.lstsq(A, target_y, rcond=None)
    return coeffs_x, coeffs_y
```

- [ ] Run to confirm it passes:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_fit_poly_identity -v
```

- [ ] Commit:
```bash
git add glaze-app/calibration.py glaze-app/tests/test_calibration.py
git commit -m "feat(calibration): add _fit_poly helper + test"
```

---

### Task 4: Add poly correction to Calibration.apply + unit test

**Files:**
- Modify: `glaze-app/calibration.py` (`Calibration.__init__`, `apply`, `save`, `load`)
- Modify: `glaze-app/tests/test_calibration.py`

- [ ] Write failing test:

```python
def test_apply_uses_poly_correction():
    """apply() should use poly_corrections when available."""
    import cv2
    import numpy as np
    from calibration import Calibration, _fit_poly

    cal = Calibration.__new__(Calibration)
    src = np.float32([[0.5,0.5],[0.1,0.1],[0.9,0.1],[0.1,0.9],[0.9,0.9]])
    dst = np.float32([[960,540],[0,0],[1920,0],[0,1080],[1920,1080]])
    H, _ = cv2.findHomography(src, dst)
    cal._homographies = {0: H}
    cal._corrections  = {}

    # Fit a poly that shifts x by +200, y by +100
    rng = np.random.default_rng(0)
    gaze_pts = rng.uniform(0.05, 0.95, (50, 2))
    # Get raw homography predictions for these gaze points
    raw = [cal.apply(0, gx, gy, _skip_correction=True) for gx, gy in gaze_pts]
    target_x = np.array([r[0] + 200 for r in raw], dtype=float)
    target_y = np.array([r[1] + 100 for r in raw], dtype=float)
    coeffs_x, coeffs_y = _fit_poly(gaze_pts, target_x, target_y)
    cal._poly_corrections = {0: (coeffs_x, coeffs_y)}

    result = cal.apply(0, 0.5, 0.5)
    raw_center = cal.apply(0, 0.5, 0.5, _skip_correction=True)
    assert result is not None
    assert abs(result[0] - (raw_center[0] + 200)) < 10
    assert abs(result[1] - (raw_center[1] + 100)) < 10
```

- [ ] Run to confirm it fails:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_apply_uses_poly_correction -v
```

- [ ] Update `Calibration.__init__`:
```python
def __init__(self):
    self._homographies    = {}  # monitor_id → H (3x3 numpy)
    self._corrections     = {}  # monitor_id → (gain_x, bias_x, gain_y, bias_y)
    self._poly_corrections = {} # monitor_id → (coeffs_x, coeffs_y) numpy arrays
```

- [ ] Update `Calibration.apply` — replace the correction block:

```python
def apply(self, monitor_id, x_norm, y_norm, _skip_correction=False):
    H = self._homographies.get(monitor_id)
    if H is None:
        return None
    pt = np.float32([[[x_norm, y_norm]]])
    result = cv2.perspectiveTransform(pt, H)
    x, y = result[0][0]
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

- [ ] Run test to confirm it passes:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_apply_uses_poly_correction -v
```

- [ ] Update `Calibration.save` to include `poly_corrections`:

```python
def save(self, path=None):
    path = path or CALIBRATION_FILE
    data = {
        "homographies": {
            str(k): v.tolist()
            for k, v in self._homographies.items() if v is not None
        },
        "corrections": {
            str(k): list(v) for k, v in self._corrections.items()
        },
        "poly_corrections": {
            str(k): {"coeffs_x": v[0].tolist(), "coeffs_y": v[1].tolist()}
            for k, v in self._poly_corrections.items()
        },
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
```

- [ ] Update `Calibration.load` — add poly_corrections loading inside the `"homographies" in data` branch:

```python
self._poly_corrections = {
    int(k): (
        np.array(v["coeffs_x"]),
        np.array(v["coeffs_y"]),
    )
    for k, v in data.get("poly_corrections", {}).items()
}
```

And in the old-format branch: `self._poly_corrections = {}`

Update the log line:
```python
n_poly = len(self._poly_corrections)
print(
    f"[Calibração] Carregado de {path} "
    f"({len(self._homographies)} monitores, {n_corr} correções, {n_poly} poly)"
)
```

- [ ] Run all calibration tests:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add glaze-app/calibration.py glaze-app/tests/test_calibration.py
git commit -m "feat(calibration): poly_corrections in Calibration — apply/save/load"
```

---

## Chunk 2: Tkinter sweep UI + _run_sweep

### Task 5: Add sweep UI commands to _TkCalibrationSession

**Files:**
- Modify: `glaze-app/calibration.py` (`_TkCalibrationSession`)

The session needs three new queue commands and one new event:

- `"show_sweep"` → shows intro screen ("ENTER para iniciar | ESC para pular")
- `"sweep_ball"` → moves ball to `(px, py)` absolute coords, updates progress bar
- `"sweep_done"` → hides canvas

And one new threading.Event: `_sweep_skip` — set when user presses ESC during sweep intro or sweep.

- [ ] In `_TkCalibrationSession.__init__`, add after `self._start_event`:
```python
self._sweep_skip = threading.Event()
```

- [ ] In `_process_queue`, add new handlers inside the `while True` block:
```python
elif cmd == "show_sweep":
    self._do_show_sweep(*args)
elif cmd == "sweep_ball":
    self._do_sweep_ball(*args)
elif cmd == "sweep_done":
    self._root.withdraw()
```

- [ ] Add `_do_show_sweep(self, monitor, label)` method:

```python
def _do_show_sweep(self, monitor, label):
    """Shows sweep intro screen. ENTER starts, ESC skips."""
    root = self._root
    canvas = self._canvas

    l, t = monitor["left"], monitor["top"]
    w = monitor["right"] - l
    h = monitor["bottom"] - t
    root.geometry(f"{w}x{h}+{l}+{t}")
    root.deiconify()
    root.lift()
    root.focus_force()

    canvas.configure(width=w, height=h)
    canvas.delete("all")

    canvas.create_text(w // 2, h // 2 - 30,
                       text="Fase de varredura — siga a bolinha com os olhos.",
                       fill="white", font=("Arial", 18))
    canvas.create_text(w // 2, h // 2 + 10,
                       text="ENTER para iniciar  |  ESC para pular",
                       fill="#888888", font=("Arial", 14))
    canvas.create_text(w // 2, h // 2 + 50,
                       text=label, fill="#00FF88", font=("Arial", 12))

    # Barra de progresso (começa vazia, preenchida por sweep_ball)
    bar_w, bar_h = 400, 12
    bx = w // 2 - bar_w // 2
    by = h - 60
    canvas.create_rectangle(bx, by, bx + bar_w, by + bar_h,
                             outline="#555555", fill="#222222")
    self._bar_rect = canvas.create_rectangle(bx, by, bx, by + bar_h,
                                              outline="", fill="#00FF88")
    self._bar_bx = bx
    self._bar_w  = bar_w
    self._bar_by = by
    self._bar_h  = bar_h

    self._sweep_skip.clear()
    self._start_event.clear()
    self._pulse_active = False  # no pulsing dot until sweep starts

    root.bind("<Return>",   self._on_sweep_start)
    root.bind("<KP_Enter>", self._on_sweep_start)
    root.bind("<Escape>",   self._on_sweep_skip)
```

- [ ] Add `_on_sweep_start` and `_on_sweep_skip` handlers:

```python
def _on_sweep_start(self, event=None):
    self._start_event.set()

def _on_sweep_skip(self, event=None):
    self._sweep_skip.set()
    self._start_event.set()  # unblocks wait_for_start if waiting
```

- [ ] Add `_do_sweep_ball(self, px, py, progress)` method:

```python
def _do_sweep_ball(self, px, py, progress):
    """Moves ball to absolute position (px, py) and updates progress bar."""
    canvas = self._canvas
    canvas.delete("sweep_dot")
    r = 18
    canvas.create_oval(px - r, py - r, px + r, py + r,
                       fill="#00FF88", outline="white", width=2,
                       tags="sweep_dot")
    # Update progress bar
    fill_w = int(self._bar_w * min(1.0, max(0.0, progress)))
    canvas.coords(self._bar_rect,
                  self._bar_bx, self._bar_by,
                  self._bar_bx + fill_w, self._bar_by + self._bar_h)
```

- [ ] Add public thread-safe methods:

```python
def show_sweep(self, monitor, label):
    """Enfileira tela de introdução da varredura (thread-safe)."""
    self._cmd_queue.put(("show_sweep", (monitor, label)))

def sweep_ball(self, px, py, progress):
    """Move a bolinha para (px, py) e atualiza progresso (thread-safe)."""
    self._cmd_queue.put(("sweep_ball", (px, py, progress)))

def sweep_done(self):
    """Esconde o canvas ao fim da varredura (thread-safe)."""
    self._cmd_queue.put(("sweep_done", ()))

def wait_for_sweep_start(self):
    """Bloqueia até ENTER ou ESC na tela de intro da varredura."""
    self._start_event.wait()
    self._start_event.clear()

def sweep_skipped(self):
    """Retorna True se o usuário pressionou ESC."""
    return self._sweep_skip.is_set()
```

- [ ] Smoke-test manually: run `python main.py`, trigger calibration with `Ctrl+Alt+C`, verify it still works normally (sweep not wired yet)

- [ ] Commit:
```bash
git add glaze-app/calibration.py
git commit -m "feat(calibration): sweep UI commands in _TkCalibrationSession"
```

---

### Task 6: Implement _run_sweep in Calibration

**Files:**
- Modify: `glaze-app/calibration.py` (`Calibration._run_sweep`, `run_calibration`)

- [ ] Add `_run_sweep` method to `Calibration` (after `_run_refinement`):

```python
def _run_sweep(self, session, monitor, gaze_tracker) -> bool:
    """
    Phase 3 calibration: slow horizontal ball sweep.
    Collects gaze samples across full screen, fits 2D poly correction.
    Returns True if poly fit succeeded and was stored, False if skipped/failed.
    """
    from config import SWEEP_SPEED, SWEEP_ROWS, SWEEP_MIN_SAMPLES

    mid  = monitor["id"]
    l, t = monitor["left"], monitor["top"]
    w    = monitor["right"]  - l
    h    = monitor["bottom"] - t

    # Show intro and wait for ENTER or ESC
    session.show_sweep(monitor, f"Monitor: {monitor['name']}")
    session.wait_for_sweep_start()
    if session.sweep_skipped():
        print(f"  [Varredura] Monitor {mid} — pulado pelo usuário.")
        session.sweep_done()
        return False

    # Compute sweep path
    # Each row: from x=0.05 to x=0.95 (or reverse), at y = row position
    row_ys   = [0.10, 0.30, 0.50, 0.70, 0.90][:SWEEP_ROWS]
    x_margin = 0.05
    row_w    = 1.0 - 2 * x_margin  # 0.90 normalized units

    # Total path length in normalized units:
    # SWEEP_ROWS rows * row_w  +  (SWEEP_ROWS-1) diagonal transitions
    # Diagonal transition: from (end_x, row_y) to (start_x_next, next_row_y)
    # Both axes move simultaneously at SWEEP_SPEED — duration = max(dx, dy)/SWEEP_SPEED
    # For simplicity, treat total duration as rows * (row_w / SWEEP_SPEED) * 1.15 (15% for transitions)
    row_duration  = row_w / SWEEP_SPEED          # seconds per row
    total_duration = row_duration * SWEEP_ROWS * 1.15

    print(f"\n[Varredura] Monitor {mid} — iniciando (~{total_duration:.0f}s)")

    samples     = []  # list of (gaze_norm_x, gaze_norm_y, ball_norm_x, ball_norm_y)
    t_start     = time.time()
    dt          = 0.033   # ~30fps tick

    # Current ball position in normalized coords
    ball_x = x_margin
    ball_y = row_ys[0]
    row_idx = 0
    going_right = True

    while True:
        now     = time.time()
        elapsed = now - t_start
        progress = min(1.0, elapsed / total_duration)

        # Compute target x for this row
        target_x = (1.0 - x_margin) if going_right else x_margin

        # Move ball toward target_x at SWEEP_SPEED
        dx       = target_x - ball_x
        step     = SWEEP_SPEED * dt
        if abs(dx) <= step:
            # Row complete — transition to next row
            ball_x = target_x
            if row_idx + 1 < len(row_ys):
                row_idx    += 1
                going_right = not going_right
                # Move to start of next row (diagonal, same speed)
                next_x = x_margin if going_right else (1.0 - x_margin)
                next_y = row_ys[row_idx]
                # Transition: interpolate both axes simultaneously
                tdx = abs(next_x - ball_x)
                tdy = abs(next_y - ball_y)
                t_trans = max(tdx, tdy) / SWEEP_SPEED
                t_trans_start = time.time()
                sx, sy = ball_x, ball_y
                while True:
                    te = time.time() - t_trans_start
                    if te >= t_trans:
                        ball_x, ball_y = next_x, next_y
                        break
                    frac   = te / t_trans
                    ball_x = sx + (next_x - sx) * frac
                    ball_y = sy + (next_y - sy) * frac
                    px_abs = int(l + ball_x * w)
                    py_abs = int(t + ball_y * h)
                    session.sweep_ball(px_abs, py_abs, min(1.0, (time.time() - t_start) / total_duration))
                    g = gaze_tracker.get_gaze()
                    if g is not None:
                        samples.append((g[0], g[1], ball_x, ball_y))
                    time.sleep(dt)
            else:
                # All rows done
                break
        else:
            ball_x += step * (1 if dx > 0 else -1)

        px_abs = int(l + ball_x * w)
        py_abs = int(t + ball_y * h)
        session.sweep_ball(px_abs, py_abs, progress)

        g = gaze_tracker.get_gaze()
        if g is not None:
            samples.append((g[0], g[1], ball_x, ball_y))

        time.sleep(dt)

    session.sweep_done()
    print(f"  [Varredura] {len(samples)} amostras coletadas.")

    if len(samples) < SWEEP_MIN_SAMPLES:
        print(f"  [Varredura] Amostras insuficientes ({len(samples)} < {SWEEP_MIN_SAMPLES}) — pulando poly fit.")
        return False

    # Fit polynomial
    gaze_pts = np.array([(s[0], s[1]) for s in samples])
    # Get raw homography predictions for each gaze point
    raw_preds = [self.apply(mid, s[0], s[1], _skip_correction=True) for s in samples]
    # Filter out None
    valid = [(gaze_pts[i], raw_preds[i], samples[i][2], samples[i][3])
             for i in range(len(samples)) if raw_preds[i] is not None]
    if len(valid) < SWEEP_MIN_SAMPLES:
        print(f"  [Varredura] Predições válidas insuficientes — pulando.")
        return False

    gaze_arr  = np.array([v[0] for v in valid])
    # Ground truth: ball position in absolute desktop coords
    target_x_arr = np.array([l + v[2] * w for v in valid])
    target_y_arr = np.array([t + v[3] * h for v in valid])

    coeffs_x, coeffs_y = _fit_poly(gaze_arr, target_x_arr, target_y_arr)
    self._poly_corrections[mid] = (coeffs_x, coeffs_y)
    print(f"  [Varredura] Monitor {mid} — poly fit OK ({len(valid)} amostras válidas).")
    return True
```

- [ ] Wire `_run_sweep` into `run_calibration` and **remove the existing `session.hide()` call** — each phase now hides its own canvas:

```python
# Before (current code):
self._homographies[mid] = H
print(f"[Calibração] Monitor {mid} calibrado.")
self._run_refinement(session, monitor, gaze_tracker)
session.hide()  # ← REMOVE THIS LINE

# After:
self._homographies[mid] = H
print(f"[Calibração] Monitor {mid} calibrado.")
self._run_refinement(session, monitor, gaze_tracker)  # hides its own canvas
self._run_sweep(session, monitor, gaze_tracker)        # hides its own canvas
```

- [ ] Run all calibration tests to confirm nothing broke:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py -v
```
Expected: all pass

- [ ] Commit:
```bash
git add glaze-app/calibration.py
git commit -m "feat(calibration): _run_sweep — moving ball poly fit phase 3"
```

---

## Chunk 3: Integration test + cleanup

### Task 7: Integration smoke test for full calibration flow

**Files:**
- Modify: `glaze-app/tests/test_calibration.py`

- [ ] Add save/load roundtrip test for poly_corrections:

```python
def test_save_load_poly_corrections(tmp_path):
    import cv2, numpy as np
    from calibration import Calibration, _fit_poly

    cal = Calibration.__new__(Calibration)
    src = np.float32([[0.5,0.5],[0.1,0.1],[0.9,0.1],[0.1,0.9],[0.9,0.9]])
    dst = np.float32([[960,540],[0,0],[1920,0],[0,1080],[1920,1080]])
    H, _ = cv2.findHomography(src, dst)
    cal._homographies     = {0: H}
    cal._corrections      = {}
    rng = np.random.default_rng(7)
    gaze_pts = rng.uniform(0.05, 0.95, (40, 2))
    raw = [cal.apply(0, gx, gy, _skip_correction=True) for gx, gy in gaze_pts]
    tx  = np.array([r[0] for r in raw], dtype=float)
    ty  = np.array([r[1] for r in raw], dtype=float)
    cx, cy = _fit_poly(gaze_pts, tx, ty)
    cal._poly_corrections = {0: (cx, cy)}

    path = str(tmp_path / "cal.json")
    cal.save(path)

    cal2 = Calibration.__new__(Calibration)
    cal2._homographies     = {}
    cal2._corrections      = {}
    cal2._poly_corrections = {}
    cal2.load(path)

    assert 0 in cal2._poly_corrections
    assert cal2._poly_corrections[0][0].shape == (6,)
    # Predictions should be close
    r1 = cal.apply(0, 0.5, 0.5)
    r2 = cal2.apply(0, 0.5, 0.5)
    assert abs(r1[0] - r2[0]) < 2
    assert abs(r1[1] - r2[1]) < 2
```

- [ ] Run:
```bash
cd glaze-app && python -m pytest tests/test_calibration.py::test_save_load_poly_corrections -v
```
Expected: PASS

- [ ] Run full test suite:
```bash
cd glaze-app && python -m pytest tests/ -v
```
Expected: all pass

- [ ] Commit:
```bash
git add glaze-app/tests/test_calibration.py
git commit -m "test(calibration): save/load roundtrip for poly_corrections"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] In the Architecture section, update the `calibration.py` description to mention the 3-phase flow and poly correction:

```
### `calibration.py`
Three-phase calibration per monitor:
1. **Homography** — 5 static points → `cv2.findHomography`
2. **Gain/Bias** — 3 validation points → linear correction per axis
3. **Sweep** (optional) — slow moving ball → 2D polynomial correction (degree 2, `numpy.linalg.lstsq`)

`apply()` priority: poly_corrections → gain/bias → raw homography.
Persists to `calibration.json` (retrocompatible with older formats).
```

- [ ] Commit:
```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md — 3-phase calibration description"
```

---

## Windows test instructions

After all tasks complete, test on Windows:

```
cd E:\projetos\glaze\glaze-app
python main.py
```

1. Press `Ctrl+Alt+C` to start calibration
2. Complete Phase 1 (5 green dots) and Phase 2 (3 orange dots) as usual
3. Phase 3 intro screen appears — press **ENTER** to start sweep
4. Follow the green ball slowly across the screen with your eyes
5. Ball completes all rows → canvas hides automatically
6. Tracking resumes — test accuracy improvement with `Ctrl+Alt+B` overlay

To skip Phase 3: press **ESC** on the intro screen.

Check terminal for:
```
[Varredura] Monitor 0 — iniciando (~65s)
[Varredura] 523 amostras coletadas.
[Varredura] Monitor 0 — poly fit OK (523 amostras válidas).
```
