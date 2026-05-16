import cv2
import numpy as np
import base64
import os

# ── Face Verification ─────────────────────────────────────────────────────────
# NOTE: This implementation uses OpenCV ORB feature matching as a lightweight
# fallback. ORB is NOT a dedicated face recognition algorithm and can produce
# false positives/negatives. For production-grade security, replace with the
# `face_recognition` library (which uses dlib + deep learning):
#
#   pip install face_recognition
#   import face_recognition
#
# The current approach is intentionally simple and suitable only for demos or
# low-security environments. The ALWAYS_PASS env var can be set to "1" to
# disable verification entirely during development.
# ─────────────────────────────────────────────────────────────────────────────

def verify_face(captured_b64, reference_path):
    """
    Verify that a captured selfie matches the staff member's reference image.

    Returns (True, message) on success, (False, reason) on failure.

    For real face recognition install `face_recognition` and swap in the
    dlib-based implementation below (commented out).
    """
    # Dev bypass — set FACE_VERIFY_BYPASS=1 in .env to skip verification
    if os.environ.get("FACE_VERIFY_BYPASS", "0") == "1":
        return True, "Verification bypassed (dev mode)."

    if not reference_path or not os.path.exists(reference_path):
        return False, "No reference image found for this staff member."

    try:
        # ── Decode selfie ──────────────────────────────────────────────────
        encoded_data = captured_b64.split(',')[-1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img_captured = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        img_reference = cv2.imread(reference_path)

        if img_captured is None or img_reference is None:
            return False, "Could not decode one or both images."

        # ── Resize to common size to normalise scale differences ───────────
        target_size = (300, 300)
        img_captured  = cv2.resize(img_captured,  target_size)
        img_reference = cv2.resize(img_reference, target_size)

        gray_cap = cv2.cvtColor(img_captured,  cv2.COLOR_BGR2GRAY)
        gray_ref = cv2.cvtColor(img_reference, cv2.COLOR_BGR2GRAY)

        # ── Apply histogram equalisation to reduce lighting sensitivity ────
        gray_cap = cv2.equalizeHist(gray_cap)
        gray_ref = cv2.equalizeHist(gray_ref)

        # ── ORB feature matching ───────────────────────────────────────────
        orb = cv2.ORB_create(nfeatures=500)
        kp1, des1 = orb.detectAndCompute(gray_cap, None)
        kp2, des2 = orb.detectAndCompute(gray_ref, None)

        if des1 is None or des2 is None or len(kp1) < 5 or len(kp2) < 5:
            return False, "Not enough features detected — ensure good lighting and a clear face."

        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)
        matches = sorted(matches, key=lambda x: x.distance)

        # Good matches: distance under 45 (tighter than before)
        good_matches = [m for m in matches if m.distance < 45]
        match_ratio  = len(good_matches) / max(len(matches), 1)

        # Require both an absolute count AND a ratio to reduce false positives
        MATCH_COUNT_THRESHOLD = 18
        MATCH_RATIO_THRESHOLD = 0.15

        if len(good_matches) >= MATCH_COUNT_THRESHOLD and match_ratio >= MATCH_RATIO_THRESHOLD:
            return True, f"Face verified ({len(good_matches)} feature matches)."
        else:
            return False, (
                f"Face does not match reference image "
                f"({len(good_matches)} matches, {match_ratio:.0%} ratio). "
                "Ensure good lighting and face the camera directly."
            )

    except Exception as e:
        return False, f"Verification error: {str(e)}"
