# glaze-app/calibration.py
"""
Calibração 5 pontos por monitor (centro + 4 cantos).
Calcula homografia 2D: gaze normalizado → coordenada absoluta de desktop (px).

Após a homografia, executa fase de refinamento gain/bias (3 pontos de validação)
que calcula correção linear por eixo para reduzir erro residual.
"""
import json
import time
import threading
import queue
import numpy as np
import cv2
from config import CALIBRATION_FILE, CALIBRATION_SAMPLES


def _poly_features(gx: float, gy: float) -> "np.ndarray":
    """Degree-2 polynomial feature vector: [1, gx, gy, gx², gx·gy, gy²]."""
    return np.array([1.0, gx, gy, gx * gx, gx * gy, gy * gy])


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


# Segundos de coleta antes de liberar o ENTER
_COLLECT_SECS = 2.0


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

    Confirmação via ENTER ou SPACE.
    """

    def __init__(self):
        self._cmd_queue = queue.Queue()
        self._confirmed = threading.Event()
        self._start_event = threading.Event()
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
                elif cmd == "show_val_point":
                    self._do_show_val_point(*args)
                elif cmd == "progress":
                    self._do_update_progress(*args)
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

        self._instr_text = canvas.create_text(w//2, h-60,
                           text="Olhe para o ponto e pressione ENTER para iniciar a coleta",
                           fill="white", font=("Arial", 16))
        canvas.create_text(w//2, h-30, text=label,
                           fill="#00FF88", font=("Arial", 12))

        # Barra de progresso de coleta
        bar_w, bar_h = 300, 16
        bx = w // 2 - bar_w // 2
        by = h - 90
        canvas.create_rectangle(bx, by, bx + bar_w, by + bar_h,
                                 outline="#555555", fill="#222222", tags="bar_bg")
        self._bar_rect = canvas.create_rectangle(bx, by, bx, by + bar_h,
                                                  outline="", fill="#00FF88", tags="bar_fill")
        self._bar_bx = bx
        self._bar_w  = bar_w
        self._bar_by = by
        self._bar_h  = bar_h

        self._confirmed.clear()
        self._collecting = False   # aguardando primeiro ENTER
        self._collect_ready = False
        self._pulse_cx = cx
        self._pulse_cy = cy
        self._pulse_r = [20]
        self._pulse_growing = [True]
        self._pulse_active = True

        root.bind("<Return>", self._on_confirm)
        root.bind("<KP_Enter>", self._on_confirm)
        root.bind("<space>", self._on_confirm)

        self._animate_pulse(canvas)

    def _do_show_val_point(self, monitor, px_abs, py_abs, label):
        """Handler Tk para exibir ponto de validação por coordenada absoluta."""
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

        # Converte coordenada absoluta para relativa à janela do monitor
        cx = px_abs - l
        cy = py_abs - t

        self._instr_text = canvas.create_text(w//2, h-60,
                           text="[Refinamento] Olhe para o ponto e pressione ENTER para iniciar a coleta",
                           fill="white", font=("Arial", 16))
        canvas.create_text(w//2, h-30, text=label,
                           fill="#FFAA00", font=("Arial", 12))

        # Barra de progresso de coleta
        bar_w, bar_h = 300, 16
        bx = w // 2 - bar_w // 2
        by = h - 90
        canvas.create_rectangle(bx, by, bx + bar_w, by + bar_h,
                                 outline="#555555", fill="#222222", tags="bar_bg")
        self._bar_rect = canvas.create_rectangle(bx, by, bx, by + bar_h,
                                                  outline="", fill="#FFAA00", tags="bar_fill")
        self._bar_bx = bx
        self._bar_w  = bar_w
        self._bar_by = by
        self._bar_h  = bar_h

        self._confirmed.clear()
        self._collecting = False
        self._collect_ready = False
        self._pulse_cx = cx
        self._pulse_cy = cy
        self._pulse_r = [20]
        self._pulse_growing = [True]
        self._pulse_active = True

        root.bind("<Return>", self._on_confirm)
        root.bind("<KP_Enter>", self._on_confirm)
        root.bind("<space>", self._on_confirm)

        self._animate_pulse(canvas)

    def _animate_pulse(self, canvas):
        if not self._pulse_active:
            return
        canvas.delete("dot")
        r = self._pulse_r[0]
        cx, cy = self._pulse_cx, self._pulse_cy
        # Desenha contorno internamente: reduz bbox do outline em 1px para não vazar
        canvas.create_oval(cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1,
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

    def update_progress(self, ratio):
        """Atualiza a barra de progresso (0.0–1.0). Thread-safe via after."""
        self._cmd_queue.put(("progress", (ratio,)))

    def _do_update_progress(self, ratio):
        fill_w = int(self._bar_w * min(1.0, max(0.0, ratio)))
        self._canvas.coords(
            self._bar_rect,
            self._bar_bx, self._bar_by,
            self._bar_bx + fill_w, self._bar_by + self._bar_h,
        )
        if ratio >= 1.0 and not self._collect_ready:
            self._collect_ready = True
            self._canvas.itemconfig(
                self._instr_text,
                text="Coleta concluída — pressione ENTER para confirmar"
            )

    def _on_confirm(self, event=None):
        if not self._collecting:
            # Primeiro ENTER: inicia coleta
            self._collecting = True
            self._canvas.itemconfig(
                self._instr_text,
                text="Coletando... aguarde a barra encher"
            )
            self._start_event.set()
            return
        if not self._collect_ready:
            return  # coleta ainda em andamento
        # Segundo ENTER: confirma ponto
        self._pulse_active = False
        self._confirmed.set()

    def show_point(self, monitor, point_idx, label):
        """Enfileira exibição de ponto (thread-safe)."""
        self._start_event.clear()
        self._cmd_queue.put(("show_point", (monitor, point_idx, label)))

    def show_validation_point(self, monitor, px_abs, py_abs, label):
        """Enfileira exibição de ponto de validação por coordenada absoluta (thread-safe)."""
        self._start_event.clear()
        self._cmd_queue.put(("show_val_point", (monitor, px_abs, py_abs, label)))

    def wait_for_start(self):
        """Bloqueia até o usuário pressionar ENTER pela primeira vez (inicia coleta)."""
        self._start_event.wait()
        self._start_event.clear()

    def wait_for_confirm(self):
        """Bloqueia até o usuário pressionar ENTER pela segunda vez (confirma ponto)."""
        self._confirmed.wait()
        self._confirmed.clear()

    def hide(self):
        """Esconde a janela (thread-safe)."""
        self._cmd_queue.put(("hide", ()))

    def destroy(self):
        """Esconde a janela e deixa a thread daemon morrer naturalmente.
        Não chama root.destroy() de outra thread — causa Tcl_AsyncDelete."""
        self._cmd_queue.put(("hide", ()))


class Calibration:
    def __init__(self):
        self._homographies = {}  # monitor_id → H (3x3 numpy)
        self._corrections = {}   # monitor_id → (gain_x, bias_x, gain_y, bias_y)

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

                    # Aguarda primeiro ENTER para iniciar coleta
                    session.wait_for_start()

                    # Pequeno warm-up para o tracker estabilizar
                    time.sleep(0.3)

                    # Coleta samples durante _COLLECT_SECS
                    samples = []
                    none_count = 0
                    t_start = time.time()
                    while True:
                        elapsed = time.time() - t_start
                        ratio = elapsed / _COLLECT_SECS
                        session.update_progress(ratio)
                        g = gaze_tracker.get_gaze()
                        if g is not None:
                            samples.append(g)
                        else:
                            none_count += 1
                        if elapsed >= _COLLECT_SECS:
                            break
                        time.sleep(0.033)

                    # Aguarda segundo ENTER para confirmar
                    session.wait_for_confirm()

                    print(f"  [DBG] samples={len(samples)} none={none_count} total={len(samples)+none_count}")

                    if len(samples) < 3:
                        print(f"  [AVISO] Poucas amostras ({len(samples)}) — rosto não detectado?")
                        print(f"  [AVISO] Verifique CAMERA_INDEX em config.py e iluminação.")
                        samples = [(0.5, 0.5)]

                    gx = sum(s[0] for s in samples) / len(samples)
                    gy = sum(s[1] for s in samples) / len(samples)
                    src_points.append([gx, gy])
                    dst_points.append([float(px), float(py)])
                    print(f"  ✓ Gaze ({gx:.3f}, {gy:.3f}) → Pixel ({px}, {py}) [{len(samples)} amostras]")

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

                # Fase de refinamento gain/bias após homografia
                self._run_refinement(session, monitor, gaze_tracker)

                session.hide()

        finally:
            session.destroy()

        if not self._homographies:
            print("[Calibração] Nenhum monitor foi calibrado com sucesso.")
            return

        self.save(CALIBRATION_FILE)
        print(f"\n[Calibração] Salvo em {CALIBRATION_FILE}. Retomando tracking.")

    def _run_refinement(self, session, monitor, gaze_tracker):
        """
        Fase opcional de refinamento gain/bias após a homografia.
        Mostra 3 pontos de validação, coleta gaze, calcula correção linear por eixo.
        Se qualquer ponto tiver amostras insuficientes, o refinamento é pulado.
        """
        mid = monitor["id"]
        l, t = monitor["left"], monitor["top"]
        w = monitor["right"] - l
        h = monitor["bottom"] - t

        # Pontos de validação: (nx, ny normalizado, label)
        val_points = [
            (0.5,  0.5,  "Centro"),
            (0.95, 0.05, "Canto superior direito"),
            (0.05, 0.95, "Canto inferior esquerdo"),
        ]

        print(f"\n[Refinamento] Monitor {mid} — fase de ajuste gain/bias ({len(val_points)} pontos)")

        pred_xs, pred_ys = [], []
        real_xs, real_ys = [], []

        for nx, ny, label in val_points:
            real_x = int(l + nx * w)
            real_y = int(t + ny * h)

            print(f"  → Refinamento: {label} — olhe para o ponto e pressione ENTER")
            session.show_validation_point(
                monitor, real_x, real_y,
                f"[Refinamento] {label} — Monitor: {monitor['name']}"
            )

            # Aguarda primeiro ENTER para iniciar coleta
            session.wait_for_start()
            time.sleep(0.3)

            # Coleta samples durante _COLLECT_SECS
            samples = []
            none_count = 0
            t_start = time.time()
            while True:
                elapsed = time.time() - t_start
                ratio = elapsed / _COLLECT_SECS
                session.update_progress(ratio)
                g = gaze_tracker.get_gaze()
                if g is not None:
                    samples.append(g)
                else:
                    none_count += 1
                if elapsed >= _COLLECT_SECS:
                    break
                time.sleep(0.033)

            # Aguarda segundo ENTER para confirmar
            session.wait_for_confirm()

            print(f"  [DBG-ref] samples={len(samples)} none={none_count}")

            if len(samples) < 3:
                print(f"  [Refinamento] Poucas amostras em '{label}' — pulando refinamento do monitor {mid}")
                return

            gx_raw = sum(s[0] for s in samples) / len(samples)
            gy_raw = sum(s[1] for s in samples) / len(samples)

            # Aplica homografia para obter predição atual (antes da correção)
            pred = self.apply(mid, gx_raw, gy_raw, _skip_correction=True)
            if pred is None:
                print(f"  [Refinamento] apply() retornou None — pulando refinamento do monitor {mid}")
                return

            pred_xs.append(pred[0])
            pred_ys.append(pred[1])
            real_xs.append(real_x)
            real_ys.append(real_y)
            print(f"  ✓ Pred ({pred[0]}, {pred[1]}) → Real ({real_x}, {real_y})")

        # Regressão linear 1D por eixo: real = gain * pred + bias
        def fit_1d(preds, reals):
            n = len(preds)
            sum_p  = sum(preds)
            sum_r  = sum(reals)
            sum_pp = sum(p * p for p in preds)
            sum_pr = sum(p * r for p, r in zip(preds, reals))
            denom  = n * sum_pp - sum_p ** 2
            if abs(denom) < 1e-6:
                return 1.0, 0.0  # sem correção — pontos colineares ou idênticos
            gain = (n * sum_pr - sum_p * sum_r) / denom
            bias = (sum_r - gain * sum_p) / n
            return gain, bias

        gain_x, bias_x = fit_1d(pred_xs, real_xs)
        gain_y, bias_y = fit_1d(pred_ys, real_ys)
        self._corrections[mid] = (gain_x, bias_x, gain_y, bias_y)
        print(
            f"  [Refinamento] Monitor {mid}: "
            f"gain_x={gain_x:.3f} bias_x={bias_x:.1f} | "
            f"gain_y={gain_y:.3f} bias_y={bias_y:.1f}"
        )

    def apply(self, monitor_id, x_norm, y_norm, _skip_correction=False):
        """
        Mapeia gaze normalizado → (x_abs, y_abs) em pixels.
        Retorna None se não calibrado.

        _skip_correction: se True, ignora a correção gain/bias (uso interno do refinamento).
        """
        H = self._homographies.get(monitor_id)
        if H is None:
            return None
        pt = np.float32([[[x_norm, y_norm]]])
        result = cv2.perspectiveTransform(pt, H)
        x, y = result[0][0]
        # Aplica correção gain/bias se disponível e não estiver sendo chamado pelo refinamento
        if not _skip_correction and monitor_id in self._corrections:
            gx, bx, gy, by = self._corrections[monitor_id]
            x = gx * x + bx
            y = gy * y + by
        return int(x), int(y)

    def save(self, path=None):
        path = path or CALIBRATION_FILE
        data = {
            "homographies": {
                str(k): v.tolist()
                for k, v in self._homographies.items()
                if v is not None
            },
            "corrections": {
                str(k): list(v)
                for k, v in self._corrections.items()
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path=None):
        path = path or CALIBRATION_FILE
        try:
            with open(path) as f:
                data = json.load(f)

            # Suporte a formato antigo: dict plano de homografias (sem chave "homographies")
            if "homographies" in data:
                # Formato novo
                self._homographies = {
                    int(k): np.array(v)
                    for k, v in data["homographies"].items()
                }
                self._corrections = {
                    int(k): tuple(v)
                    for k, v in data.get("corrections", {}).items()
                }
            else:
                # Formato antigo: cada chave é um monitor_id, valor é a homografia
                self._homographies = {int(k): np.array(v) for k, v in data.items()}
                self._corrections = {}

            n_corr = len(self._corrections)
            print(
                f"[Calibração] Carregado de {path} "
                f"({len(self._homographies)} monitores, {n_corr} correções)"
            )
            return True
        except FileNotFoundError:
            print(f"[Calibração] {path} não encontrado — rode Ctrl+Alt+C para calibrar.")
            return False
        except Exception as e:
            print(f"[Calibração] Erro ao carregar {path}: {e} — rode Ctrl+Alt+C para calibrar.")
            return False

    def is_calibrated(self):
        return len(self._homographies) > 0
