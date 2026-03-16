# Precision Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve gaze tracking precision by implementing three techniques borrowed from JEOresearch/EyeTracker: (1) multi-threshold iris detection as a MediaPipe fallback, (2) orthogonal ray intersection to estimate eye globe center, and (3) angle-based contour point filtering.

**Architecture:** All changes are confined to `gaze_tracker.py` (new helper functions) and `config.py` (new tunable parameters). A new optional module `iris_detector.py` handles the OpenCV-based pupil/iris detection pipeline used as fallback and cross-validation. No changes to calibration, focus, or UI layers.

**Tech Stack:** Python 3.10+, OpenCV (`cv2`), NumPy, MediaPipe FaceLandmarker (existing), pytest (existing test suite runs on Windows via `python -m pytest tests/ -v`).

**Reference repo:** https://github.com/JEOresearch/EyeTracker/tree/main/3DTracker
Key source files: `Orlosky3DEyeTracker.py` (pupil detection), `gl_sphere.py` (ray-sphere math).

---

## Chunk 1: Multi-Threshold Iris Detector Module

### Task 1: `iris_detector.py` — darkest-area scan + multi-threshold ellipse fitting

**Files:**
- Create: `glaze-app/iris_detector.py`
- Create: `glaze-app/tests/test_iris_detector.py`

This module takes a grayscale eye-region crop (NumPy array) and returns the best-fit ellipse center `(cx, cy)` in crop coordinates, or `None` if no valid pupil is found. It is **not** called in the main pipeline yet — that happens in Task 3.

---

- [ ] **Step 1: Write the failing tests**

Create `glaze-app/tests/test_iris_detector.py`:

```python
# glaze-app/tests/test_iris_detector.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pytest
from iris_detector import (
    get_darkest_area,
    apply_binary_threshold,
    filter_contours_by_area_ratio,
    optimize_contours_by_angle,
    detect_iris_center,
)


def _make_pupil_image(cx=64, cy=64, radius=15, size=128):
    """Synthetic grayscale image: dark ellipse on gray background."""
    import cv2
    img = np.full((size, size), 180, dtype=np.uint8)
    cv2.ellipse(img, (cx, cy), (radius, radius - 3), 0, 0, 360, 20, -1)
    return img


def test_get_darkest_area_finds_pupil():
    img = _make_pupil_image(cx=64, cy=64)
    cx_found, cy_found = get_darkest_area(img)
    assert abs(cx_found - 64) < 20
    assert abs(cy_found - 64) < 20


def test_apply_binary_threshold_returns_three_levels():
    img = _make_pupil_image()
    cx, cy = get_darkest_area(img)
    darkest = int(img[cy, cx])
    strict, medium, relaxed = apply_binary_threshold(img, darkest)
    # All three are binary (0/255) images
    for thresh_img in (strict, medium, relaxed):
        assert thresh_img.dtype == np.uint8
        assert set(np.unique(thresh_img)).issubset({0, 255})


def test_filter_contours_rejects_noise():
    import cv2
    # Tiny contour (< 1000 px area) should be rejected
    img = np.zeros((128, 128), dtype=np.uint8)
    cv2.circle(img, (64, 64), 5, 255, -1)  # area ~ 78 px
    contours, _ = cv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    result = filter_contours_by_area_ratio(contours, area_thresh=1000, ratio_thresh=3.0)
    assert result == []


def test_detect_iris_center_on_synthetic_pupil():
    img = _make_pupil_image(cx=64, cy=64, radius=18, size=128)
    result = detect_iris_center(img)
    assert result is not None
    cx, cy = result
    assert abs(cx - 64) < 15
    assert abs(cy - 64) < 15


def test_detect_iris_center_returns_none_on_blank():
    img = np.full((128, 128), 200, dtype=np.uint8)  # uniform — no pupil
    result = detect_iris_center(img)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/test_iris_detector.py -v
```

Expected: `ImportError: No module named 'iris_detector'`

- [ ] **Step 3: Implement `iris_detector.py`**

Create `glaze-app/iris_detector.py`:

