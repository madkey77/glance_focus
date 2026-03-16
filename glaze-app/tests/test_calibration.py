# glaze-app/tests/test_calibration.py
import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
from calibration import Calibration


def test_apply_homography_identity():
    """Homografia identidade: ponto (0.5, 0.5) deve mapear para centro do monitor."""
    cal = Calibration.__new__(Calibration)
    # Monitor 1920x1080 em (0,0)
    monitor = {"left": 0, "top": 0, "right": 1920, "bottom": 1080}
    # 5 pontos de calibração: gaze normalizado → pixel absoluto
    src = np.float32([[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]])
    dst = np.float32([
        [960,  540],
        [0,    0  ],
        [1920, 0  ],
        [0,    1080],
        [1920, 1080],
    ])
    import cv2
    H, _ = cv2.findHomography(src, dst)
    cal._homographies = {0: H}

    result = cal.apply(0, 0.5, 0.5)
    assert result is not None
    x, y = result
    assert abs(x - 960) < 5
    assert abs(y - 540) < 5


def test_save_and_load(tmp_path):
    import cv2
    cal = Calibration.__new__(Calibration)
    src = np.float32([[0.5,0.5],[0.1,0.1],[0.9,0.1],[0.1,0.9],[0.9,0.9]])
    dst = np.float32([[960,540],[0,0],[1920,0],[0,1080],[1920,1080]])
    H, _ = cv2.findHomography(src, dst)
    cal._homographies = {0: H}

    path = str(tmp_path / "cal.json")
    cal.save(path)
    assert os.path.exists(path)

    cal2 = Calibration.__new__(Calibration)
    cal2.load(path)
    result = cal2.apply(0, 0.5, 0.5)
    assert result is not None
    x, y = result
    assert abs(x - 960) < 5


def test_apply_returns_none_without_calibration():
    cal = Calibration.__new__(Calibration)
    cal._homographies = {}
    assert cal.apply(0, 0.5, 0.5) is None


def test_poly_features_shape():
    from calibration import _poly_features
    import numpy as np
    f = _poly_features(0.5, 0.3)
    assert f.shape == (6,)
    assert f[0] == 1.0
    assert abs(f[1] - 0.5) < 1e-9   # gx
    assert abs(f[2] - 0.3) < 1e-9   # gy
    assert abs(f[3] - 0.25) < 1e-9  # gx²
    assert abs(f[4] - 0.15) < 1e-9  # gx·gy
    assert abs(f[5] - 0.09) < 1e-9  # gy²
