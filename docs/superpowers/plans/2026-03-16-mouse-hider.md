# MouseHider Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `MouseHider` class that hides the cursor after 2s of inactivity and teleports it to the current gaze position when the mouse moves again.

**Architecture:** New class `MouseHider` in `focus_controller.py` uses a `pynput.mouse.Listener` to detect movement and a `threading.Timer` for the idle timeout. `FocusController` feeds gaze coordinates every frame. `main.py` calls `set_enabled()` on tracking toggle/calibration and `stop()` on quit.

**Tech Stack:** Python, pynput (already in requirements), ctypes/win32 (already used), threading.

**Spec:** `docs/superpowers/specs/2026-03-16-mouse-hider-design.md`

---

## Chunk 1: Config + MouseHider class + unit tests

### Task 1: Add MOUSE_HIDE_DELAY_S to config.py

**Files:**
- Modify: `glaze-app/config.py`

- [ ] Add at the end of `config.py`:

```python
MOUSE_HIDE_DELAY_S = 2.0   # seconds of inactivity before cursor hides
```

- [ ] Commit:
```bash
git add glaze-app/config.py
git commit -m "feat(mouse-hider): add MOUSE_HIDE_DELAY_S config constant"
```

---

### Task 2: Add MouseHider class + unit tests (TDD)

**Files:**
- Modify: `glaze-app/focus_controller.py` (add `MouseHider` before `FocusController`)
- Create: `glaze-app/tests/test_mouse_hider.py`

The tests mock all Win32 calls — no Windows required to run them. The mock targets are `ctypes.windll.user32.ShowCursor` and `ctypes.windll.user32.SetCursorPos`, but since `MouseHider` imports these lazily via `ctypes.windll.user32`, we patch them on the module after import.

**Important:** `MouseHider.__init__` starts a `pynput.mouse.Listener`. In tests we must prevent the real listener from starting — pass a `_listener_factory` kwarg for dependency injection (default: `pynput.mouse.Listener`). `threading.Timer` is injected via `_timer_factory` kwarg (default: `threading.Timer`) so `test_hide_called_after_delay` can use a near-zero delay timer instead of waiting 2 real seconds.

#### Step-by-step:

- [ ] Create `glaze-app/tests/test_mouse_hider.py` with all failing tests:

```python
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch, call
import pytest


def _make_fast_timer(delay, fn):
    """threading.Timer replacement that fires in 0.05s regardless of delay."""
    return threading.Timer(0.05, fn)


def _make_hider(**kwargs):
    """Create MouseHider with a no-op listener factory and fast timer factory."""
    from focus_controller import MouseHider
    fake_listener = MagicMock()
    fake_listener_instance = MagicMock()
    fake_listener.return_value = fake_listener_instance
    return MouseHider(
        _listener_factory=fake_listener,
        _timer_factory=_make_fast_timer,
        **kwargs
    ), fake_listener


def test_hide_called_after_delay():
    """Timer fires → ShowCursor(False) called once, _hidden becomes True."""
    hider, _ = _make_hider()
    fired = threading.Event()
    original_hide = hider._hide

    def patched_hide():
        original_hide()
        fired.set()

    hider._hide = patched_hide

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider.set_enabled(True)
        hider._reset_timer()  # manually trigger timer with near-zero delay
        # wait for timer
        assert fired.wait(timeout=3.0), "Timer never fired"
        mock_user32.ShowCursor.assert_called_once_with(False)
    hider.stop()


def test_no_double_hide():
    """Calling _hide() twice only calls ShowCursor(False) once."""
    hider, _ = _make_hider()
    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._hide()
        mock_user32.ShowCursor.assert_called_once_with(False)
    hider.stop()


def test_no_double_show():
    """Calling _show() twice only calls ShowCursor(True) once."""
    hider, _ = _make_hider()
    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()   # first hide so _hidden=True
        hider._show()
        hider._show()   # second show — should be no-op
        # ShowCursor(True) called once
        true_calls = [c for c in mock_user32.ShowCursor.call_args_list if c == call(True)]
        assert len(true_calls) == 1
    hider.stop()


def test_show_on_move_teleports_cursor():
    """Movement: SetCursorPos called BEFORE ShowCursor(True)."""
    hider, _ = _make_hider()
    hider.set_gaze_pos(800, 400)
    call_order = []

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        mock_user32.SetCursorPos.side_effect = lambda x, y: call_order.append('set')
        mock_user32.ShowCursor.side_effect = lambda v: call_order.append(f'show_{v}')

        hider._hide()          # hide first so _hidden=True
        hider._on_move(0, 0)   # simulate mouse movement

        assert call_order == ['show_False', 'set', 'show_True'], \
            f"Expected [show_False, set, show_True], got {call_order}"
        mock_user32.SetCursorPos.assert_called_with(800, 400)
    hider.stop()


def test_gaze_pos_used_on_wake():
    """Cursor teleports to last set_gaze_pos coordinates on wake."""
    hider, _ = _make_hider()
    hider.set_gaze_pos(1200, 600)

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._on_move(0, 0)
        mock_user32.SetCursorPos.assert_called_with(1200, 600)
    hider.stop()


def test_no_teleport_when_no_gaze():
    """If set_gaze_pos was never called, _on_move skips SetCursorPos."""
    hider, _ = _make_hider()
    # _gaze_pos is None — never set

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider._on_move(0, 0)
        mock_user32.SetCursorPos.assert_not_called()
        mock_user32.ShowCursor.assert_called_with(True)
    hider.stop()


def test_disabled_prevents_hide():
    """set_enabled(False) cancels timer and restores cursor."""
    hider, _ = _make_hider()

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()           # simulate hidden state
        hider.set_enabled(False)
        # ShowCursor(True) must be called to restore cursor
        mock_user32.ShowCursor.assert_called_with(True)
    hider.stop()


def test_stop_restores_cursor():
    """stop() calls ShowCursor(True) if cursor is currently hidden."""
    hider, _ = _make_hider()

    with patch('ctypes.windll') as mock_windll:
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32
        hider._hide()
        hider.stop()
        true_calls = [c for c in mock_user32.ShowCursor.call_args_list if c == call(True)]
        assert len(true_calls) >= 1
```

