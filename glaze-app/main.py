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
import threading
from pynput import keyboard as kb
from config import ZONE_LAYOUT, CALIBRATION_FILE
from gaze_tracker import GazeTracker
from calibration import Calibration
from monitor_layout import MonitorLayout
from quadrant_mapper import QuadrantMapper
from focus_controller import FocusController

TARGET_FPS = 30
FRAME_MS = 1.0 / TARGET_FPS

# Teclas modificadoras monitoradas
_CTRL  = {kb.Key.ctrl_l, kb.Key.ctrl_r}
_ALT   = {kb.Key.alt_l, kb.Key.alt_r, kb.Key.alt_gr}
_KEYS  = {
    'g': "toggle",
    'b': "overlay",
    'c': "calibrate",
    'q': "quit",
}


def _make_hotkey_listener(callbacks):
    """
    Listener passivo (pynput) — não suprime eventos, não interfere com
    Ctrl+C / Ctrl+V nem com outros atalhos do sistema.
    """
    pressed = set()

    def on_press(key):
        pressed.add(key)
        has_ctrl = bool(pressed & _CTRL)
        has_alt  = bool(pressed & _ALT)
        if not (has_ctrl and has_alt):
            return
        # Normaliza o char: pynput pode entregar char de controle (ex: \x07)
        # quando Ctrl está pressionado, então usa vk para obter o char real.
        char = None
        if hasattr(key, 'char') and key.char:
            c = key.char.lower()
            char = c if len(c) == 1 and c.isascii() and c.isprintable() else None
        if char is None and hasattr(key, 'vk') and key.vk:
            vk = key.vk
            if 65 <= vk <= 90:  # A-Z
                char = chr(vk + 32)  # converte para minúsculo
        if char and char in _KEYS:
            action = _KEYS[char]
            if action in callbacks:
                threading.Thread(target=callbacks[action], daemon=True).start()

    def on_release(key):
        pressed.discard(key)

    return kb.Listener(on_press=on_press, on_release=on_release)


def main():
    print("[Glaze] Iniciando...")

    tracker = GazeTracker()
    calibration = Calibration()
    layout = MonitorLayout(ZONE_LAYOUT)
    mapper = QuadrantMapper(layout.zones)
    controller = FocusController(layout)

    calibration.load(CALIBRATION_FILE)

    tracker.start()
    print("[Glaze] Tracker iniciado.")
    print("[Glaze] Hotkeys: Ctrl+Alt+G=toggle | Ctrl+Alt+B=overlay | Ctrl+Alt+C=calibrar | Ctrl+Alt+Q=sair")

    running = [True]
    tracking_enabled = [True]

    def toggle_tracking():
        tracking_enabled[0] = not tracking_enabled[0]
        state = "ON" if tracking_enabled[0] else "OFF"
        print(f"[Glaze] Tracking {state}")

    def do_calibrate():
        tracking_enabled[0] = False
        print("[Glaze] Iniciando calibração...")
        calibration.run_calibration(layout.monitors, tracker)
        tracking_enabled[0] = True

    def quit_app():
        running[0] = False

    listener = _make_hotkey_listener({
        "toggle":    toggle_tracking,
        "overlay":   controller.toggle_overlay,
        "calibrate": do_calibrate,
        "quit":      quit_app,
    })
    listener.start()

    # Loop principal
    while running[0]:
        t0 = time.time()

        if tracking_enabled[0] and calibration.is_calibrated():
            gaze = tracker.get_gaze()
            if gaze is not None:
                gx, gy = gaze
                _found = False
                _last_abs = None
                for monitor in layout.monitors:
                    abs_pos = calibration.apply(monitor["id"], gx, gy)
                    if abs_pos is None:
                        continue
                    ax, ay = abs_pos
                    _last_abs = (ax, ay)
                    zone = layout.get_zone(ax, ay)
                    if zone is not None:
                        dominant = mapper.get_dominant(zone)
                        controller.update(zone, dominant, ax, ay)
                        _found = True
                        break
                if not _found:
                    # Passa coordenadas mesmo sem zona para o overlay congelar na borda
                    lax, lay = _last_abs if _last_abs else (None, None)
                    controller.update(None, None, lax, lay)
            else:
                controller.update(None, None)

        elapsed = time.time() - t0
        sleep_time = FRAME_MS - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    print("[Glaze] Encerrando...")
    listener.stop()
    tracker.stop()


if __name__ == "__main__":
    main()
