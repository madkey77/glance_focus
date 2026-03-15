# glaze-app/debug_gaze.py
"""
Preview visual do gaze tracking com facial_transformation_matrix.
Pressione Q para sair, S para screenshot.
"""
import sys, math, time
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

try:
    from config import CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT
except ImportError:
    CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT = 0, 480, 360

MODEL_PATH = "face_landmarker.task"

LEFT_IRIS         = [474, 475, 476, 477]
RIGHT_IRIS        = [469, 470, 471, 472]
LEFT_EYE_CORNERS  = [33, 133]
RIGHT_EYE_CORNERS = [362, 263]


def _normalize(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def _iris_center(lm, idx, w, h):
    pts = np.array([[lm[i].x*w, lm[i].y*h] for i in idx])
    return pts.mean(axis=0)


def _eye_center(lm, idx, w, h):
    pts = np.array([[lm[i].x*w, lm[i].y*h] for i in idx])
    return pts.mean(axis=0)


def compute_gaze(lm, face_matrix, w, h):
    R = face_matrix[:3, :3]
    head_fwd = _normalize(R[:, 2])

    l_iris = _iris_center(lm, LEFT_IRIS,  w, h)
    r_iris = _iris_center(lm, RIGHT_IRIS, w, h)
    l_eye  = _eye_center(lm, LEFT_EYE_CORNERS,  w, h)
    r_eye  = _eye_center(lm, RIGHT_EYE_CORNERS, w, h)
    offset = ((l_iris-l_eye) + (r_iris-r_eye)) * 0.5

    eye_w = np.linalg.norm(np.array([lm[133].x-lm[33].x, lm[133].y-lm[33].y])*w)
    if eye_w > 1e-6:
        offset /= eye_w

    yaw_i   = -offset[0] * 1.5
    pitch_i =  offset[1] * 1.5

    def rx(a):
        ca,sa = math.cos(a),math.sin(a)
        return np.array([[1,0,0],[0,ca,-sa],[0,sa,ca]], dtype=float)
    def ry(a):
        ca,sa = math.cos(a),math.sin(a)
        return np.array([[ca,0,sa],[0,1,0],[-sa,0,ca]], dtype=float)

    gaze = _normalize(ry(yaw_i) @ rx(pitch_i) @ head_fwd)
    yaw   = math.atan2(gaze[0],  gaze[2])
    pitch = math.atan2(-gaze[1], gaze[2])
    x_norm = max(0., min(1., 0.5 + yaw   / math.radians(90)))
    y_norm = max(0., min(1., 0.5 + pitch / math.radians(70)))
    return gaze, head_fwd, offset, yaw, pitch, x_norm, y_norm, l_iris, r_iris, l_eye, r_eye


def draw_text(img, text, pos, scale=0.5, color=(255,255,255), bg=True):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw,th),_ = cv2.getTextSize(text, font, scale, 1)
    x,y = pos
    if bg:
        cv2.rectangle(img,(x-2,y-th-2),(x+tw+2,y+3),(0,0,0),-1)
    cv2.putText(img, text, (x,y), font, scale, color, 1, cv2.LINE_AA)


def draw_iris(img, lm, idx, w, h, color=(0,255,128)):
    pts = np.array([[lm[i].x*w, lm[i].y*h] for i in idx], dtype=int)
    c = pts.mean(axis=0).astype(int)
    r = int(np.linalg.norm(pts-c, axis=1).mean()) + 2
    cv2.circle(img, tuple(c), r, color, 2)
    cv2.circle(img, tuple(c), 2, color, -1)
    return c


def draw_arrow(img, origin, direction_2d, length=55, color=(0,255,128)):
    ox, oy = int(origin[0]), int(origin[1])
    dx, dy = direction_2d
    n = math.sqrt(dx*dx+dy*dy)
    if n < 1e-6: return
    ex = int(ox + dx/n*length)
    ey = int(oy + dy/n*length)
    cv2.arrowedLine(img, (ox,oy),(ex,ey), color, 2, tipLength=0.3)


