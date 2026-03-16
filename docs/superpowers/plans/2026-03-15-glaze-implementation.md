# Glaze — Eye-Tracking Window Focus — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python program that uses a webcam to detect which Windows window the user is looking at and automatically brings it to focus using eye-tracking and saccade detection.

**Architecture:** A modular pipeline running at ~30fps: `GazeTracker` captures raw gaze coordinates via MediaPipe FaceMesh in a background thread; `Calibration` maps normalized gaze to absolute desktop pixels via homography; `MonitorLayout` + `QuadrantMapper` resolve which window is dominant in the gaze quadrant; `FocusController` detects saccades and calls `SetForegroundWindow`.

**Tech Stack:** Python 3.11+, MediaPipe, OpenCV, scipy, pywin32 (win32api/win32gui), keyboard, numpy

---

## File Map

| File | Responsibility |
|------|---------------|
| `glaze-app/config.py` | All tuneable constants in one place |
| `glaze-app/gaze_tracker.py` | Webcam capture + MediaPipe FaceMesh → normalized (x,y) gaze |
| `glaze-app/calibration.py` | 5-point calibration per monitor, homography, load/save JSON |
| `glaze-app/monitor_layout.py` | Enumerate monitors, names, positions, quadrant computation |
| `glaze-app/quadrant_mapper.py` | Enumerate visible windows, compute dominant window per quadrant |
| `glaze-app/focus_controller.py` | Saccade detection, SetForegroundWindow, overlay border |
| `glaze-app/main.py` | Loop principal, hotkeys, orchestration |
| `glaze-app/requirements.txt` | Python dependencies |
| `glaze-app/tests/test_monitor_layout.py` | Unit tests for quadrant math |
| `glaze-app/tests/test_quadrant_mapper.py` | Unit tests for window intersection logic |
| `glaze-app/tests/test_focus_controller.py` | Unit tests for saccade detection state machine |
| `glaze-app/tests/test_calibration.py` | Unit tests for homography apply/load/save |

---

## Chunk 1: Projeto base + config + monitor layout

### Task 1: Estrutura de pastas e dependências

**Files:**
- Create: `glaze-app/requirements.txt`
- Create: `glaze-app/config.py`
- Create: `glaze-app/tests/__init__.py`

- [x] **Step 1: Criar pasta glaze-app**

No WSL:
```bash
mkdir -p /mnt/e/projetos/glaze/glaze-app/tests
touch /mnt/e/projetos/glaze/glaze-app/tests/__init__.py
```

- [x] **Step 2: Criar requirements.txt**

```
opencv-python
numpy
mediapipe
scipy
keyboard
pywin32
```

- [x] **Step 3: Criar config.py**

```python
# glaze-app/config.py

CAMERA_INDEX = 0
CAPTURE_WIDTH = 480
CAPTURE_HEIGHT = 360

SACCADE_STABLE_MS = 150       # ms de estabilidade para confirmar saccade
GAZE_SMOOTH_FRAMES = 10       # janela do filtro de média móvel
ZONE_LAYOUT = "2x2"           # "2x2" | "4x1" | "1x4"
QUADRANT_UPDATE_MS = 500      # frequência de atualização das janelas dominantes
MIN_WINDOW_SIZE = 200         # px — tamanho mínimo de janela considerada

CALIBRATION_FILE = "calibration.json"
CALIBRATION_SAMPLES = 5       # amostras coletadas por ponto de calibração
```

- [x] **Step 4: Instalar dependências no Windows**

```
## O que fazer no Windows

1. Abra o PowerShell ou Prompt de Comando como Administrador
2. Execute:
   pip install opencv-python numpy mediapipe scipy keyboard pywin32
3. Verifique: python -c "import cv2, mediapipe, win32gui; print('OK')"
   Esperado: OK
```

---

### Task 2: monitor_layout.py — enumeração e quadrantes

**Files:**
- Create: `glaze-app/monitor_layout.py`
- Create: `glaze-app/tests/test_monitor_layout.py`

- [x] **Step 1: Escrever testes primeiro**

```python
# glaze-app/tests/test_monitor_layout.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from monitor_layout import MonitorLayout, ZoneLayout, get_zones_for_monitor

def make_monitor(left, top, right, bottom):
    return {"left": left, "top": top, "right": right, "bottom": bottom,
            "name": "TEST", "id": 0}

def test_quadrant_2x2_top_left():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    # Ponto no quadrante superior esquerdo
    zone = _find_zone(zones, 100, 100)
    assert zone["quadrant"] == 0

def test_quadrant_2x2_bottom_right():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    zone = _find_zone(zones, 1800, 900)
    assert zone["quadrant"] == 3

def test_quadrant_4x1_leftmost():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "4x1")
    zone = _find_zone(zones, 100, 500)
    assert zone["quadrant"] == 0

def test_quadrant_4x1_rightmost():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "4x1")
    zone = _find_zone(zones, 1800, 500)
    assert zone["quadrant"] == 3

def test_point_outside_monitor_returns_none():
    m = make_monitor(0, 0, 1920, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    zone = _find_zone(zones, 2000, 500)
    assert zone is None

def test_second_monitor_offset():
    m = make_monitor(1920, 0, 3840, 1080)
    zones = get_zones_for_monitor(m, "2x2")
    # Ponto no monitor 2, superior esquerdo (coordenada absoluta)
    zone = _find_zone(zones, 2000, 100)
    assert zone["quadrant"] == 0

def _find_zone(zones, x, y):
    for z in zones:
        if z["left"] <= x < z["right"] and z["top"] <= y < z["bottom"]:
            return z
    return None
```