```python
# glaze-app/iris_detector.py
"""
OpenCV-based iris/pupil center detector.

Implements multi-threshold ellipse fitting inspired by JEOresearch/EyeTracker
(Orlosky3DEyeTracker.py). Used as a cross-validation layer or fallback when
MediaPipe iris landmarks have low confidence.

Public API:
    detect_iris_center(gray_crop) -> (cx, cy) | None
"""
import cv2
import numpy as np
from typing import Optional, Tuple, List


# ── tunable constants ──────────────────────────────────────────────────────────
_SCAN_SKIP    = 10    # coarse scan step (pixels)
_FINE_SKIP    = 5     # fine scan step (pixels)
_SCAN_WIN     = 20    # search window half-size for darkest-area scan
_MASK_SIZE    = 250   # square mask radius around pupil candidate (pixels)
_AREA_THRESH  = 1000  # minimum contour area (pixels²)
_RATIO_THRESH = 3.0   # maximum minor/major axis ratio for ellipse
_DILATE_K     = 5     # dilation kernel size
_THRESH_STRICT  = 5
_THRESH_MEDIUM  = 15
_THRESH_RELAXED = 25


def get_darkest_area(gray: np.ndarray) -> Tuple[int, int]:
    """
    Coarse + fine scan to find the darkest region (pupil candidate).

    Returns (cx, cy) in image coordinates.
    Inspired by Orlosky3DEyeTracker.py::get_darkest_area().
    """
    h, w = gray.shape
    best_val = float('inf')
    best_x, best_y = w // 2, h // 2

    # Coarse scan
    for y in range(0, h - _SCAN_WIN, _SCAN_SKIP):
        for x in range(0, w - _SCAN_WIN, _SCAN_SKIP):
            region = gray[y:y + _SCAN_WIN, x:x + _SCAN_WIN]
            val = float(region.sum())
            if val < best_val:
                best_val = val
                best_x, best_y = x + _SCAN_WIN // 2, y + _SCAN_WIN // 2

    # Fine scan around coarse result
    fine_best_val = float('inf')
    x0 = max(0, best_x - _SCAN_WIN * 3)
    y0 = max(0, best_y - _SCAN_WIN * 3)
    x1 = min(w - _SCAN_WIN, best_x + _SCAN_WIN * 3)
    y1 = min(h - _SCAN_WIN, best_y + _SCAN_WIN * 3)
    for y in range(y0, y1, _FINE_SKIP):
        for x in range(x0, x1, _FINE_SKIP):
            region = gray[y:y + _SCAN_WIN, x:x + _SCAN_WIN]
            val = float(region.sum())
            if val < fine_best_val:
                fine_best_val = val
                best_x, best_y = x + _SCAN_WIN // 2, y + _SCAN_WIN // 2

    return best_x, best_y


def apply_binary_threshold(
    gray: np.ndarray,
    darkest_value: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Apply three binary thresholds (strict / medium / relaxed).

    Returns (strict_img, medium_img, relaxed_img) — all uint8 binary.
    Inspired by Orlosky3DEyeTracker.py::apply_binary_threshold().
    """
    def _thresh(added: int) -> np.ndarray:
        limit = min(255, darkest_value + added)
        _, binary = cv2.threshold(gray, limit, 255, cv2.THRESH_BINARY_INV)
        return binary

    return _thresh(_THRESH_STRICT), _thresh(_THRESH_MEDIUM), _thresh(_THRESH_RELAXED)


def filter_contours_by_area_ratio(
    contours: List,
    area_thresh: float = _AREA_THRESH,
    ratio_thresh: float = _RATIO_THRESH,
) -> List:
    """
    Keep only contours with area >= area_thresh and aspect ratio <= ratio_thresh.
    Inspired by Orlosky3DEyeTracker.py::filter_contours_by_area_and_return_largest().
    """
    valid = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < area_thresh:
            continue
        if len(c) < 5:
            continue
        ellipse = cv2.fitEllipse(c)
        (_, _), (ma, mi), _ = ellipse
        if ma < 1e-6:
            continue
        ratio = max(ma, mi) / max(min(ma, mi), 1e-6)
        if ratio <= ratio_thresh:
            valid.append(c)
    return valid


def optimize_contours_by_angle(contour: np.ndarray, angle_thresh_deg: float = 60.0) -> np.ndarray:
    """
    Filter contour points that deviate more than angle_thresh_deg from pointing
    toward the centroid. Removes spurious edge points before ellipse fitting.
    Inspired by Orlosky3DEyeTracker.py::optimize_contours_by_angle().
    """
    pts = contour.reshape(-1, 2).astype(float)
    if len(pts) < 5:
        return contour

    centroid = pts.mean(axis=0)
    kept = []
    n = len(pts)
    thresh_rad = np.radians(angle_thresh_deg)

    for i in range(n):
        p = pts[i]
        to_center = centroid - p
        norm = np.linalg.norm(to_center)
        if norm < 1e-9:
            kept.append(p)
            continue
        to_center /= norm

        # Direction to next point
        nxt = pts[(i + 1) % n]
        to_next = nxt - p
        nn = np.linalg.norm(to_next)
        if nn < 1e-9:
            continue
        to_next /= nn

        angle = np.arccos(np.clip(np.dot(to_center, to_next), -1.0, 1.0))
        if angle < thresh_rad:
            kept.append(p)

    if len(kept) < 5:
        return contour  # fallback: return original

    return np.array(kept, dtype=np.int32).reshape(-1, 1, 2)


def _ellipse_goodness(binary: np.ndarray, contour: np.ndarray) -> float:
    """
    Score an ellipse candidate: pixel coverage ratio × aspect score.
    Higher is better. Returns 0.0 on failure.
    Inspired by Orlosky3DEyeTracker.py::check_ellipse_goodness().
    """
    if len(contour) < 5:
        return 0.0
    try:
        ellipse = cv2.fitEllipse(contour)
    except cv2.error:
        return 0.0

    mask = np.zeros_like(binary)
    cv2.ellipse(mask, ellipse, 255, 10)
    covered = int(cv2.countNonZero(cv2.bitwise_and(binary, mask)))
    total_mask = int(cv2.countNonZero(mask))
    if total_mask == 0:
        return 0.0

    coverage = covered / total_mask
    (_, _), (ma, mi), _ = ellipse
    aspect = min(ma, mi) / max(max(ma, mi), 1e-6)  # 1.0 = perfect circle
    return coverage * aspect


def detect_iris_center(gray: np.ndarray) -> Optional[Tuple[float, float]]:
    """
    Detect iris/pupil center in a grayscale crop.

    Returns (cx, cy) in crop pixel coordinates, or None if no valid pupil found.

    Algorithm:
    1. Find darkest area (pupil candidate)
    2. Apply 3 binary thresholds
    3. Dilate each; find contours
    4. Filter by area + aspect ratio
    5. Optimize contour points by angle
    6. Fit ellipses; score each; pick best
    """
    cx, cy = get_darkest_area(gray)
    darkest_val = int(gray[min(cy, gray.shape[0]-1), min(cx, gray.shape[1]-1)])

    strict, medium, relaxed = apply_binary_threshold(gray, darkest_val)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_DILATE_K, _DILATE_K))

    best_score = 0.0
    best_center: Optional[Tuple[float, float]] = None

    for binary in (strict, medium, relaxed):
        dilated = cv2.dilate(binary, kernel)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        valid = filter_contours_by_area_ratio(contours)
        if not valid:
            continue

        # Take largest valid contour
        largest = max(valid, key=cv2.contourArea)
        optimized = optimize_contours_by_angle(largest)

        if len(optimized.reshape(-1, 2)) < 5:
            continue

        score = _ellipse_goodness(binary, optimized)
        if score > best_score:
            best_score = score
            try:
                ellipse = cv2.fitEllipse(optimized)
                best_center = (float(ellipse[0][0]), float(ellipse[0][1]))
            except cv2.error:
                pass

    return best_center if best_score > 0.05 else None
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/test_iris_detector.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add glaze-app/iris_detector.py glaze-app/tests/test_iris_detector.py
git commit -m "feat(iris): add multi-threshold iris detector with angle-filtered contours"
```

