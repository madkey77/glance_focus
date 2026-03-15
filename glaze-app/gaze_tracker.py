# glaze-app/gaze_tracker.py
"""
GazeTracker — encapsula MediaPipe FaceLandmarker (nova API 0.10+) e lógica de gaze.
Roda captura em thread separada. Expõe get_gaze() → (x, y) normalizado [0..1] ou None.

Requer: face_landmarker.task na pasta glaze-app/
Download: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

Baseado em: https://github.com/JEOresearch/EyeTracker/tree/main/Webcam3DTracker
"""
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import math
import threading
from collections import deque
from config import CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT, GAZE_SMOOTH_FRAMES

MODEL_PATH = "face_landmarker.task"

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

    # Vetor base da cabeça: PCA Z aponta para a câmera (Z negativo em imagem),
    # então R[:,2] já é o forward correto (sem negação)
    head_forward = R[:, 2]

    # Aplica rotações de yaw/pitch conforme offset da íris
    yaw   = -offset[0] * 0.8   # horizontal
    pitch =  offset[1] * 0.8   # vertical

    gaze = _rot_y(yaw) @ _rot_x(pitch) @ head_forward
    return _normalize(gaze)


class GazeTracker:
    """
    Captura webcam e estima gaze normalizado [0..1] em thread de background.
    Usa a nova API mediapipe.tasks (0.10+) com FaceLandmarker em modo VIDEO.

    Requer face_landmarker.task na mesma pasta que main.py.

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

        # Calibração de centro
        self._calib_yaw   = 0.0
        self._calib_pitch = 0.0
        self._calibrated  = False

        # Nova API: FaceLandmarker em modo VIDEO (síncrono por frame)
        base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self._detector = mp_vision.FaceLandmarker.create_from_options(options)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._detector.close()

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
            return self._gaze

    def _loop(self):
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

        import time as _time
        _start = _time.time()

        while self._running:
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]

            # Nova API: converter para mp.Image e chamar detect_for_video
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            # CAP_PROP_POS_MSEC é sempre 0 em câmeras ao vivo — usar wall clock
            timestamp_ms = int((_time.time() - _start) * 1000)

            result = self._detector.detect_for_video(mp_image, timestamp_ms)

            # Nova API: result.face_landmarks em vez de result.multi_face_landmarks
            if not result.face_landmarks:
                with self._lock:
                    self._gaze = None
                continue

            # Os landmarks têm os mesmos atributos .x .y .z normalizados
            lm = result.face_landmarks[0]

            R = _compute_head_rotation(lm, w, h, self._ref_nose)
            gaze_dir = _compute_gaze_direction(lm, R, w, h)

            # Converte direção 3D → ângulos yaw/pitch normalizados
            yaw   = math.atan2(gaze_dir[0], -gaze_dir[2])
            pitch = math.atan2(gaze_dir[1], -gaze_dir[2])

            # Aplica offset de calibração
            yaw   -= self._calib_yaw
            pitch -= self._calib_pitch

            # Normaliza para [0..1] (range ±45 graus horizontal, ±35 vertical)
            x_norm = 0.5 + yaw   / math.radians(90)
            y_norm = 0.5 + pitch / math.radians(70)
            x_norm = max(0.0, min(1.0, x_norm))
            y_norm = max(0.0, min(1.0, y_norm))

            self._smooth_buf.append((x_norm, y_norm))
            xs = sum(p[0] for p in self._smooth_buf) / len(self._smooth_buf)
            ys = sum(p[1] for p in self._smooth_buf) / len(self._smooth_buf)

            with self._lock:
                self._gaze = (xs, ys)

        cap.release()
