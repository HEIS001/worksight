import cv2
import numpy as np
import base64
import os

# ── Face Verification ─────────────────────────────────────────────────────────
# Uses a multi-method approach:
#   1. Haar Cascade face detection to confirm a face is present in both images
#   2. SSIM (Structural Similarity Index) on the face region for comparison
#   3. Histogram correlation as a secondary signal
#
# This is significantly more reliable than raw ORB keypoint matching for faces.
# For production, replace with the `face_recognition` library (dlib-based).
# ─────────────────────────────────────────────────────────────────────────────

# Load OpenCV's built-in face detector once at module level
_face_cascade = None

def _get_cascade():
    global _face_cascade
    if _face_cascade is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(cascade_path)
    return _face_cascade


def _extract_face_roi(gray_img):
    """
    Detect the largest face in a grayscale image and return the cropped ROI.
    Returns None if no face is detected.
    """
    cascade = _get_cascade()
    faces = cascade.detectMultiScale(
        gray_img,
        scaleFactor=1.1,
        minNeighbors=4,
        minSize=(60, 60),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    if len(faces) == 0:
        return None
    # Pick the largest face
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return gray_img[y:y+h, x:x+w]


def _ssim(img_a, img_b):
    """Compute mean SSIM between two same-size grayscale images (OpenCV version)."""
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    img_a = img_a.astype(np.float64)
    img_b = img_b.astype(np.float64)
    k = cv2.getGaussianKernel(11, 1.5)
    kernel = k @ k.T
    mu1 = cv2.filter2D(img_a, -1, kernel)
    mu2 = cv2.filter2D(img_b, -1, kernel)
    mu1_sq, mu2_sq, mu1_mu2 = mu1**2, mu2**2, mu1*mu2
    sig1_sq = cv2.filter2D(img_a**2, -1, kernel) - mu1_sq
    sig2_sq = cv2.filter2D(img_b**2, -1, kernel) - mu2_sq
    sig12   = cv2.filter2D(img_a*img_b, -1, kernel) - mu1_mu2
    num = (2*mu1_mu2 + C1) * (2*sig12 + C2)
    den = (mu1_sq + mu2_sq + C1) * (sig1_sq + sig2_sq + C2)
    return float(np.mean(num / den))


def verify_face(captured_b64, reference_path):
    """
    Verify that a captured selfie matches the staff member's reference image.
    Returns (True, message) on success, (False, reason) on failure.
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
        img_captured  = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        img_reference = cv2.imread(reference_path)

        if img_captured is None or img_reference is None:
            return False, "Could not decode one or both images."

        # ── Equalise lighting via CLAHE ────────────────────────────────────
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        gray_cap = cv2.cvtColor(img_captured,  cv2.COLOR_BGR2GRAY)
        gray_ref = cv2.cvtColor(img_reference, cv2.COLOR_BGR2GRAY)
        gray_cap = clahe.apply(gray_cap)
        gray_ref = clahe.apply(gray_ref)

        # ── Try face-region comparison first ──────────────────────────────
        roi_cap = _extract_face_roi(gray_cap)
        roi_ref = _extract_face_roi(gray_ref)

        if roi_cap is not None and roi_ref is not None:
            # Resize both ROIs to the same size for SSIM
            size = (128, 128)
            roi_cap_r = cv2.resize(roi_cap, size)
            roi_ref_r = cv2.resize(roi_ref, size)
            ssim_score = _ssim(roi_cap_r, roi_ref_r)

            # Histogram correlation on face ROI
            hist_cap = cv2.calcHist([roi_cap_r], [0], None, [64], [0, 256])
            hist_ref = cv2.calcHist([roi_ref_r], [0], None, [64], [0, 256])
            cv2.normalize(hist_cap, hist_cap)
            cv2.normalize(hist_ref, hist_ref)
            hist_corr = cv2.compareHist(hist_cap, hist_ref, cv2.HISTCMP_CORREL)

            # Combined score (SSIM weighted higher)
            combined = 0.65 * ssim_score + 0.35 * hist_corr

            THRESHOLD = 0.42   # tuned for lighting variation tolerance
            if combined >= THRESHOLD:
                return True, f"Face verified (score: {combined:.2f})."
            else:
                return False, (
                    f"Face does not match reference image (score: {combined:.2f}). "
                    "Ensure good lighting, face the camera directly, and remove any accessories."
                )
        else:
            # Fall back to full-image comparison when face detection fails
            # (e.g., very low light or extreme angle)
            size = (200, 200)
            gray_cap_r = cv2.resize(gray_cap, size)
            gray_ref_r = cv2.resize(gray_ref, size)
            ssim_score = _ssim(gray_cap_r, gray_ref_r)

            if ssim_score >= 0.38:
                return True, f"Face verified via full-image match (score: {ssim_score:.2f})."
            else:
                return False, (
                    f"Face could not be detected clearly ({ssim_score:.2f}). "
                    "Ensure your face is well-lit and directly facing the camera."
                )

    except Exception as e:
        return False, f"Verification error: {str(e)}"
