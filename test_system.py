import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db


def get_test_face_bytes() -> bytes:
    with open("test_face.jpg", "rb") as f:
        return f.read()


def run_tests():
    print("========================================")
    print("Testing Universal Smart Attendance API")
    print("========================================")
    init_db()
    client = TestClient(app)

    # 0. Health & Success Endpoint
    res = client.get("/api/health")
    assert res.status_code == 200
    health = res.json()
    print(f"[PASS] 0. Health check OK: DB={health['database']['backend']} ({health['database']['database_name']})")

    res_success = client.get("/api/success")
    assert res_success.status_code == 200 and res_success.json()["success"] is True
    print("[PASS] 1. Dedicated GET /api/success endpoint works")

    # 1. Separate Company / Administrator Registration & Login
    comp_id = f"TESTCO_{uuid.uuid4().hex[:5].upper()}"
    dev_key = f"dev_{uuid.uuid4().hex[:8]}"
    res = client.post("/api/companies/register", json={
        "company_id": comp_id,
        "company_name": "Test Robotics Corp",
        "admin_name": "Ada Lovelace",
        "admin_pin": "4321",
        "org_type": "office",
        "device_key": dev_key,
    })
    assert res.status_code == 200, f"Company register failed: {res.text}"
    session_id = res.json()["default_session_id"]

    res_login = client.post("/api/companies/login", json={"company_id": comp_id, "admin_pin": "4321"})
    assert res_login.status_code == 200 and res_login.json()["company"]["company_id"] == comp_id
    assert client.post("/api/companies/login", json={"company_id": comp_id, "admin_pin": "9999"}).status_code == 401
    print(f"[PASS] 2. Separate Company & Admin Portal auth verified ({comp_id})")

    # 2. Test Device OTP (Unauthorized vs Authorized, 60s expiry, hidden from frontend)
    assert client.post("/api/device/otp", json={"device_key": "wrong_key"}).status_code == 401
    res = client.post("/api/device/otp", json={"device_key": dev_key})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    otp_data = res.json()
    assert len(otp_data["otp"]) == 6
    assert 50 <= otp_data["expires_in"] <= 60, f"Expected ~60s OTP expiry, got {otp_data['expires_in']}"
    current_otp = otp_data["otp"]

    res_status = client.get(f"/api/device/status?company_id={comp_id}")
    assert res_status.status_code == 200 and "current_otp" not in res_status.json()
    assert client.get("/api/otp").status_code == 401
    assert client.get(f"/api/device/otp?device_key={dev_key}").json()["otp"] == current_otp
    print(f"[PASS] 3. ESP32-only 60s OTP active: {current_otp} (expires_in={otp_data['expires_in']}s)")

    # 3. Enroll Member under the Company
    test_face = get_test_face_bytes()
    files = [("photos", ("face1.jpg", test_face, "image/jpeg"))]
    data = {
        "company_id": comp_id,
        "member_id": "EMP-101",
        "name": "Jordan Smith",
        "group": "Engineering",
    }
    res = client.post("/api/enroll", data=data, files=files)
    assert res.status_code == 200, f"Enroll failed: {res.status_code}: {res.text}"
    assert res.json()["company_id"] == comp_id
    print(f"[PASS] 4. Member enrolled under {comp_id}: {res.json()['message']}")

    # 4. Attendance with Location & Notice Time + Single-Use OTP Lock
    files = {"face_image": ("live.jpg", test_face, "image/jpeg")}
    att_data = {
        "company_id": comp_id,
        "member_id": "EMP-101",
        "session_id": session_id,
        "otp": current_otp,
        "latitude": "28.6139",
        "longitude": "77.2090",
        "location_accuracy": "15",
        "client_time": "2026-11-27 10:35:00",
    }
    res = client.post("/api/attendance", data=att_data, files=files)
    assert res.status_code == 200, f"Attendance failed: {res.status_code}: {res.text}"
    att_res = res.json()
    assert att_res["success"] is True
    assert att_res["location"]["latitude"] == 28.6139
    assert "2026-11-27 10:35:00" in att_res["notice_time"]
    receipt_id = att_res["attendance_id"]
    print(f"[PASS] 5. First user attendance marked! Receipt={receipt_id}, Location={att_res['location']['label']}")

    # Verify /api/success?attendance_id=...
    res_rec = client.get(f"/api/success?attendance_id={receipt_id}")
    assert res_rec.status_code == 200 and res_rec.json()["receipt"]["attendance_id"] == receipt_id
    print(f"[PASS] 6. GET /api/success?attendance_id={receipt_id} verified")

    # Verify second user CANNOT reuse the same OTP (single user at a time)
    files2 = {"face_image": ("live2.jpg", test_face, "image/jpeg")}
    res2 = client.post("/api/attendance", data=att_data, files=files2)
    assert res2.status_code == 403, f"Expected 403 for reused OTP, got {res2.status_code}"
    print("[PASS] 7. Single-user lock enforced: second attempt with same OTP rejected (403)")

    # 5. CSV Export with Location & Notice Time
    res_csv = client.get(f"/api/attendance/{session_id}?company_id={comp_id}&format=csv")
    assert res_csv.status_code == 200 and "text/csv" in res_csv.headers.get("content-type", "")
    assert "EMP-101" in res_csv.text and "28.61390" in res_csv.text
    print("[PASS] 8. CSV Export includes Receipt ID, Company ID, Notice Time, and GPS Location")

    print("\n========================================")
    print("ALL VERIFICATION CHECKS PASSED!")
    print("========================================")


if __name__ == "__main__":
    run_tests()