- [x] **Step 2: Rodar testes — confirmar que falham**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_monitor_layout.py -v
   Esperado: ImportError ou ModuleNotFoundError (monitor_layout ainda não existe)
```

- [x] **Step 3: Implementar monitor_layout.py**

```python
# glaze-app/monitor_layout.py
import win32api
import win32con

def get_monitors():
    """
    Retorna lista de monitores com nome, posição e dimensões no desktop virtual.
    Cada monitor: {"id", "name", "left", "top", "right", "bottom"}
    """
    monitors = []
    raw = win32api.EnumDisplayMonitors(None, None)
    for i, (hmon, _, rect) in enumerate(raw):
        info = win32api.GetMonitorInfo(hmon)
        left, top, right, bottom = info["Monitor"]
        # Nome legível via EnumDisplayDevices
        try:
            device = win32api.EnumDisplayDevices(info["Device"], 0)
            name = device.DeviceString
        except Exception:
            name = info["Device"]
        monitors.append({
            "id": i,
            "name": name,
            "device": info["Device"],
            "left": left, "top": top,
            "right": right, "bottom": bottom,
        })
    return monitors


def get_zones_for_monitor(monitor, layout="2x2"):
    """
    Divide um monitor em zonas conforme o layout.
    Retorna lista de dicts: {"monitor_id", "quadrant", "left", "top", "right", "bottom"}

    Layouts suportados:
      "2x2" — 4 quadrantes (2 colunas x 2 linhas)
      "4x1" — 4 faixas verticais
      "1x4" — 4 faixas horizontais
    """
    l, t, r, b = monitor["left"], monitor["top"], monitor["right"], monitor["bottom"]
    w = r - l
    h = b - t
    mid = monitor.get("id", 0)
    zones = []

    if layout == "2x2":
        halfw, halfh = w // 2, h // 2
        grid = [
            (0, l,        t,        l+halfw, t+halfh),
            (1, l+halfw,  t,        r,       t+halfh),
            (2, l,        t+halfh,  l+halfw, b),
            (3, l+halfw,  t+halfh,  r,       b),
        ]
    elif layout == "4x1":
        qw = w // 4
        grid = [(i, l+i*qw, t, l+(i+1)*qw if i < 3 else r, b) for i in range(4)]
    elif layout == "1x4":
        qh = h // 4
        grid = [(i, l, t+i*qh, r, t+(i+1)*qh if i < 3 else b) for i in range(4)]
    else:
        raise ValueError(f"Layout desconhecido: {layout}")

    for quadrant, zl, zt, zr, zb in grid:
        zones.append({
            "monitor_id": mid,
            "quadrant": quadrant,
            "left": zl, "top": zt,
            "right": zr, "bottom": zb,
        })
    return zones


class MonitorLayout:
    def __init__(self, layout="2x2"):
        self.layout = layout
        self.monitors = get_monitors()
        self.zones = []
        for m in self.monitors:
            self.zones.extend(get_zones_for_monitor(m, layout))

    def get_zone(self, x_abs, y_abs):
        """Retorna zona (dict) onde o ponto (x_abs, y_abs) cai, ou None."""
        for z in self.zones:
            if z["left"] <= x_abs < z["right"] and z["top"] <= y_abs < z["bottom"]:
                return z
        return None

    def get_monitor_names(self):
        return [(m["id"], m["name"], m["device"]) for m in self.monitors]
```

- [x] **Step 4: Rodar testes — confirmar que passam**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_monitor_layout.py -v
   Esperado: 6 testes passando (PASSED)
```

- [x] **Step 5: Commit**

```bash
cd /mnt/e/projetos/glaze/glaze-app
git init
git add config.py monitor_layout.py requirements.txt tests/
git commit -m "feat: add config, monitor_layout with zone computation"
```

---

## Chunk 2: QuadrantMapper — janelas dominantes

### Task 3: quadrant_mapper.py — janela dominante por zona

**Files:**
- Create: `glaze-app/quadrant_mapper.py`
- Create: `glaze-app/tests/test_quadrant_mapper.py`

- [x] **Step 1: Escrever testes**

```python
# glaze-app/tests/test_quadrant_mapper.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from quadrant_mapper import compute_intersection_area, find_dominant_window

def make_rect(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}

def test_intersection_full_overlap():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(0, 0, 960, 540)
    assert compute_intersection_area(zone, win) == 960 * 540

def test_intersection_partial():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(480, 0, 1440, 540)
    assert compute_intersection_area(zone, win) == 480 * 540

def test_intersection_no_overlap():
    zone = make_rect(0, 0, 960, 540)
    win  = make_rect(1000, 0, 1920, 540)
    assert compute_intersection_area(zone, win) == 0

def test_find_dominant_picks_largest_overlap():
    zone = make_rect(0, 0, 960, 540)
    windows = [
        {"hwnd": 1, "title": "A", "left": 0,   "top": 0, "right": 500, "bottom": 540, "z_order": 0},
        {"hwnd": 2, "title": "B", "left": 0,   "top": 0, "right": 900, "bottom": 540, "z_order": 1},
    ]
    dominant = find_dominant_window(zone, windows)
    assert dominant["hwnd"] == 2

def test_find_dominant_tiebreak_z_order():
    zone = make_rect(0, 0, 960, 540)
    windows = [
        {"hwnd": 1, "title": "A", "left": 0, "top": 0, "right": 960, "bottom": 540, "z_order": 2},
        {"hwnd": 2, "title": "B", "left": 0, "top": 0, "right": 960, "bottom": 540, "z_order": 0},
    ]
    dominant = find_dominant_window(zone, windows)
    # z_order menor = mais ao topo
    assert dominant["hwnd"] == 2

def test_find_dominant_returns_none_no_windows():
    zone = make_rect(0, 0, 960, 540)
    assert find_dominant_window(zone, []) is None
```

