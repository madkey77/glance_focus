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
                # Mapeia gaze normalizado → coordenada absoluta desktop
                # Itera por monitor — usa o primeiro que retornar zona válida
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