---

## Chunk 2: Orthogonal Ray Intersection — Eye Globe Center Estimator

### Task 2: `eye_globe.py` — orthogonal ray intersection to estimate 2D eye center

**Files:**
- Create: `glaze-app/eye_globe.py`
- Create: `glaze-app/tests/test_eye_globe.py`

This module accumulates ellipse orientations across frames and computes pairwise intersections of rays orthogonal to ellipse minor axes. The averaged intersection converges toward the eye globe center in image space. Returns `None` until enough intersections are accumulated.

---

- [ ] **Step 1: Write the failing tests**

Create `glaze-app/tests/test_eye_globe.py`:

```python
# glaze-app/tests/test_eye_globe.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pytest
from eye_globe import EyeGlobeEstimator, find_line_intersection


def test_find_line_intersection_perpendicular():
    # Two perpendicular lines crossing at (50, 50)
    # Line 1: passes (50, 0), direction (0, 1) — vertical
    # Line 2: passes (0, 50), direction (1, 0) — horizontal
    p1, d1 = np.array([50.0, 0.0]), np.array([0.0, 1.0])
    p2, d2 = np.array([0.0, 50.0]), np.array([1.0, 0.0])
    result = find_line_intersection(p1, d1, p2, d2)
    assert result is not None
    assert abs(result[0] - 50.0) < 1e-6
    assert abs(result[1] - 50.0) < 1e-6


def test_find_line_intersection_parallel_returns_none():
    p1, d1 = np.array([0.0, 0.0]), np.array([1.0, 0.0])
    p2, d2 = np.array([0.0, 5.0]), np.array([1.0, 0.0])
    result = find_line_intersection(p1, d1, p2, d2)
    assert result is None


def test_estimator_returns_none_when_insufficient_data():
    est = EyeGlobeEstimator(max_intersections=100)
    # Feed only 1 observation — not enough pairs
    est.update(center=(60.0, 60.0), angle_deg=30.0)
    assert est.get_center() is None


def test_estimator_converges_near_true_center():
    """
    Simulate ellipses of an eye globe at image center (64, 64).
    Each frame, the ellipse minor axis angle changes slightly.
    Rays perpendicular to each minor axis should intersect near (64, 64).
    """
    est = EyeGlobeEstimator(max_intersections=500, angle_threshold_deg=2.0)
    true_cx, true_cy = 64.0, 64.0
    radius = 20.0

    # Simulate points on circle around globe center as ellipse centers
    # with minor axis pointing roughly toward center
    for i in range(60):
        theta = np.radians(i * 6)  # 6° steps
        # ellipse center slightly offset from true center
        ecx = true_cx + radius * np.cos(theta)
        ecy = true_cy + radius * np.sin(theta)
        # Minor axis perpendicular to radius vector → points toward center
        minor_angle_deg = np.degrees(theta) + 90.0
        est.update(center=(ecx, ecy), angle_deg=minor_angle_deg)

    result = est.get_center()
    assert result is not None
    cx, cy = result
    assert abs(cx - true_cx) < 10.0
    assert abs(cy - true_cy) < 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/test_eye_globe.py -v
```

