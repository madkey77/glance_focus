# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Glaze** — Eye-tracking window focus for Windows. Uses a webcam + MediaPipe FaceLandmarker to detect where the user is looking and automatically brings that window to the foreground via `SetForegroundWindow`.

Code lives in `glaze-app/`. The project runs on **Windows natively** but is **developed from WSL** (editing files at `/mnt/e/projetos/glaze/`). All Python commands must be run on the Windows side.

## Running the App (Windows)

```
cd E:\projetos\glaze\glaze-app
python main.py
```

Requires `face_landmarker.task` in `glaze-app/` (MediaPipe model file — not in git).

## Running Tests (Windows)

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/ -v
```

Run a single test file:
```
python -m pytest tests/test_calibration.py -v
```

## Installing Dependencies (Windows)

```
cd E:\projetos\glaze\glaze-app
pip install -r requirements.txt
```

Dependencies: `opencv-python`, `numpy`, `mediapipe`, `scipy`, `pynput`, `pywin32`.

## Architecture

Pipeline executed at ~30fps in the main loop:

```
webcam → GazeTracker → Calibration → MonitorLayout → QuadrantMapper → FocusController
```

- **`gaze_tracker.py`** — Runs MediaPipe FaceLandmarker in a background thread. Combines `facial_transformation_matrix` (head pose) with iris offset deviation to compute a normalized gaze `(x, y)` in `[0..1]`. Exposes `get_gaze() → (x,y) | None`.

- **`calibration.py`** — 5-point calibration per monitor (center first, then 4 corners). Uses a dedicated Tkinter thread (`_TkCalibrationSession`) to display calibration UI safely — never call Tk from a non-Tk thread. Computes a 2D homography (OpenCV `findHomography`) mapping normalized gaze → absolute desktop pixels. Persists to `calibration.json`.

- **`monitor_layout.py`** — Enumerates Windows monitors via `win32api.EnumDisplayMonitors`. Divides each monitor into 4 quadrants. Exposes `get_zone(x_abs, y_abs)`.

- **`quadrant_mapper.py`** — Lists visible, non-minimized windows ≥200×200px via `win32gui.EnumWindows`. Maps each quadrant to its dominant window (largest overlap area). Updates every 500ms.

- **`focus_controller.py`** — `SaccadeDetector` confirms a gaze zone change after 150ms of stability before triggering `SetForegroundWindow`. `OverlayBorder` draws a colored border around the focused window via a transparent always-on-top Tkinter window (runs in its own thread). `_force_foreground` uses the ALT-key trick + `AttachThreadInput` fallback to bypass Windows foreground lock.

- **`main.py`** — Orchestrates everything. Hotkeys via `pynput` (passive listener, doesn't suppress system shortcuts):
  - `Ctrl+Alt+G` — toggle tracking on/off
  - `Ctrl+Alt+B` — toggle overlay border
  - `Ctrl+Alt+C` — start re-calibration
  - `Ctrl+Alt+Q` — quit

- **`config.py`** — All tunable parameters (`CAMERA_INDEX`, `SACCADE_STABLE_MS`, `GAZE_SMOOTH_FRAMES`, `ZONE_LAYOUT`, etc.).

## Key Design Constraints

- **Tkinter must run in a dedicated thread.** Two Tk windows exist: one in `calibration.py` (`_TkCalibrationSession`) and one in `focus_controller.py` (`OverlayBorder`). Commands to them must go through a `queue.Queue` + `root.after()` — never call Tk methods from other threads (causes `Tcl_AsyncDelete` crash).

- **`face_landmarker.task` must be present** next to `main.py`. It's not committed to git. Download from the MediaPipe models storage.

- **Hotkey normalization:** `pynput` delivers control characters (e.g., `\x07`) when Ctrl is held. The listener normalizes using `vk` codes (VK 65–90 → lowercase a–z) when `key.char` is a non-printable character.

- **`SetForegroundWindow` workaround:** Windows 10/11 blocks focus changes from background processes. `_apply_foreground_lock_timeout()` sets `ForegroundLockTimeout=0` at startup, and `_force_foreground()` injects a synthetic ALT keypress to acquire "last input event" permission.

## Tests

Unit tests in `glaze-app/tests/`. They mock Win32 dependencies and don't require a webcam or display. Tests cover: calibration homography, saccade detection, monitor layout, quadrant mapping.
