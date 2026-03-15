# glaze-app/calibration.py
"""
Calibração 5 pontos por monitor (centro + 4 cantos).
Calcula homografia 2D: gaze normalizado → coordenada absoluta de desktop (px).
"""
import json
import time
import threading
import queue
import numpy as np
import cv2
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


class _TkCalibrationSession:
    """
    Gerencia a sessão Tkinter inteira para calibração em uma única thread dedicada.
    Todos os comandos Tk são executados via fila + root.after para evitar
    Tcl_AsyncDelete (nunca chamamos Tk de outra thread).

    Usa ENTER como confirmação (não SPACE) para não conflitar com o
    hook global do `keyboard` que captura SPACE durante calibração.
    """

    def __init__(self):
        self._cmd_queue = queue.Queue()
        self._confirmed = threading.Event()
        self._ready = threading.Event()
        self._pulse_active = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        import tkinter as tk
        self._tk = tk
        self._root = tk.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg="black")
        self._root.withdraw()

        self._canvas = tk.Canvas(self._root, bg="black", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)

        self._ready.set()
        self._root.after(50, self._process_queue)
        self._root.mainloop()

    def _process_queue(self):
        try:
            while True:
                cmd, args = self._cmd_queue.get_nowait()
                if cmd == "show_point":
                    self._do_show_point(*args)
                elif cmd == "hide":
                    self._root.withdraw()
                elif cmd == "destroy":
                    self._root.destroy()
                    return  # sai do mainloop via destroy
        except queue.Empty:
            pass
        self._root.after(50, self._process_queue)

    def _do_show_point(self, monitor, point_idx, label):
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

        px, py, nx, ny = _normalized_point_pos(monitor, point_idx)
        cx = int(nx * w)
        cy = int(ny * h)

        canvas.create_text(w//2, h-60,
                           text="Olhe para o ponto e pressione ENTER para confirmar",
                           fill="white", font=("Arial", 16))
        canvas.create_text(w//2, h-30, text=label,
                           fill="#00FF88", font=("Arial", 12))

        self._confirmed.clear()
        self._pulse_cx = cx
        self._pulse_cy = cy
        self._pulse_r = [20]
        self._pulse_growing = [True]
        self._pulse_active = True

        # ENTER em vez de SPACE — evita conflito com keyboard global
        root.bind("<Return>", self._on_confirm)
        root.bind("<KP_Enter>", self._on_confirm)

        self._animate_pulse(canvas)

    def _animate_pulse(self, canvas):
        if not self._pulse_active:
            return
        canvas.delete("dot")
        r = self._pulse_r[0]
        cx, cy = self._pulse_cx, self._pulse_cy
        canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                           fill="#00FF88", outline="white", width=2, tags="dot")
        if self._pulse_growing[0]:
            self._pulse_r[0] += 1
            if self._pulse_r[0] >= 28:
                self._pulse_growing[0] = False
        else:
            self._pulse_r[0] -= 1
            if self._pulse_r[0] <= 16:
                self._pulse_growing[0] = True
        self._root.after(30, lambda: self._animate_pulse(canvas))

    def _on_confirm(self, event=None):
        self._pulse_active = False
        self._confirmed.set()

    def show_point(self, monitor, point_idx, label):
        """Enfileira exibição de ponto (thread-safe)."""
        self._cmd_queue.put(("show_point", (monitor, point_idx, label)))

    def wait_for_confirm(self):
        """Bloqueia até o usuário pressionar ENTER."""
        self._confirmed.wait()
        self._confirmed.clear()

    def hide(self):
        """Esconde a janela (thread-safe)."""
        self._cmd_queue.put(("hide", ()))

    def destroy(self):
        """Encerra a sessão Tkinter de forma segura (thread-safe)."""
        self._cmd_queue.put(("destroy", ()))
        self._thread.join(timeout=3)


class Calibration:
    def __init__(self):
        self._homographies = {}  # monitor_id → H (3x3 numpy)

    def run_calibration(self, monitors, gaze_tracker):
        """
        Conduz calibração interativa para cada monitor.
        monitors: lista de dicts com left/top/right/bottom/id/name
        gaze_tracker: instância de GazeTracker com get_gaze()
        """
        session = _TkCalibrationSession()

        try:
            for monitor in monitors:
                mid = monitor["id"]
                name = monitor["name"]
                print(f"\n[Calibração] Monitor {mid+1} de {len(monitors)}: {name}")
                print(f"[Calibração] Dispositivo: {monitor.get('device', '?')}")

                src_points = []
                dst_points = []

                for i, label in enumerate(POINT_LABELS):
                    px, py, nx, ny = _normalized_point_pos(monitor, i)
                    print(f"  → Ponto {i+1}/5: {label} — olhe para o ponto e pressione ENTER")

                    session.show_point(monitor, i, f"{label} — Monitor: {name}")
                    session.wait_for_confirm()

                    # Coleta amostras por 1 segundo após confirmação
                    samples = []
                    deadline = time.time() + 1.0
                    while time.time() < deadline:
                        g = gaze_tracker.get_gaze()
                        if g is not None:
                            samples.append(g)
                        time.sleep(0.033)

                    if len(samples) < 3:
                        print(f"  [AVISO] Poucas amostras ({len(samples)}) — rosto não detectado?")
                        print(f"  [AVISO] Verifique CAMERA_INDEX em config.py e iluminação.")
                        samples = [(0.5, 0.5)]

                    gx = sum(s[0] for s in samples) / len(samples)
                    gy = sum(s[1] for s in samples) / len(samples)
                    src_points.append([gx, gy])
                    dst_points.append([float(px), float(py)])
                    print(f"  ✓ Gaze ({gx:.3f}, {gy:.3f}) → Pixel ({px}, {py})")

                src = np.float32(src_points)
                dst = np.float32(dst_points)
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC)

                if H is None:
                    print(f"  [ERRO] Homografia falhou para monitor {mid} — pontos degenerados.")
                    print(f"  [ERRO] Valores de gaze y={[round(p[1],3) for p in src_points]}")
                    print(f"  [ERRO] Verifique se o tracker está detectando o rosto corretamente.")
                    continue

                self._homographies[mid] = H
                print(f"[Calibração] Monitor {mid} calibrado.")

                session.hide()

        finally:
            session.destroy()

        if not self._homographies:
            print("[Calibração] Nenhum monitor foi calibrado com sucesso.")
            return

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
        data = {str(k): v.tolist() for k, v in self._homographies.items()
                if v is not None}
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
        except Exception as e:
            print(f"[Calibração] Erro ao carregar {path}: {e} — rode Ctrl+Alt+C para calibrar.")
            return False

    def is_calibrated(self):
        return len(self._homographies) > 0