def draw_gaze_box(img, x_norm, y_norm, px, py, size=110):
    cv2.rectangle(img,(px,py),(px+size,py+size),(50,50,50),-1)
    cv2.rectangle(img,(px,py),(px+size,py+size),(160,160,160),1)
    mid = px+size//2, py+size//2
    cv2.line(img,(mid[0],py),(mid[0],py+size),(80,80,80),1)
    cv2.line(img,(px,mid[1]),(px+size,mid[1]),(80,80,80),1)
    gx = max(px+4, min(px+size-4, int(px+x_norm*size)))
    gy = max(py+4, min(py+size-4, int(py+y_norm*size)))
    cv2.circle(img,(gx,gy),7,(0,255,128),-1)
    cv2.circle(img,(gx,gy),7,(255,255,255),1)
    draw_text(img,"GAZE",(px,py+size+14),scale=0.38,color=(160,160,160),bg=False)


def main():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
    if not cap.isOpened():
        print("Erro: câmera não encontrada."); sys.exit(1)

    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=True,
    )
    detector = mp_vision.FaceLandmarker.create_from_options(options)
    print("Pronto. Q=sair  S=screenshot")

    start = time.time()
    shot_n = 0

    while True:
        ret, frame = cap.read()
        if not ret: continue

        h, w = frame.shape[:2]
        disp = frame.copy()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts = int((time.time()-start)*1000)
        result = detector.detect_for_video(mp_img, ts)

        if not result.face_landmarks or not result.facial_transformation_matrixes:
            draw_text(disp, "Rosto nao detectado", (10,h-10), color=(0,0,255))
            cv2.imshow("Glaze Debug", disp)
            if cv2.waitKey(1)&0xFF in (ord('q'),ord('Q')): break
            continue

        lm = result.face_landmarks[0]
        M = np.array(result.facial_transformation_matrixes[0].data).reshape(4,4)
        gaze, hfwd, offset, yaw_r, pitch_r, xn, yn, l_iris, r_iris, l_eye, r_eye = \
            compute_gaze(lm, M, w, h)

        # Íris
        lc = draw_iris(disp, lm, LEFT_IRIS,  w, h)
        rc = draw_iris(disp, lm, RIGHT_IRIS, w, h)

        # Centros dos olhos
        for c in [l_eye.astype(int), r_eye.astype(int)]:
            cv2.circle(disp, tuple(c), 3, (255,200,0), -1)

        # Setas de gaze (projeção 2D do gaze_dir)
        draw_arrow(disp, lc, (gaze[0], gaze[1]))
        draw_arrow(disp, rc, (gaze[0], gaze[1]))

        # Nariz
        np4 = (int(lm[4].x*w), int(lm[4].y*h))
        cv2.circle(disp, np4, 4, (0,128,255), -1)

        # Eixos da face_matrix no nariz (X=vermelho, Y=verde, Z=laranja)
        R = M[:3,:3]
        sc = 50
        for col, color in [(0,(0,0,255)),(1,(0,220,0)),(2,(255,128,0))]:
            ex = int(np4[0]+R[0,col]*sc)
            ey = int(np4[1]+R[1,col]*sc)
            cv2.arrowedLine(disp, np4, (ex,ey), color, 2, tipLength=0.3)

        # Gaze box (canto superior direito)
        draw_gaze_box(disp, xn, yn, w-130, 10, size=115)

        # HUD
        yaw_deg   = math.degrees(yaw_r)
        pitch_deg = math.degrees(pitch_r)
        lines = [
            f"yaw:   {yaw_deg:+7.1f} deg   x_norm: {xn:.3f}",
            f"pitch: {pitch_deg:+7.1f} deg   y_norm: {yn:.3f}",
            f"head_fwd: ({hfwd[0]:+.2f},{hfwd[1]:+.2f},{hfwd[2]:+.2f})",
            f"iris_off: ({offset[0]:+.3f},{offset[1]:+.3f})",
        ]
        for i, line in enumerate(lines):
            draw_text(disp, line, (10, h-10-(len(lines)-1-i)*20), scale=0.46)

        cv2.imshow("Glaze Debug", disp)
        k = cv2.waitKey(1)&0xFF
        if k in (ord('q'),ord('Q')): break
        if k in (ord('s'),ord('S')):
            fn = f"debug_screenshot_{shot_n}.png"
            cv2.imwrite(fn, disp)
            print(f"Screenshot salvo: {fn}")
            shot_n += 1

    cap.release()
    detector.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