Expected: `ImportError: No module named 'eye_globe'`

- [ ] **Step 3: Implement `eye_globe.py`**

Create `glaze-app/eye_globe.py`:

```python
# glaze-app/eye_globe.py
"""
Eye globe center estimator via orthogonal ray intersection.

Across frames, the ellipse fitted to the iris/pupil has a minor axis
whose perpendicular ray, traced from the ellipse center, converges toward
the eye globe center. Accumulating pairwise intersections of these rays
and averaging gives a stable 2D estimate of the eye center in image space.

Inspired by JEOresearch/EyeTracker::Orlosky3DEyeTracker.py
  - compute_average_intersection()
  - find_line_intersection()

Public API:
    EyeGlobeEstimator.update(center, angle_deg) -> None
    EyeGlobeEstimator.get_center() -> (cx, cy) | None
    find_line_intersection(p1, d1, p2, d2) -> (x, y) | None
"""
import numpy as np
from typing import Optional, Tuple, List
import math


def find_line_intersection(
    p1: np.ndarray,
    d1: np.ndarray,
    p2: np.ndarray,
    d2: np.ndarray,
) -> Optional[Tuple[float, float]]:
    """
    Find the 2D intersection of two infinite lines.

    Line 1: p1 + t*d1
    Line 2: p2 + s*d2

    Returns (x, y) intersection point, or None if lines are parallel.
    Solves:  [d1 | -d2] * [t, s]^T = p2 - p1
    """
    A = np.array([[d1[0], -d2[0]],
                  [d1[1], -d2[1]]], dtype=float)
    det = A[0, 0] * A[1, 1] - A[0, 1] * A[1, 0]
    if abs(det) < 1e-9:
        return None  # parallel lines

    b = p2 - p1
    t = (b[0] * A[1, 1] - b[1] * A[0, 1]) / det
    ix = p1[0] + t * d1[0]
    iy = p1[1] + t * d1[1]
    return (ix, iy)


class EyeGlobeEstimator:
    """
    Accumulates iris ellipse observations (center + minor axis angle) across
    frames and estimates the eye globe center via orthogonal ray intersection.

    Usage:
        est = EyeGlobeEstimator()
        # Each frame where iris ellipse is detected:
        est.update(center=(cx_px, cy_px), angle_deg=minor_axis_angle_deg)
        globe_center = est.get_center()  # None until enough data
    """

    def __init__(
        self,
        max_intersections: int = 500,
        angle_threshold_deg: float = 2.0,
        min_intersections: int = 30,
    ):
        self._max = max_intersections
        self._angle_thresh = angle_threshold_deg
        self._min = min_intersections
        self._rays: List[Tuple[np.ndarray, np.ndarray]] = []  # (center, direction)
        self._intersections: List[Tuple[float, float]] = []

    def update(self, center: Tuple[float, float], angle_deg: float) -> None:
        """
        Register one iris ellipse observation.

        center:    (cx, cy) — ellipse center in image pixels
        angle_deg: minor axis angle in degrees (OpenCV convention: angle of major axis,
                   so minor axis is perpendicular → angle_deg + 90)
        """
        # Direction perpendicular to minor axis = direction of the ray toward globe center
        rad = math.radians(angle_deg)
        direction = np.array([math.cos(rad), math.sin(rad)], dtype=float)
        pos = np.array(center, dtype=float)

        # Try to intersect with all previous rays
        for prev_pos, prev_dir in self._rays:
            # Check angle between rays to avoid near-parallel intersections
            cos_angle = abs(np.dot(direction, prev_dir))
            angle_between = math.degrees(math.acos(min(1.0, cos_angle)))
            if angle_between < self._angle_thresh:
                continue

            pt = find_line_intersection(pos, direction, prev_pos, prev_dir)
            if pt is None:
                continue

            self._intersections.append(pt)
            if len(self._intersections) > self._max:
                self._intersections.pop(0)

        self._rays.append((pos, direction))
        # Keep only last 100 rays (reference implementation used 100)
        if len(self._rays) > 100:
            self._rays.pop(0)

    def get_center(self) -> Optional[Tuple[float, float]]:
        """
        Return estimated eye globe center, or None if not enough data.
        """
        if len(self._intersections) < self._min:
            return None
        pts = np.array(self._intersections)
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        return (cx, cy)

    def reset(self) -> None:
        """Clear accumulated data (call when face is lost or re-detected)."""
        self._rays.clear()
        self._intersections.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/test_eye_globe.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add glaze-app/eye_globe.py glaze-app/tests/test_eye_globe.py
git commit -m "feat(gaze): add eye globe center estimator via orthogonal ray intersection"
```

