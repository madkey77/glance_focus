# glaze-app/focus_controller.py
import time
import threading
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
    import ctypes
    import win32gui
    try:
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
        import tkinter as tk
        import win32gui
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
        import win32gui
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