- [ ] Run to confirm ALL fail (ImportError expected — MouseHider doesn't exist yet):
```bash
cd glaze-app && python -m pytest tests/test_mouse_hider.py -v
```
Expected: `ImportError` or `AttributeError` on every test.

- [ ] Add `MouseHider` to `glaze-app/focus_controller.py` — insert before the `FocusController` class:

```python
class MouseHider:
    """
    Esconde o cursor do mouse após MOUSE_HIDE_DELAY_S segundos sem movimento.
    Ao detectar movimento, teleporta o cursor para a posição atual do gaze
    antes de torná-lo visível novamente.

    Thread-safe: _lock protege todo o estado mutável.
    pynput.mouse.Listener roda em sua própria thread daemon.
    threading.Timer dispara _hide() em thread daemon separada.
    """

    def __init__(self, _listener_factory=None, _timer_factory=None):
        from config import MOUSE_HIDE_DELAY_S
        self._delay         = MOUSE_HIDE_DELAY_S
        self._lock          = threading.Lock()
        self._gaze_pos      = None   # None até set_gaze_pos ser chamado
        self._hidden        = False
        self._enabled       = False
        self._timer         = None
        self._listener      = None
        self._timer_factory = _timer_factory if _timer_factory is not None else threading.Timer

        factory = _listener_factory
        if factory is None:
            from pynput import mouse as _mouse
            factory = _mouse.Listener

        try:
            self._listener = factory(on_move=self._on_move)
            self._listener.start()
        except Exception as e:
            print(f"[MouseHider] Falha ao iniciar listener: {e} — desativado.")
            self._enabled = False
            self._listener = None
            return

        # Inicia habilitado
        self._enabled = True
        self._reset_timer()

    # ── state mutators (all acquire _lock) ───────────────────────────────────

    def _hide(self):
        """Esconde cursor. Só chama ShowCursor(False) uma vez (_hidden guard)."""
        import ctypes
        with self._lock:
            if self._hidden:
                return
            self._hidden = True
        ctypes.windll.user32.ShowCursor(False)

    def _show(self):
        """Mostra cursor. Só chama ShowCursor(True) uma vez (_hidden guard)."""
        import ctypes
        with self._lock:
            if not self._hidden:
                return
            self._hidden = False
        ctypes.windll.user32.ShowCursor(True)

    def _reset_timer(self):
        """Cancela timer existente e agenda novo _hide após _delay segundos."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if not self._enabled:
                return
            t = self._timer_factory(self._delay, self._hide)
            t.daemon = True
            t.start()
            self._timer = t

    def _cancel_timer(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ── pynput callback ───────────────────────────────────────────────────────

    def _on_move(self, x, y):
        """Chamado pelo pynput em qualquer movimento do mouse."""
        import ctypes
        with self._lock:
            enabled = self._enabled
            gaze    = self._gaze_pos
        if not enabled:
            return
        # Teleporta para o gaze ANTES de mostrar (evita flicker na posição antiga)
        if gaze is not None:
            ctypes.windll.user32.SetCursorPos(gaze[0], gaze[1])
        self._show()
        self._reset_timer()

    # ── public interface ──────────────────────────────────────────────────────

    def set_gaze_pos(self, ax: int, ay: int) -> None:
        """Atualiza posição do gaze. Chamado a cada frame quando gaze é válido."""
        with self._lock:
            self._gaze_pos = (ax, ay)

    def set_enabled(self, enabled: bool) -> None:
        """Habilita/desabilita. Idempotente. Desabilitar restaura cursor e cancela timer."""
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
        if not enabled:
            self._cancel_timer()
            self._show()
        else:
            self._reset_timer()

    def stop(self) -> None:
        """Restaura cursor e para listener. Chamado no encerramento do app."""
        self._cancel_timer()
        self._show()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
```

- [ ] Run tests to confirm ALL pass:
```bash
cd glaze-app && python -m pytest tests/test_mouse_hider.py -v
```
Expected: 8 passed.

- [ ] Run full test suite:
```bash
cd glaze-app && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] Commit:
```bash
git add glaze-app/focus_controller.py glaze-app/tests/test_mouse_hider.py
git commit -m "feat(mouse-hider): add MouseHider class + unit tests"
```

---

## Chunk 2: Integration

### Task 3: Wire MouseHider into FocusController

**Files:**
- Modify: `glaze-app/focus_controller.py` (`FocusController.__init__` and `update`)

- [ ] In `FocusController.__init__`, add after `self._last_valid_pos = None`:

```python
self.mouse_hider = MouseHider()
```

- [ ] In `FocusController.update()`, find this block:

```python
# Atualiza posição do ponto de gaze a cada frame e salva como última válida
self._last_valid_pos = (ax, ay)
self.gaze_dot.set_position(ax, ay)
self.desktop_map.set_gaze(ax, ay)
```

Replace with:
```python
# Atualiza posição do ponto de gaze a cada frame e salva como última válida
self._last_valid_pos = (ax, ay)
self.gaze_dot.set_position(ax, ay)
self.desktop_map.set_gaze(ax, ay)
if ax is not None and ay is not None:
    self.mouse_hider.set_gaze_pos(ax, ay)
```

Note: at this point in `update()` `ax` and `ay` are already guaranteed non-None (the `if zone is None or ax is None: return` guard runs earlier), but the explicit check makes the intent clear and protects against future refactors.

- [ ] Run full test suite to confirm nothing broke:
```bash
cd glaze-app && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] Commit:
```bash
git add glaze-app/focus_controller.py
git commit -m "feat(mouse-hider): wire MouseHider into FocusController"
```