- [x] **Step 2: Rodar testes — confirmar que falham**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_quadrant_mapper.py -v
   Esperado: ImportError
```

- [x] **Step 3: Implementar quadrant_mapper.py**

```python
# glaze-app/quadrant_mapper.py
import time
import win32gui
import win32con
from config import MIN_WINDOW_SIZE, QUADRANT_UPDATE_MS


def compute_intersection_area(rect_a, rect_b):
    """Área de interseção entre dois retângulos (dicts com left/top/right/bottom)."""
    ix1 = max(rect_a["left"],  rect_b["left"])
    iy1 = max(rect_a["top"],   rect_b["top"])
    ix2 = min(rect_a["right"], rect_b["right"])
    iy2 = min(rect_a["bottom"],rect_b["bottom"])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0
    return (ix2 - ix1) * (iy2 - iy1)


def find_dominant_window(zone, windows):
    """
    Retorna a janela com maior área de interseção com a zona.
    Em caso de empate, prioriza z_order menor (mais ao topo).
    Retorna None se lista vazia.
    """
    if not windows:
        return None
    best = max(
        windows,
        key=lambda w: (compute_intersection_area(zone, w), -w["z_order"])
    )
    area = compute_intersection_area(zone, best)
    return best if area > 0 else None


def _is_valid_window(hwnd):
    if not win32gui.IsWindowVisible(hwnd):
        return False
    if win32gui.IsIconic(hwnd):  # minimizada
        return False
    title = win32gui.GetWindowText(hwnd)
    if not title:
        return False
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w < MIN_WINDOW_SIZE or h < MIN_WINDOW_SIZE:
            return False
    except Exception:
        return False
    return True


class QuadrantMapper:
    def __init__(self, zones):
        self.zones = zones
        self._windows_cache = []
        self._last_update = 0
        self._dominant_cache = {}  # zone key → hwnd dict

    def _update_windows(self):
        """Atualiza lista de janelas visíveis com z_order."""
        windows = []
        z_order = [0]

        def _enum(hwnd, _):
            if _is_valid_window(hwnd):
                rect = win32gui.GetWindowRect(hwnd)
                windows.append({
                    "hwnd": hwnd,
                    "title": win32gui.GetWindowText(hwnd),
                    "left": rect[0], "top": rect[1],
                    "right": rect[2], "bottom": rect[3],
                    "z_order": z_order[0],
                })
            z_order[0] += 1

        win32gui.EnumWindows(_enum, None)
        self._windows_cache = windows

    def get_dominant(self, zone):
        """
        Retorna janela dominante para a zona dada.
        Atualiza cache a cada QUADRANT_UPDATE_MS ms.
        """
        now = time.time() * 1000
        if now - self._last_update > QUADRANT_UPDATE_MS:
            self._update_windows()
            self._last_update = now
            self._dominant_cache = {}

        key = (zone["monitor_id"], zone["quadrant"])
        if key not in self._dominant_cache:
            self._dominant_cache[key] = find_dominant_window(zone, self._windows_cache)
        return self._dominant_cache[key]
```

- [x] **Step 4: Rodar testes — confirmar que passam**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_quadrant_mapper.py -v
   Esperado: 5 testes passando
```

- [x] **Step 5: Commit**

```bash
git add quadrant_mapper.py tests/test_quadrant_mapper.py
git commit -m "feat: add quadrant_mapper with dominant window detection"
```

---

## Chunk 3: FocusController — saccade detection + overlay

### Task 4: focus_controller.py — saccade detection

**Files:**
- Create: `glaze-app/focus_controller.py`
- Create: `glaze-app/tests/test_focus_controller.py`

- [x] **Step 1: Escrever testes para saccade detection**

```python
# glaze-app/tests/test_focus_controller.py
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from focus_controller import SaccadeDetector

def make_zone(monitor_id, quadrant):
    return {"monitor_id": monitor_id, "quadrant": quadrant,
            "left": 0, "top": 0, "right": 960, "bottom": 540}

def test_no_trigger_on_first_zone():
    sd = SaccadeDetector(stable_ms=150)
    triggered = sd.update(make_zone(0, 0), time.time())
    assert triggered is False

def test_no_trigger_before_stable_time():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    # Muda de zona mas não esperou 150ms
    triggered = sd.update(make_zone(0, 1), t0 + 0.05)
    assert triggered is False

def test_trigger_after_stable_time():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    # Muda de zona e espera 150ms
    sd.update(make_zone(0, 1), t0 + 0.01)
    triggered = sd.update(make_zone(0, 1), t0 + 0.20)
    assert triggered is True

def test_no_trigger_if_zone_changes_before_stable():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    sd.update(make_zone(0, 1), t0 + 0.05)  # muda
    triggered = sd.update(make_zone(0, 2), t0 + 0.10)  # muda de novo antes dos 150ms
    assert triggered is False

def test_no_double_trigger_same_zone():
    sd = SaccadeDetector(stable_ms=150)
    t0 = time.time()
    sd.update(make_zone(0, 0), t0)
    sd.update(make_zone(0, 1), t0 + 0.01)
    sd.update(make_zone(0, 1), t0 + 0.20)   # primeiro trigger
    triggered2 = sd.update(make_zone(0, 1), t0 + 0.30)  # mesmo quadrante — não deve re-triggar
    assert triggered2 is False
```

