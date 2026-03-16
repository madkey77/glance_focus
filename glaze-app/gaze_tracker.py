# glaze-app/gaze_tracker.py
"""
GazeTracker — MediaPipe FaceLandmarker (API 0.10+) com facial_transformation_matrix.

Usa a matriz de transformação fornecida pelo próprio MediaPipe para head pose
estável, sem PCA manual. Combina com desvio da íris para estimativa de gaze.

Requer: face_landmarker.task na pasta glaze-app/
Download: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
"""
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import math
import threading
from config import CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT, GAZE_PITCH_IRIS_WEIGHT, GAZE_Y_RANGE_DEG

MODEL_PATH = "face_landmarker.task"
SACCADE_THRESHOLD = 0.04  # velocidade mínima (norm/frame) para detectar saccade


class _OneEuroFilter:
    """One Euro Filter (Casiez et al., CHI 2012).

    Filtro adaptativo que suaviza mais em repouso e menos durante movimento
    rápido, eliminando o lag fixo de uma média móvel simples.

    Args:
        freq:       frequência de amostragem estimada (Hz)
        min_cutoff: suavização mínima (Hz) — menor = mais suave em repouso
        beta:       coeficiente de velocidade — maior = mais responsivo a movimento
        d_cutoff:   cutoff da derivada (Hz)
    """

    def __init__(self, freq: float, min_cutoff: float = 1.0,
                 beta: float = 0.007, d_cutoff: float = 1.0):
        self._freq = freq
        self._min_cutoff = min_cutoff
        self._beta = beta
        self._d_cutoff = d_cutoff
        self._x_prev = None
        self._dx_prev = 0.0

    def _alpha(self, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        te = 1.0 / self._freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x: float) -> float:
        if self._x_prev is None:
            self._x_prev = x
            return x
        # Derivada filtrada
        dx = (x - self._x_prev) * self._freq
        a_d = self._alpha(self._d_cutoff)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev
        # Cutoff adaptativo
        cutoff = self._min_cutoff + self._beta * abs(dx_hat)
        # Valor filtrado
        a = self._alpha(cutoff)
        x_hat = a * x + (1.0 - a) * self._x_prev
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat

# ── índices de landmarks ─────────────────────────────────────────────────────
LEFT_IRIS         = [474, 475, 476, 477]
RIGHT_IRIS        = [469, 470, 471, 472]
LEFT_EYE_CORNERS  = [33, 133]
RIGHT_EYE_CORNERS = [362, 263]


def _normalize(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _get_iris_center(landmarks, indices, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in indices])
    return pts.mean(axis=0)


def _get_eye_center(landmarks, corners, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in corners])
    return pts.mean(axis=0)


def _compute_gaze(landmarks, face_matrix, w, h):
    """
    Estima direção do gaze combinando:
      - head_forward: extraído da facial_transformation_matrix do MediaPipe
        (coluna Z negada = direção que a face aponta para fora da tela)
      - iris_offset: desvio normalizado da íris em relação ao centro do olho

    face_matrix: np.ndarray 4x4 — transformação face→câmera fornecida pelo MediaPipe.
    A coluna 2 (eixo Z) da parte 3x3 de rotação aponta para fora da face,
    portanto head_forward = R[:,2] (sem negação, convenção MediaPipe).
    """
    R = face_matrix[:3, :3]
    head_forward = _normalize(R[:, 2])

    # Desvio da íris em pixels
    l_iris = _get_iris_center(landmarks, LEFT_IRIS,  w, h)
    r_iris = _get_iris_center(landmarks, RIGHT_IRIS, w, h)
    l_eye  = _get_eye_center(landmarks, LEFT_EYE_CORNERS,  w, h)
    r_eye  = _get_eye_center(landmarks, RIGHT_EYE_CORNERS, w, h)

    l_off = l_iris - l_eye
    r_off = r_iris - r_eye
    offset = (l_off + r_off) * 0.5

    # Normaliza pelo tamanho do olho
    eye_w = np.linalg.norm(
        np.array([landmarks[133].x - landmarks[33].x,
                  landmarks[133].y - landmarks[33].y]) * w
    )
    if eye_w > 1e-6:
        offset /= eye_w

    # Aplica rotação de yaw/pitch pelo offset da íris sobre head_forward
    # Sensibilidade: 1.5 é mais responsivo que 0.8 para movimentos sutis dos olhos
    yaw_iris   =  offset[0] * 1.4
    pitch_iris =  offset[1] * GAZE_PITCH_IRIS_WEIGHT

    # Matrizes de rotação 3D
    def rot_x(a):
        ca, sa = math.cos(a), math.sin(a)
        return np.array([[1,0,0],[0,ca,-sa],[0,sa,ca]], dtype=float)

    def rot_y(a):
        ca, sa = math.cos(a), math.sin(a)
        return np.array([[ca,0,sa],[0,1,0],[-sa,0,ca]], dtype=float)

    gaze = rot_y(yaw_iris) @ rot_x(pitch_iris) @ head_forward
    return _normalize(gaze), head_forward, offset