---

### Task 4: Wire set_enabled and stop into main.py

**Files:**
- Modify: `glaze-app/main.py`

Three integration points:

**1. `toggle_tracking()`** — call `set_enabled` after toggling the flag:

Current code:
```python
def toggle_tracking():
    tracking_enabled[0] = not tracking_enabled[0]
    state = "ON" if tracking_enabled[0] else "OFF"
    print(f"[Glaze] Tracking {state}")
```

Replace with:
```python
def toggle_tracking():
    tracking_enabled[0] = not tracking_enabled[0]
    state = "ON" if tracking_enabled[0] else "OFF"
    print(f"[Glaze] Tracking {state}")
    controller.mouse_hider.set_enabled(tracking_enabled[0])
```

**2. `do_calibrate()`** — disable before calibration, re-enable after:

Current code:
```python
def do_calibrate():
    tracking_enabled[0] = False
    print("[Glaze] Iniciando calibração...")
    calibration.run_calibration(layout.monitors, tracker)
    tracking_enabled[0] = True
```

Replace with:
```python
def do_calibrate():
    tracking_enabled[0] = False
    controller.mouse_hider.set_enabled(False)
    print("[Glaze] Iniciando calibração...")
    calibration.run_calibration(layout.monitors, tracker)
    tracking_enabled[0] = True
    controller.mouse_hider.set_enabled(True)
```

**3. `quit_app()` and post-loop cleanup** — call `stop()` before exit:

Current code:
```python
def quit_app():
    running[0] = False
```

Replace with:
```python
def quit_app():
    running[0] = False
    controller.mouse_hider.stop()
```

- [ ] Apply all three changes to `glaze-app/main.py`.

- [ ] Run full test suite:
```bash
cd glaze-app && python -m pytest tests/ -v
```
Expected: all pass.

- [ ] Commit:
```bash
git add glaze-app/main.py
git commit -m "feat(mouse-hider): wire set_enabled/stop into main.py lifecycle"
```

---

## Windows smoke test instructions

After all tasks complete, test on Windows:

```
cd E:\projetos\glaze\glaze-app
python main.py
```

1. Start the app — cursor should be visible initially
2. Leave mouse untouched for 2 seconds — cursor disappears
3. Move mouse — cursor reappears at gaze position (where eyes are looking)
4. Press `Ctrl+Alt+G` to toggle tracking off — cursor reappears permanently, no more hiding
5. Press `Ctrl+Alt+G` again — hiding resumes
6. Press `Ctrl+Alt+C` — calibration UI appears with visible cursor; after calibration, hiding resumes
7. Press `Ctrl+Alt+Q` — app closes, cursor remains visible

Check terminal for:
```
[Glaze] Tracker iniciado.
```
(No `[MouseHider] Falha` lines — listener started successfully)