- [x] **Step 2: Rodar testes — confirmar que falham**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_focus_controller.py -v
   Esperado: ImportError
```

- [x] **Step 3: Implementar SaccadeDetector + FocusController**

```python
# glaze-app/focus_controller.py
import time
import ctypes
import win32gui
import win32con
import threading
import tkinter as tk
from config import SACCADE_STABLE_MS


class SaccadeDetector:
    """
    Detecta quando o olhar estabilizou em uma nova zona por stable_ms milissegundos.
    Retorna True no frame onde o trigger acontece (uma única vez por transição).
    """
    def __init__(self, stable_ms=None):
        self.stable_ms = stable_ms if stable_ms is not None else SACCADE_STABLE_MS
        self._candidate_zone = None
        self._candidate_since = None
        self._last_triggered_key = None

    def _zone_key(self, zone):
        return (zone["monitor_id"], zone["quadrant"])

    def update(self, zone, now=None):
        if now is None:
            now = time.time()
        key = self._zone_key(zone)

        if self._candidate_zone is None:
            # Estado inicial
            self._candidate_zone = zone
            self._candidate_since = now
            return False

        if key != self._zone_key(self._candidate_zone):
            # Nova zona — reinicia candidato
            self._candidate_zone = zone
            self._candidate_since = now
            return False

        # Mesma zona
        elapsed_ms = (now - self._candidate_since) * 1000
        if elapsed_ms >= self.stable_ms and key != self._last_triggered_key:
            self._last_triggered_key = key
            return True

        return False


