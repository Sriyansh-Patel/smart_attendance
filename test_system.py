import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
import numpy as np
import cv2
import io

def get_test_face_bytes() -> bytes:
    with open("test_face.jpg", "rb") as f:
        return f.read()

def run_tests():
    print("========================================")
    print("Testing Smart Attendance System Endpoints")
    print("========================================")
    init_db()
    client = TestClient(app)

    # 0. Test Health & DB Status
    res = client.get("/api/health")
    assert res.status_code == 200, f"Health check failed: {res.status_code}"
    health = res.json()
    assert "database" in health
    print(f"[PASS] 0. Health check OK: DB={health['database']['backend']} ({health['database']['database_name']})")

    # 1. Test Device OTP (Unauthorized)
    res = client.post("/api/device/otp", json={"device_key": "wrong_key"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    print("[PASS] 1. Device OTP correctly rejects unauthorized keys (401)")

    # 2. Test Device OTP (Authorized ESP32 Only) & Confirm Frontend Status Does Not Leak OTP
    res = client.post("/api/device/otp", json={"device_key": "esp32_classroom_key_98765"})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    otp_data = res.json()
    assert "otp" in otp_data and len(otp_data["otp"]) == 6
    assert "expires_in" in otp_data
    current_otp = otp_data["otp"]
    session_id = otp_data["session_id"]

    res_status = client.get("/api/device/status")
    assert res_status.status_code == 200
    assert "current_otp" not in res_status.json(), "Frontend status must NOT expose OTP!"
    print(f"[PASS] 2. ESP32-only OTP active: OTP = {current_otp}, Expires In = {otp_data['expires_in']}s (hidden from frontend)")

    # 3. Test Student Enrollment
    test_face = get_test_face_bytes()
    files = [("photos", ("face1.jpg", test_face, "image/jpeg"))]
    data = {
        "student_id": "STU-9901",
        "name": "Jordan Smith",
        "department": "IoT Robotics"
    }
    res = client.post("/api/enroll", data=data, files=files)
    assert res.status_code == 200, f"Enroll failed: {res.status_code}: {res.text}"
    enroll_data = res.json()
    assert enroll_data["status"] == "success"
    print(f"[PASS] 3. Student Enrollment successful: {enroll_data['message']}")

    # 4. Test Attendance with Invalid OTP
    files = {"face_image": ("live.jpg", test_face, "image/jpeg")}
    att_data = {
        "student_id": "STU-9901",
        "session_id": session_id,
        "otp": "000000" # wrong OTP
    }
    res = client.post("/api/attendance", data=att_data, files=files)
    assert res.status_code == 403, f"Expected 403 for wrong OTP, got {res.status_code}"
    print("[PASS] 4. Attendance verification rejects invalid OTP (403)")

    # 5. Test Attendance with Valid OTP and Matching Face
    files = {"face_image": ("live.jpg", test_face, "image/jpeg")}
    att_data = {
        "student_id": "STU-9901",
        "session_id": session_id,
        "otp": current_otp
    }
    res = client.post("/api/attendance", data=att_data, files=files)
    assert res.status_code == 200, f"Attendance failed: {res.status_code}: {res.text}"
    att_res = res.json()
    assert att_res["status"] == "success"
    print(f"[PASS] 5. Attendance verified successfully! Match score: {att_res['similarity_score'] * 100:.1f}%")

    # 6. Test GET Attendance Session (JSON format)
    res = client.get(f"/api/attendance/{session_id}")
    assert res.status_code == 200
    records = res.json()["records"]
    assert len(records) >= 1
    assert records[0]["student_id"] == "STU-9901"
    print(f"[PASS] 6. GET /api/attendance/{session_id} returned {len(records)} records")

    # 7. Test GET Attendance Session (CSV export)
    res = client.get(f"/api/attendance/{session_id}?format=csv")
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")
    assert "STU-9901" in res.text
    print("[PASS] 7. CSV Export generated successfully with proper headers and data content:")
    for line in res.text.strip().split("\n"):
        print("   " + line)

    print("\n========================================")
    print("ALL API ENDPOINTS PASSED VERIFICATION!")
    print("========================================")

if __name__ == "__main__":
    run_tests()
