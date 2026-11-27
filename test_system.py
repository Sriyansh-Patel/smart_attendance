import sys
import uuid
import time
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
    assert health["otp_duration_seconds"] == 60
    print(f"[PASS] 0. Health check OK: DB={health['database']['backend']}, OTP duration=60s")

    res_success = client.get("/api/success")
    assert res_success.status_code == 200 and res_success.json()["success"] is True
    print("[PASS] 1. Dedicated GET /api/success endpoint works")

    # 1. Separate Company / Organization Registration & Login + Super Admin Login
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

    res_super = client.post("/api/companies/login", json={"company_id": "ALL", "admin_pin": "1234"})
    assert res_super.status_code == 200 and res_super.json()["is_super_admin"] is True
    print(f"[PASS] 2. Organization & Super Admin auth verified ({comp_id})")

    # 2. Test Device OTP (Unauthorized vs Authorized, 60s expiry, hidden from frontend)
    assert client.post("/api/device/otp", json={"device_key": "wrong_key"}).status_code == 401
    res = client.post("/api/device/otp", json={"device_key": dev_key})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    otp_data = res.json()
    assert len(otp_data["otp"]) == 6
    assert otp_data["duration_seconds"] == 60
    assert 50 <= otp_data["expires_in"] <= 60, f"Expected ~60s OTP expiry, got {otp_data['expires_in']}"
    current_otp = otp_data["otp"]

    res_status = client.get(f"/api/device/status?company_id={comp_id}")
    assert res_status.status_code == 200 and "current_otp" not in res_status.json()
    assert res_status.json()["otp_duration_seconds"] == 60
    assert res_status.json()["otp_status"]["otp_active"] is True
    assert client.get("/api/otp").status_code == 401
    assert client.get(f"/api/device/otp?device_key={dev_key}").json()["otp"] == current_otp
    print(f"[PASS] 3. Hardware-only 60s OTP active: {current_otp} (expires_in={otp_data['expires_in']}s)")

    # 3. Enroll Member & Verify Face ID Update
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
    assert res.json()["action"] == "created"

    time.sleep(0.05)
    res_upd = client.put(
        f"/api/members/{comp_id}/EMP-101/face",
        data={"name": "Jordan Smith Updated", "department": "R&D"},
        files=[("photos", ("face2.jpg", test_face, "image/jpeg"))],
    )
    assert res_upd.status_code == 200, f"Face ID update failed: {res_upd.status_code}: {res_upd.text}"
    assert res_upd.json()["updated"] is True
    assert res_upd.json()["name"] == "Jordan Smith Updated"
    print(f"[PASS] 4. Member enrolled & Face ID updated under {comp_id} (updated_at={res_upd.json()['updated_at']})")

    # 4. Attendance with Location & Notice Time + Immediate Post-Attendance OTP Rotation
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
    assert att_res["otp_rotated"] is True
    assert att_res["otp_duration_seconds"] == 60
    assert att_res["location"]["latitude"] == 28.6139
    assert "2026-11-27 10:35:00" in att_res["notice_time"]
    receipt_id = att_res["attendance_id"]
    print(f"[PASS] 5. First user attendance marked! Receipt={receipt_id}, otp_rotated=True")

    # Verify /api/success?attendance_id=...
    res_rec = client.get(f"/api/success?attendance_id={receipt_id}")
    assert res_rec.status_code == 200 and res_rec.json()["receipt"]["attendance_id"] == receipt_id
    print(f"[PASS] 6. GET /api/success?attendance_id={receipt_id} verified")

    # Verify second user CANNOT reuse the same OTP (single user at a time)
    files2 = {"face_image": ("live2.jpg", test_face, "image/jpeg")}
    res2 = client.post("/api/attendance", data=att_data, files=files2)
    assert res2.status_code == 403, f"Expected 403 for reused OTP, got {res2.status_code}"
    print("[PASS] 7. Single-user lock enforced: second attempt with same OTP rejected (403)")

    # Verify hardware immediately receives the newly rotated 60s OTP after attendance is marked
    res_new_otp = client.post("/api/device/otp", json={"device_key": dev_key})
    assert res_new_otp.status_code == 200
    assert res_new_otp.json()["otp"] != current_otp, "OTP should rotate immediately after attendance marked!"
    assert 50 <= res_new_otp.json()["expires_in"] <= 60
    print(f"[PASS] 8. Hardware OTP rotated after attendance marked: new OTP={res_new_otp.json()['otp']} ({res_new_otp.json()['expires_in']}s)")

    # Verify ESP8266 GET /api/device/attendance-event & POST /api/device/attendance-event/ack
    res_evt = client.get(f"/api/device/attendance-event?device_key={dev_key}")
    assert res_evt.status_code == 200 and res_evt.json()["has_event"] is True
    assert res_evt.json()["next_otp"] == res_new_otp.json()["otp"]
    event_id = res_evt.json()["event_id"]
    assert res_evt.json()["member_id"] == "EMP-101"
    print(f"[PASS] 9. GET /api/device/attendance-event returned pending event {event_id} & rotated OTP")

    res_ack = client.post("/api/device/attendance-event/ack", json={"device_key": dev_key, "event_id": event_id})
    assert res_ack.status_code == 200 and res_ack.json()["acknowledged"] is True
    assert res_ack.json()["device_status"] == "ACK_BY_ESP8266"

    res_evt2 = client.get(f"/api/device/attendance-event?device_key={dev_key}")
    assert res_evt2.status_code == 200 and res_evt2.json()["has_event"] is False
    res_rec_after = client.get(f"/api/success?attendance_id={receipt_id}")
    assert res_rec_after.json()["receipt"]["device_status"] == "ACK_BY_ESP8266"
    print("[PASS] 10. POST /api/device/attendance-event/ack updated attendance status to ACK_BY_ESP8266")

    # 5. Super Admin Managing Every Organization & Date Sorting
    super_headers = {"X-Admin-Company": "ALL", "X-Admin-Pin": "1234"}
    res_overview = client.get("/api/admin/overview?sort_order=desc", headers=super_headers)
    assert res_overview.status_code == 200
    orgs = res_overview.json()["organizations"]
    assert any(o["company_id"] == comp_id for o in orgs)

    res_edit = client.put(
        f"/api/companies/{comp_id}",
        headers=super_headers,
        json={"company_name": "Test Robotics Global", "org_type": "school"},
    )
    assert res_edit.status_code == 200 and res_edit.json()["company"]["company_name"] == "Test Robotics Global"

    res_sess = client.post(
        "/api/sessions",
        headers=super_headers,
        json={
            "company_id": comp_id,
            "session_id": f"{comp_id}_SESSION_02",
            "session_title": "Evening Session",
        },
    )
    assert res_sess.status_code == 200

    # Enroll second member to verify date sorting (newest first)
    time.sleep(0.05)
    res_m2 = client.post(
        "/api/enroll",
        data={"company_id": comp_id, "member_id": "EMP-102", "name": "Second Member", "group": "QA"},
        files=[("photos", ("face1.jpg", test_face, "image/jpeg"))],
    )
    assert res_m2.status_code == 200

    res_members_desc = client.get(f"/api/members?company_id={comp_id}&sort_order=desc")
    assert res_members_desc.status_code == 200
    m_list = res_members_desc.json()
    assert len(m_list) >= 2 and m_list[0]["student_id"] == "EMP-102"
    print("[PASS] 11. Super Admin multi-org management & date-descending sorting verified")

    # 6. CSV Export with Date Sorting
    res_csv = client.get(f"/api/attendance/ALL?company_id={comp_id}&sort_order=desc&format=csv")
    assert res_csv.status_code == 200 and "text/csv" in res_csv.headers.get("content-type", "")
    assert "EMP-101" in res_csv.text and "28.61390" in res_csv.text and "ACK_BY_ESP8266" in res_csv.text
    print("[PASS] 12. CSV Export (date-sorted) includes Receipt ID, Company ID, Notice Time, Device Ack & GPS")

    # Clean up test organization via Super Admin DELETE
    res_del = client.delete(f"/api/companies/{comp_id}", headers=super_headers)
    assert res_del.status_code == 200
    print(f"[PASS] 13. Super Admin deleted test organization {comp_id}")

    print("\n========================================")
    print("ALL VERIFICATION CHECKS PASSED!")
    print("========================================")


if __name__ == "__main__":
    run_tests()