def _force_foreground(hwnd):
    """
    SetForegroundWindow com workaround para Windows 10/11.
    Usa keybd_event para "enganar" a restrição de processo em foco.
    """
    try:
        # Workaround: simula pressionamento de Alt para que o processo ganhe permissão
        ALT = 0xA4
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(ALT, 0, KEYEVENTF_EXTENDEDKEY, 0)
        win32gui.SetForegroundWindow(hwnd)
        ctypes.windll.user32.keybd_event(ALT, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception as e:
        print(f"[FocusController] Erro ao ativar janela: {e}")


class OverlayBorder:
    """
    Janela transparente sempre-no-topo que desenha uma borda colorida
    ao redor da janela atualmente em foco pelo gaze.
    Roda em thread própria (Tkinter precisa de thread dedicada).
    """
    def __init__(self):
        self._hwnd_target = None
        self._visible = False
        self._root = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "black")
        self._root.configure(bg="black")
        self._canvas = tk.Canvas(self._root, bg="black", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._root.withdraw()
        self._root.after(100, self._update_loop)
        self._root.mainloop()

    def _update_loop(self):
        if self._visible and self._hwnd_target:
            try:
                rect = win32gui.GetWindowRect(self._hwnd_target)
                l, t, r, b = rect
                thickness = 4
                self._root.geometry(f"{r-l}x{b-t}+{l}+{t}")
                self._canvas.delete("all")
                self._canvas.create_rectangle(
                    thickness//2, thickness//2,
                    r-l-thickness//2, b-t-thickness//2,
                    outline="#00FF88", width=thickness
                )
                self._root.deiconify()
            except Exception:
                self._root.withdraw()
        else:
            self._root.withdraw()
        self._root.after(50, self._update_loop)

    def set_target(self, hwnd):
        self._hwnd_target = hwnd

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def toggle(self):
        self._visible = not self._visible


class FocusController:
    def __init__(self):
        self.detector = SaccadeDetector()
        self.overlay = OverlayBorder()
        self._current_hwnd = None

    def update(self, zone, dominant_window):
        """
        Deve ser chamado a cada frame com a zona atual e a janela dominante.
        Ativa a janela se saccade confirmada.
        """
        if zone is None or dominant_window is None:
            return

        triggered = self.detector.update(zone)
        if triggered:
            hwnd = dominant_window["hwnd"]
            if hwnd != self._current_hwnd:
                self._current_hwnd = hwnd
                _force_foreground(hwnd)
                self.overlay.set_target(hwnd)
                print(f"[Glaze] Foco → {dominant_window['title']}")

    def toggle_overlay(self):
        self.overlay.toggle()
```

- [x] **Step 4: Rodar testes de saccade**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_focus_controller.py -v
   Esperado: 5 testes passando
```

- [x] **Step 5: Commit**

```bash
git add focus_controller.py tests/test_focus_controller.py
git commit -m "feat: add focus_controller with saccade detection and overlay border"
```

---

## Chunk 4: GazeTracker — MediaPipe encapsulado

### Task 5: gaze_tracker.py — captura e gaze normalizado

**Files:**
- Create: `glaze-app/gaze_tracker.py`

> Nota: Não há testes unitários automatizados para esta camada — ela depende de webcam real.
> O teste será manual (smoke test visual).

- [x] **Step 1: Baixar MonitorTracking.py como referência**

No WSL:
```bash
gh api repos/JEOresearch/EyeTracker/contents/Webcam3DTracker/MonitorTracking.py \
  --jq '.content' | base64 -d > /mnt/e/projetos/glaze/glaze-app/MonitorTracking_ref.py
```

- [x] **Step 2: Implementar gaze_tracker.py**

Extrai as funções essenciais do `MonitorTracking_ref.py` e as encapsula na classe `GazeTracker`:

```python
# glaze-app/gaze_tracker.py
"""
GazeTracker — encapsula MediaPipe FaceMesh e lógica de gaze do MonitorTracking.py.
Roda captura em thread separada. Expõe get_gaze() → (x, y) normalizado [0..1] ou None.

Baseado em: https://github.com/JEOresearch/EyeTracker/tree/main/Webcam3DTracker
"""
import cv2
import numpy as np
import mediapipe as mp
import math
import threading
from collections import deque
from scipy.spatial.transform import Rotation as Rscipy
from config import CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT, GAZE_SMOOTH_FRAMES


# ── índices de landmarks ────────────────────────────────────────────────────
NOSE_INDICES = [4, 45, 275, 220, 440, 1, 5, 51, 281, 44, 274, 241,
                461, 125, 354, 218, 438, 195, 167, 393, 165, 391, 3, 248]
LEFT_IRIS  = [474, 475, 476, 477]
RIGHT_IRIS = [469, 470, 471, 472]
LEFT_EYE_CORNERS  = [33, 133]
RIGHT_EYE_CORNERS = [362, 263]


def _normalize(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _rot_x(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[1,0,0],[0,ca,-sa],[0,sa,ca]], dtype=float)


def _rot_y(a):
    ca, sa = math.cos(a), math.sin(a)
    return np.array([[ca,0,sa],[0,1,0],[-sa,0,ca]], dtype=float)


def _compute_head_rotation(landmarks, w, h, ref_container):
    """
    Calcula matriz de rotação da cabeça usando landmarks do nariz.
    Mantém consistência de eixos via ref_container (evita flipping).
    """
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h, landmarks[i].z * w]
                    for i in NOSE_INDICES], dtype=float)
    pts -= pts.mean(axis=0)
    cov = pts.T @ pts
    _, vecs = np.linalg.eigh(cov)
    vecs = vecs[:, ::-1]

    if ref_container[0] is None:
        ref_container[0] = vecs.copy()
    else:
        for i in range(3):
            if np.dot(vecs[:, i], ref_container[0][:, i]) < 0:
                vecs[:, i] *= -1

    return vecs  # colunas = eixos X, Y, Z da cabeça


def _get_iris_center(landmarks, indices, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in indices])
    return pts.mean(axis=0)


def _get_eye_center(landmarks, corner_indices, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in corner_indices])
    return pts.mean(axis=0)


def _compute_gaze_direction(landmarks, R, w, h):
    """
    Combina desvio da íris em relação ao canto do olho com orientação da cabeça
    para obter um vetor de gaze 3D aproximado.
    """
    l_iris  = _get_iris_center(landmarks, LEFT_IRIS,  w, h)
    r_iris  = _get_iris_center(landmarks, RIGHT_IRIS, w, h)
    l_eye   = _get_eye_center(landmarks, LEFT_EYE_CORNERS,  w, h)
    r_eye   = _get_eye_center(landmarks, RIGHT_EYE_CORNERS, w, h)

    l_offset = l_iris - l_eye
    r_offset = r_iris - r_eye
    offset   = (l_offset + r_offset) * 0.5

    # Escala do offset em relação ao tamanho do olho
    eye_width = np.linalg.norm(
        np.array([landmarks[133].x - landmarks[33].x,
                  landmarks[133].y - landmarks[33].y]) * w
    )
    if eye_width > 1e-6:
        offset /= eye_width

    # Vetor base da cabeça (forward = -Z no sistema MediaPipe)
    head_forward = -R[:, 2]

    # Aplica rotações de yaw/pitch conforme offset da íris
    yaw   = -offset[0] * 0.8   # horizontal
    pitch =  offset[1] * 0.8   # vertical

    gaze = _rot_y(yaw) @ _rot_x(pitch) @ head_forward
    return _normalize(gaze)


class GazeTracker:
    """
    Captura webcam e estima gaze normalizado [0..1] em thread de background.

    Uso:
        tracker = GazeTracker()
        tracker.start()
        gaze = tracker.get_gaze()   # (x, y) ou None
        tracker.stop()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._gaze = None          # (x_norm, y_norm) suavizado
        self._running = False
        self._thread = None
        self._ref_nose = [None]    # estabilização de eixos
        self._smooth_buf = deque(maxlen=GAZE_SMOOTH_FRAMES)

        # Calibração de centro (1 ponto, usado para inicializar coordenadas)
        self._calib_yaw   = 0.0
        self._calib_pitch = 0.0
        self._calibrated  = False

        self._mp_face = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def calibrate_center(self):
        """Captura gaze atual como ponto de centro (yaw/pitch = 0)."""
        gaze = self._raw_gaze_angles()
        if gaze:
            self._calib_yaw, self._calib_pitch = gaze
            self._calibrated = True
            print("[GazeTracker] Centro calibrado.")

    def get_gaze(self):
        """Retorna (x, y) normalizados [0..1] ou None se rosto não detectado."""
        with self._lock:
            return self._gaze

    def _raw_gaze_angles(self):
        with self._lock:
            return self._gaze  # temporário — refinado na calibração 5-pontos

    def _loop(self):
        cap = cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self._mp_face.process(rgb)

            if not result.multi_face_landmarks:
                with self._lock:
                    self._gaze = None
                continue

            lm = result.multi_face_landmarks[0].landmark
            R = _compute_head_rotation(lm, w, h, self._ref_nose)
            gaze_dir = _compute_gaze_direction(lm, R, w, h)

            # Converte direção 3D → ângulos yaw/pitch normalizados
            yaw   = math.atan2(gaze_dir[0], -gaze_dir[2])
            pitch = math.atan2(gaze_dir[1], -gaze_dir[2])

            # Aplica offset de calibração
            yaw   -= self._calib_yaw
            pitch -= self._calib_pitch

            # Normaliza para [0..1] (range ±30 graus)
            x_norm = 0.5 + yaw   / math.radians(60)
            y_norm = 0.5 + pitch / math.radians(40)
            x_norm = max(0.0, min(1.0, x_norm))
            y_norm = max(0.0, min(1.0, y_norm))

            self._smooth_buf.append((x_norm, y_norm))
            xs = sum(p[0] for p in self._smooth_buf) / len(self._smooth_buf)
            ys = sum(p[1] for p in self._smooth_buf) / len(self._smooth_buf)

            with self._lock:
                self._gaze = (xs, ys)

        cap.release()
```

- [x] **Step 3: Smoke test manual no Windows**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -c "
import time
from gaze_tracker import GazeTracker
t = GazeTracker()
t.start()
print('Tracker iniciado. Olhe para a câmera...')
for _ in range(30):
    time.sleep(0.1)
    print(t.get_gaze())
t.stop()
"
   Esperado: tuples (x, y) aparecendo (valores entre 0 e 1), ou None se rosto não detectado.
   Se aparecer erro de câmera: trocar CAMERA_INDEX = 1 em config.py
```

- [x] **Step 4: Commit**

```bash
git add gaze_tracker.py MonitorTracking_ref.py
git commit -m "feat: add GazeTracker with MediaPipe FaceMesh in background thread"
```

---

## Chunk 5: Calibração 5 pontos por monitor

### Task 6: calibration.py — homografia e fluxo interativo

**Files:**
- Create: `glaze-app/calibration.py`
- Create: `glaze-app/tests/test_calibration.py`

- [x] **Step 1: Escrever testes para homografia**

```python
# glaze-app/tests/test_calibration.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from calibration import Calibration

def test_apply_homography_identity():
    """Homografia identidade: ponto (0.5, 0.5) deve mapear para centro do monitor."""
    cal = Calibration.__new__(Calibration)
    # Monitor 1920x1080 em (0,0)
    monitor = {"left": 0, "top": 0, "right": 1920, "bottom": 1080}
    # 5 pontos de calibração: gaze normalizado → pixel absoluto
    src = np.float32([[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]])
    dst = np.float32([
        [960,  540],
        [0,    0  ],
        [1920, 0  ],
        [0,    1080],
        [1920, 1080],
    ])
    import cv2
    H, _ = cv2.findHomography(src, dst)
    cal._homographies = {0: H}

    result = cal.apply(0, 0.5, 0.5)
    assert result is not None
    x, y = result
    assert abs(x - 960) < 5
    assert abs(y - 540) < 5

def test_save_and_load(tmp_path):
    import cv2
    cal = Calibration.__new__(Calibration)
    src = np.float32([[0.5,0.5],[0.1,0.1],[0.9,0.1],[0.1,0.9],[0.9,0.9]])
    dst = np.float32([[960,540],[0,0],[1920,0],[0,1080],[1920,1080]])
    H, _ = cv2.findHomography(src, dst)
    cal._homographies = {0: H}

    path = str(tmp_path / "cal.json")
    cal.save(path)
    assert os.path.exists(path)

    cal2 = Calibration.__new__(Calibration)
    cal2.load(path)
    result = cal2.apply(0, 0.5, 0.5)
    assert result is not None
    x, y = result
    assert abs(x - 960) < 5

def test_apply_returns_none_without_calibration():
    cal = Calibration.__new__(Calibration)
    cal._homographies = {}
    assert cal.apply(0, 0.5, 0.5) is None
```

- [x] **Step 2: Rodar testes — confirmar que falham**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_calibration.py -v
   Esperado: ImportError
```

- [x] **Step 3: Implementar calibration.py**

```python
# glaze-app/calibration.py
"""
Calibração 5 pontos por monitor (centro + 4 cantos).
Calcula homografia 2D: gaze normalizado → coordenada absoluta de desktop (px).
"""
import json
import time
import threading
import numpy as np
import cv2
import tkinter as tk
from config import CALIBRATION_FILE, CALIBRATION_SAMPLES


# Ordem dos pontos: centro, top-left, top-right, bottom-left, bottom-right
POINT_LABELS = ["Centro", "Canto superior esquerdo", "Canto superior direito",
                "Canto inferior esquerdo", "Canto inferior direito"]


def _normalized_point_pos(monitor, point_idx):
    """Retorna posição normalizada [0..1] para cada ponto de calibração."""
    margin = 0.05
    positions = [
        (0.5,        0.5       ),  # centro
        (margin,     margin    ),  # top-left
        (1-margin,   margin    ),  # top-right
        (margin,     1-margin  ),  # bottom-left
        (1-margin,   1-margin  ),  # bottom-right
    ]
    nx, ny = positions[point_idx]
    l, t = monitor["left"], monitor["top"]
    w = monitor["right"] - l
    h = monitor["bottom"] - t
    return int(l + nx * w), int(t + ny * h), nx, ny


class CalibrationWindow:
    """Janela fullscreen para um monitor exibindo ponto de calibração pulsante."""

    def __init__(self, monitor, point_idx, label):
        self.monitor = monitor
        self.point_idx = point_idx
        self.label = label
        self.confirmed = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        root = tk.Tk()
        root.overrideredirect(True)
        l, t = self.monitor["left"], self.monitor["top"]
        w = self.monitor["right"] - l
        h = self.monitor["bottom"] - t
        root.geometry(f"{w}x{h}+{l}+{t}")
        root.configure(bg="black")
        root.attributes("-topmost", True)

        canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        px, py, nx, ny = _normalized_point_pos(self.monitor, self.point_idx)
        # Posição relativa à janela
        cx = int(nx * w)
        cy = int(ny * h)

        # Texto de instrução
        canvas.create_text(w//2, h-60, text=f"Olhe para o ponto e pressione SPACE",
                           fill="white", font=("Arial", 16))
        canvas.create_text(w//2, h-30, text=self.label,
                           fill="#00FF88", font=("Arial", 12))

        # Animação de pulso
        radius = [20]
        growing = [True]

        def pulse():
            canvas.delete("dot")
            r = radius[0]
            canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                               fill="#00FF88", outline="white", width=2, tags="dot")
            if growing[0]:
                radius[0] += 1
                if radius[0] >= 28: growing[0] = False
            else:
                radius[0] -= 1
                if radius[0] <= 16: growing[0] = True
            root.after(30, pulse)

        pulse()
        root.bind("<space>", lambda e: (self.confirmed.set(), root.destroy()))
        root.focus_force()
        root.mainloop()

    def wait_for_confirm(self):
        self.confirmed.wait()


class Calibration:
    def __init__(self):
        self._homographies = {}  # monitor_id → H (3x3 numpy)

    def run_calibration(self, monitors, gaze_tracker):
        """
        Conduz calibração interativa para cada monitor.
        monitors: lista de dicts com left/top/right/bottom/id/name
        gaze_tracker: instância de GazeTracker com get_gaze()
        """
        for monitor in monitors:
            mid = monitor["id"]
            name = monitor["name"]
            print(f"\n[Calibração] Monitor {mid+1} de {len(monitors)}: {name}")
            print(f"[Calibração] Dispositivo: {monitor.get('device', '?')}")

            src_points = []  # gaze normalizado
            dst_points = []  # pixels absolutos

            for i, label in enumerate(POINT_LABELS):
                px, py, nx, ny = _normalized_point_pos(monitor, i)
                print(f"  → Ponto {i+1}/5: {label}")

                win = CalibrationWindow(monitor, i, f"{label} — Monitor: {name}")
                win.wait_for_confirm()

                # Coleta amostras
                samples = []
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    g = gaze_tracker.get_gaze()
                    if g is not None:
                        samples.append(g)
                    time.sleep(0.05)

                if len(samples) < 2:
                    print(f"  [AVISO] Poucas amostras ({len(samples)}) — rosto não detectado?")
                    samples = [(0.5, 0.5)]

                gx = sum(s[0] for s in samples) / len(samples)
                gy = sum(s[1] for s in samples) / len(samples)
                src_points.append([gx, gy])
                dst_points.append([px, py])
                print(f"  ✓ Gaze ({gx:.3f}, {gy:.3f}) → Pixel ({px}, {py})")

            src = np.float32(src_points)
            dst = np.float32(dst_points)
            H, _ = cv2.findHomography(src, dst)
            self._homographies[mid] = H
            print(f"[Calibração] Monitor {mid} calibrado.")

        self.save(CALIBRATION_FILE)
        print(f"\n[Calibração] Salvo em {CALIBRATION_FILE}. Retomando tracking.")

    def apply(self, monitor_id, x_norm, y_norm):
        """Mapeia gaze normalizado → (x_abs, y_abs) em pixels. Retorna None se não calibrado."""
        H = self._homographies.get(monitor_id)
        if H is None:
            return None
        pt = np.float32([[[x_norm, y_norm]]])
        result = cv2.perspectiveTransform(pt, H)
        x, y = result[0][0]
        return int(x), int(y)

    def save(self, path=None):
        path = path or CALIBRATION_FILE
        data = {str(k): v.tolist() for k, v in self._homographies.items()}
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path=None):
        path = path or CALIBRATION_FILE
        try:
            with open(path) as f:
                data = json.load(f)
            self._homographies = {int(k): np.array(v) for k, v in data.items()}
            print(f"[Calibração] Carregado de {path}")
            return True
        except FileNotFoundError:
            print(f"[Calibração] {path} não encontrado — rode Ctrl+Alt+C para calibrar.")
            return False

    def is_calibrated(self):
        return len(self._homographies) > 0
```

- [x] **Step 4: Rodar testes**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/test_calibration.py -v
   Esperado: 3 testes passando
```

- [x] **Step 5: Commit**

```bash
git add calibration.py tests/test_calibration.py
git commit -m "feat: add 5-point calibration with homography per monitor"
```

---

## Chunk 6: main.py — orquestração + hotkeys

### Task 7: main.py — loop principal e hotkeys

**Files:**
- Create: `glaze-app/main.py`

- [x] **Step 1: Implementar main.py**

```python
# glaze-app/main.py
"""
Glaze — Eye-Tracking Window Focus
Hotkeys:
  Ctrl+Alt+G — liga/desliga tracking
  Ctrl+Alt+B — toggle overlay visual
  Ctrl+Alt+C — inicia re-calibração
  Ctrl+Alt+Q — encerra
"""
import time
import keyboard
from config import ZONE_LAYOUT, CALIBRATION_FILE
from gaze_tracker import GazeTracker
from calibration import Calibration
from monitor_layout import MonitorLayout
from quadrant_mapper import QuadrantMapper
from focus_controller import FocusController

TARGET_FPS = 30
FRAME_MS = 1.0 / TARGET_FPS


def main():
    print("[Glaze] Iniciando...")

    tracker = GazeTracker()
    calibration = Calibration()
    layout = MonitorLayout(ZONE_LAYOUT)
    mapper = QuadrantMapper(layout.zones)
    controller = FocusController()

    # Tenta carregar calibração existente
    calibration.load(CALIBRATION_FILE)

    tracker.start()
    print("[Glaze] Tracker iniciado.")
    print("[Glaze] Hotkeys: Ctrl+Alt+G=toggle | Ctrl+Alt+B=overlay | Ctrl+Alt+C=calibrar | Ctrl+Alt+Q=sair")

    tracking_enabled = True

    def do_calibrate():
        nonlocal tracking_enabled
        tracking_enabled = False
        print("[Glaze] Iniciando calibração...")
        calibration.run_calibration(layout.monitors, tracker)
        tracking_enabled = True

    keyboard.add_hotkey("ctrl+alt+g", lambda: toggle_tracking())
    keyboard.add_hotkey("ctrl+alt+b", lambda: controller.toggle_overlay())
    keyboard.add_hotkey("ctrl+alt+c", lambda: do_calibrate())
    keyboard.add_hotkey("ctrl+alt+q", lambda: quit_app())

    running = [True]

    def toggle_tracking():
        nonlocal tracking_enabled
        tracking_enabled = not tracking_enabled
        state = "ON" if tracking_enabled else "OFF"
        print(f"[Glaze] Tracking {state}")

    def quit_app():
        running[0] = False

    # Loop principal
    while running[0]:
        t0 = time.time()

        if tracking_enabled and calibration.is_calibrated():
            gaze = tracker.get_gaze()
            if gaze is not None:
                gx, gy = gaze
                # Mapeia gaze normalizado → coordenada absoluta desktop usando o primeiro monitor
                # (simplificação: usa monitor 0 para o mapeamento inicial)
                # Para multi-monitor, o calibration.apply itera por monitor_id
                # e a zona resolve qual monitor contém o ponto resultante
                for monitor in layout.monitors:
                    abs_pos = calibration.apply(monitor["id"], gx, gy)
                    if abs_pos is None:
                        continue
                    ax, ay = abs_pos
                    zone = layout.get_zone(ax, ay)
                    if zone is not None:
                        dominant = mapper.get_dominant(zone)
                        controller.update(zone, dominant)
                        break

        elapsed = time.time() - t0
        sleep_time = FRAME_MS - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    print("[Glaze] Encerrando...")
    tracker.stop()


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Teste de integração manual no Windows**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python main.py

   O que esperar:
   - Mensagem "[Glaze] Iniciando..."
   - Se calibration.json não existir: "[Calibração] calibration.json não encontrado — rode Ctrl+Alt+C"
   - Mensagem "[Glaze] Tracker iniciado."
   - Pressione Ctrl+Alt+C para iniciar calibração
     → Uma janela preta fullscreen aparece no seu monitor com um ponto verde pulsante
     → Olhe para o ponto e pressione SPACE para cada um dos 5 pontos
     → Repita para o segundo monitor
   - Após calibração: mova os olhos entre janelas
     → No terminal deve aparecer: "[Glaze] Foco → NomeDaJanela"
   - Ctrl+Alt+B — toggle da borda verde ao redor da janela focada
   - Ctrl+Alt+G — pausa/retoma tracking
   - Ctrl+Alt+Q — encerra

3. Se a câmera não abrir: edite config.py e mude CAMERA_INDEX = 1
4. Se o foco não mudar: ajuste SACCADE_STABLE_MS = 200 em config.py (mais tolerante)
```

- [x] **Step 3: Rodar todos os testes unitários**

```
## O que fazer no Windows

1. cd E:\projetos\glaze\glaze-app
2. python -m pytest tests/ -v
   Esperado: todos os testes passando
```

- [x] **Step 4: Commit final**

```bash
git add main.py
git commit -m "feat: add main.py with hotkeys and main loop — glaze v1 complete"
```

---

## Resumo de Hotkeys

| Hotkey | Ação |
|--------|------|
| `Ctrl+Alt+G` | Liga/desliga tracking |
| `Ctrl+Alt+B` | Toggle overlay (borda verde) |
| `Ctrl+Alt+C` | Inicia re-calibração |
| `Ctrl+Alt+Q` | Encerra o programa |

## Configurações Ajustáveis (config.py)

| Parâmetro | Padrão | O que faz |
|-----------|--------|-----------|
| `CAMERA_INDEX` | 0 | Índice da webcam |
| `CAPTURE_WIDTH/HEIGHT` | 480x360 | Resolução de captura |
| `SACCADE_STABLE_MS` | 150 | Tempo de estabilidade para ativar janela |
| `GAZE_SMOOTH_FRAMES` | 10 | Suavização do gaze |
| `ZONE_LAYOUT` | "2x2" | Layout de zonas ("2x2", "4x1", "1x4") |
| `QUADRANT_UPDATE_MS` | 500 | Frequência de atualização das janelas |
| `MIN_WINDOW_SIZE` | 200 | Tamanho mínimo de janela considerada |