---

## Chunk 3: Integration into GazeTracker

### Task 3: Wire `EyeGlobeEstimator` into `gaze_tracker.py`

**Files:**
- Modify: `glaze-app/gaze_tracker.py`
- Modify: `glaze-app/config.py`

The eye globe center will refine the iris offset calculation. Instead of normalizing iris offset by the eye corner width (a rough approximation), we use the distance from the iris center to the estimated eye globe center in image space — which is geometrically more accurate as the globe center is the pivot of the eyeball rotation.

**How it fits into `_compute_gaze()`:**

Current code:
```python
l_off = l_iris - l_eye   # l_eye = midpoint of eye corners
r_off = r_iris - r_eye
```

After this task, `l_eye` and `r_eye` can optionally be replaced by the globe center estimates if available, and the offset normalized by the globe radius rather than the eye-corner span.

---

- [ ] **Step 1: Add config flags**

In `glaze-app/config.py`, add at the end:

```python
# Eye globe estimator (orthogonal ray intersection)
EYE_GLOBE_ENABLED        = True   # feature flag
EYE_GLOBE_MAX_INTERSECT  = 500    # rolling buffer size
EYE_GLOBE_ANGLE_THRESH   = 2.0    # degrees — minimum angle between ray pairs
EYE_GLOBE_MIN_INTERSECT  = 30     # minimum intersections before returning estimate
```

