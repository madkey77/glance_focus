# glaze-app/gaze_tracker.py
"""
GazeTracker — encapsula MediaPipe FaceMesh e lógica de gaze.
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