class GazeTracker:
    """
    Captura webcam e estima gaze normalizado [0..1] em thread de background.
    Usa facial_transformation_matrix do FaceLandmarker para head pose estável.

    Requer face_landmarker.task na mesma pasta que main.py.

    Uso:
        tracker = GazeTracker()
        tracker.start()
        gaze = tracker.get_gaze()   # (x, y) ou None
        tracker.stop()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._gaze = None
        self._running = False
        self._thread = None
        self._filter_x = _OneEuroFilter(freq=30.0)
        self._filter_y = _OneEuroFilter(freq=30.0)
        self._prev_gaze = None

        self._calib_yaw   = 0.0
        self._calib_pitch = 0.0

        base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,  # ← matriz estável do MP
        )
        self._detector = mp_vision.FaceLandmarker.create_from_options(options)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._prev_gaze = None
        if self._thread:
            self._thread.join(timeout=2)
        self._detector.close()

    def calibrate_center(self):
        """Captura gaze atual como ponto de centro."""
        with self._lock:
            g = self._gaze
        if g:
            self._calib_yaw, self._calib_pitch = g
            print("[GazeTracker] Centro calibrado.")

    def get_gaze(self):
        """Retorna (x, y) normalizados [0..1] ou None."""
        with self._lock:
            return self._gaze

    def _loop(self):
        import time as _time
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        real_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        real_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[GazeTracker] Câmera aberta: {real_w}x{real_h}")
        _start = _time.time()
        _frame_count = 0
        _detect_count = 0

        while self._running:
            ret, frame = cap.read()
            if not ret:
                continue
            _frame_count += 1

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int((_time.time() - _start) * 1000)

            result = self._detector.detect_for_video(mp_image, timestamp_ms)

            if not result.face_landmarks or not result.facial_transformation_matrixes:
                with self._lock:
                    self._gaze = None
                if _frame_count % 30 == 0:
                    print(f"[GazeTracker] frames={_frame_count} detect={_detect_count} ts={timestamp_ms} shape={frame.shape} — sem rosto")
                continue
            _detect_count += 1

            lm = result.face_landmarks[0]
            face_matrix = np.array(result.facial_transformation_matrixes[0].data).reshape(4, 4)

            gaze_dir, _, _ = _compute_gaze(lm, face_matrix, w, h)

            # Ângulos a partir do gaze_dir
            # gaze_dir aponta para fora da face no espaço câmera:
            #   X+ = direita, Y+ = baixo (imagem), Z+ = longe da câmera
            yaw   = math.atan2(gaze_dir[0],  gaze_dir[2])
            pitch = math.atan2(-gaze_dir[1], gaze_dir[2])

            yaw   -= self._calib_yaw
            pitch -= self._calib_pitch

            # Normaliza: range ±75° horizontal, ±65° vertical (rosto menos sensível)
            x_norm = max(0.0, min(1.0, 0.5 + yaw   / math.radians(150)))
            y_norm = max(0.0, min(1.0, 0.5 + pitch  / math.radians(GAZE_Y_RANGE_DEG)))

            # Saccade detection: se velocidade exceder threshold, passa bruto e
            # reseta os filtros para evitar que puxem o gaze de volta ao valor antigo.
            if self._prev_gaze is not None:
                dx = x_norm - self._prev_gaze[0]
                dy = y_norm - self._prev_gaze[1]
                speed = math.sqrt(dx * dx + dy * dy)
                if speed > SACCADE_THRESHOLD:
                    xs, ys = x_norm, y_norm
                    self._filter_x = _OneEuroFilter(freq=30.0)
                    self._filter_y = _OneEuroFilter(freq=30.0)
                else:
                    xs = self._filter_x(x_norm)
                    ys = self._filter_y(y_norm)
            else:
                xs = self._filter_x(x_norm)
                ys = self._filter_y(y_norm)

            self._prev_gaze = (x_norm, y_norm)

            with self._lock:
                self._gaze = (xs, ys)

        cap.release()