- [ ] **Step 2: Write a failing integration test**

Add to `glaze-app/tests/test_iris_detector.py` (append at end):

```python
def test_detect_iris_center_large_pupil():
    """Larger pupil (radius 25) at off-center position."""
    img = _make_pupil_image(cx=80, cy=50, radius=25, size=128)
    result = detect_iris_center(img)
    assert result is not None
    cx, cy = result
    assert abs(cx - 80) < 20
    assert abs(cy - 50) < 20
```

Run: `python -m pytest tests/test_iris_detector.py::test_detect_iris_center_large_pupil -v`
Expected: PASS (no code change needed — this verifies robustness).

- [ ] **Step 3: Integrate `EyeGlobeEstimator` into `GazeTracker`**

In `glaze-app/gaze_tracker.py`:

a) Add import at top (after existing imports):
```python
from config import (CAMERA_INDEX, CAPTURE_WIDTH, CAPTURE_HEIGHT,
                    GAZE_PITCH_IRIS_WEIGHT, GAZE_Y_RANGE_DEG,
                    EYE_GLOBE_ENABLED, EYE_GLOBE_MAX_INTERSECT,
                    EYE_GLOBE_ANGLE_THRESH, EYE_GLOBE_MIN_INTERSECT)
from eye_globe import EyeGlobeEstimator
```

b) In `GazeTracker.__init__`, after `self._calib_pitch = 0.0`, add:
```python
if EYE_GLOBE_ENABLED:
    self._globe_left  = EyeGlobeEstimator(
        max_intersections=EYE_GLOBE_MAX_INTERSECT,
        angle_threshold_deg=EYE_GLOBE_ANGLE_THRESH,
        min_intersections=EYE_GLOBE_MIN_INTERSECT,
    )
    self._globe_right = EyeGlobeEstimator(
        max_intersections=EYE_GLOBE_MAX_INTERSECT,
        angle_threshold_deg=EYE_GLOBE_ANGLE_THRESH,
        min_intersections=EYE_GLOBE_MIN_INTERSECT,
    )
else:
    self._globe_left = None
    self._globe_right = None
```

c) In `GazeTracker.stop()`, after `self._prev_gaze = None`, add:
```python
if self._globe_left:
    self._globe_left.reset()
if self._globe_right:
    self._globe_right.reset()
```

d) In `GazeTracker._loop()`, after `lm = result.face_landmarks[0]` and before `gaze_dir, _, _ = _compute_gaze(...)`, add:

```python
# Feed iris ellipse info into globe estimators
if EYE_GLOBE_ENABLED:
    _update_globe_estimators(
        lm, w, h,
        self._globe_left,
        self._globe_right,
    )
```

e) Update the call to `_compute_gaze()` to pass globe centers:

Change:
```python
gaze_dir, _, _ = _compute_gaze(lm, face_matrix, w, h)
```

To:
```python
globe_l = self._globe_left.get_center()  if self._globe_left  else None
globe_r = self._globe_right.get_center() if self._globe_right else None
gaze_dir, _, _ = _compute_gaze(lm, face_matrix, w, h, globe_l, globe_r)
```

- [ ] **Step 4: Add `_update_globe_estimators()` helper to `gaze_tracker.py`**

Add before the `GazeTracker` class definition:

```python
def _update_globe_estimators(landmarks, w, h, globe_left, globe_right):
    """
    Feeds iris ellipse observations to both EyeGlobeEstimator instances.

    Uses the iris landmark ring to fit an ellipse, then extracts the minor
    axis angle for orthogonal ray intersection.

    MediaPipe iris indices:
        Left iris:  474-477 (4 points on iris ring)
        Right iris: 469-472
    """
    import cv2 as _cv2

    def _feed(globe, indices):
        if globe is None:
            return
        pts = np.array([[landmarks[i].x * w, landmarks[i].y * h]
                        for i in indices], dtype=np.float32)
        if len(pts) < 5:
            # Pad to 5 points by repeating — cv2.fitEllipse requires ≥5
            pts = np.vstack([pts, pts[:5 - len(pts)]])
        try:
            ellipse = _cv2.fitEllipse(pts.reshape(-1, 1, 2).astype(np.int32))
        except _cv2.error:
            return
        center = (ellipse[0][0], ellipse[0][1])
        # OpenCV angle is major axis angle; minor axis = major + 90
        minor_angle_deg = ellipse[2] + 90.0
        globe.update(center=center, angle_deg=minor_angle_deg)

    _feed(globe_left,  list(range(474, 478)))
    _feed(globe_right, list(range(469, 473)))
```

- [ ] **Step 5: Update `_compute_gaze()` signature to accept globe centers**

Change the function signature from:
```python
def _compute_gaze(landmarks, face_matrix, w, h):
```
To:
```python
def _compute_gaze(landmarks, face_matrix, w, h,
                  globe_center_left=None, globe_center_right=None):
```

Then replace the iris offset computation block (current lines 106-121) with:

```python
    l_iris = _get_iris_center(landmarks, LEFT_IRIS,  w, h)
    r_iris = _get_iris_center(landmarks, RIGHT_IRIS, w, h)

    # Use globe center estimate if available, else fall back to eye corners
    if globe_center_left is not None:
        l_eye = np.array(globe_center_left, dtype=float)
    else:
        l_eye = _get_eye_center(landmarks, LEFT_EYE_CORNERS, w, h)

    if globe_center_right is not None:
        r_eye = np.array(globe_center_right, dtype=float)
    else:
        r_eye = _get_eye_center(landmarks, RIGHT_EYE_CORNERS, w, h)

    l_off = l_iris - l_eye
    r_off = r_iris - r_eye
    offset = (l_off + r_off) * 0.5

    # Normalize by eye corner span (approximation of globe radius)
    eye_w = np.linalg.norm(
        np.array([landmarks[133].x - landmarks[33].x,
                  landmarks[133].y - landmarks[33].y]) * w
    )
    if eye_w > 1e-6:
        offset /= eye_w
```

- [ ] **Step 6: Run the full test suite**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/ -v
```

Expected: all existing tests + new tests PASS. No regressions.

- [ ] **Step 7: Commit**

```bash
git add glaze-app/gaze_tracker.py glaze-app/config.py
git commit -m "feat(gaze): integrate EyeGlobeEstimator into GazeTracker for refined iris offset"
```

---

## Chunk 4: Push and Smoke Test

### Task 4: Push to remote + manual smoke test

**Files:** no code changes — integration verification only.

---

- [ ] **Step 1: Run full test suite one final time**

```
cd E:\projetos\glaze\glaze-app
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Push to remote**

```bash
git push origin master
```

- [ ] **Step 3: Manual smoke test on Windows**

```
cd E:\projetos\glaze\glaze-app
python main.py
```

Verify:
- App starts without `ImportError`
- `[GazeTracker]` log appears and tracking begins
- After ~5 seconds, globe estimator accumulates data silently (no visible change expected until `EYE_GLOBE_MIN_INTERSECT=30` intersections)
- Gaze cursor moves responsively across monitors

- [ ] **Step 4: If tracking feels off, tune in `config.py`**

If the globe estimator introduces jitter (unlikely before min_intersections is reached):
```python
EYE_GLOBE_MIN_INTERSECT = 60   # raise to require more data before using estimate
```

If you want to disable without code changes:
```python
EYE_GLOBE_ENABLED = False
```

---

## Summary: Files Changed

| File | Status | Purpose |
|---|---|---|
| `glaze-app/iris_detector.py` | NEW | Multi-threshold pupil detection + angle-filtered contour fitting |
| `glaze-app/eye_globe.py` | NEW | Orthogonal ray intersection eye globe center estimator |
| `glaze-app/gaze_tracker.py` | MODIFIED | Integrates EyeGlobeEstimator; refines iris offset pivot point |
| `glaze-app/config.py` | MODIFIED | 4 new EYE_GLOBE_* config flags |
| `glaze-app/tests/test_iris_detector.py` | NEW | Tests for multi-threshold detector |
| `glaze-app/tests/test_eye_globe.py` | NEW | Tests for ray intersection estimator |

## Reference Code

- `Orlosky3DEyeTracker.py`: `get_darkest_area()`, `apply_binary_threshold()`, `optimize_contours_by_angle()`, `check_ellipse_goodness()`, `compute_average_intersection()`, `find_line_intersection()`
- `gl_sphere.py`: `update_sphere_rotation()` — ray-sphere quadratic (not implemented in this plan; reserved for future precision iteration)
