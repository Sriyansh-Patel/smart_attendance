import io
import csv
import base64
import threading
from typing import List, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from .config import (
    BASE_DIR,
    FACE_SIMILARITY_THRESHOLD,
    BLUR_THRESHOLD,
    ORG_NAME,
    DEFAULT_ORG_MODE,
    DEFAULT_COMPANY_ID,
)
from .database import (
    init_db,
    get_db_status,
    get_device_info,
    register_company,
    authenticate_company,
    get_company,
    list_companies,
    register_company_device,
    save_student,
    get_student,
    list_students,
    create_session,
    get_active_session,
    get_session,
    list_sessions,
    record_attendance,
    get_attendance_by_id,
    get_session_attendance,
)
from .face_engine import face_engine
from .otp_service import (
    get_or_create_device_otp,
    check_student_otp,
    verify_and_consume_student_otp,
)

app = FastAPI(
    title="Universal Smart Face & OTP Attendance System",
    description="Multi-Company Attendance Platform with ESP32 60s Single-Use OTP, ArcFace/SFace Recognition, MediaPipe Liveness, Location & Notice Time",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Strict serialization lock so only ONE user can mark attendance at once
_attendance_lock = threading.Lock()


@app.on_event("startup")
def startup_event():
    init_db()
    db_info = get_db_status()
    print(f"[Server] Startup complete. Database={db_info['backend']} ({db_info['database_name']})")


# --- Request Models ---
class DeviceOtpRequest(BaseModel):
    device_key: str
    company_id: Optional[str] = None


class CompanyRegisterRequest(BaseModel):
    company_id: str
    company_name: str
    admin_name: str
    admin_pin: str
    org_type: Optional[str] = "general"
    device_key: Optional[str] = None


class CompanyLoginRequest(BaseModel):
    company_id: str
    admin_pin: str


class CompanyDeviceRequest(BaseModel):
    company_id: str
    admin_pin: str
    device_key: str
    device_name: Optional[str] = "ESP32-LED-Matrix"


class SessionCreateRequest(BaseModel):
    session_id: str
    company_id: Optional[str] = DEFAULT_COMPANY_ID
    admin_pin: Optional[str] = None
    course_name: Optional[str] = None
    session_title: Optional[str] = None
    cutoff_time: Optional[str] = None  # HH:MM (24h)


# -------------------------------------------------------------
# 0. API Index, Health & Success Endpoints
# -------------------------------------------------------------
@app.get("/api", summary="List Available API Endpoints")
async def api_index():
    return {
        "status": "ok",
        "docs": "/docs",
        "endpoints": {
            "health": "GET /api/health",
            "success": "GET /api/success",
            "companies_list": "GET /api/companies",
            "company_register": "POST /api/companies/register",
            "company_login": "POST /api/companies/login",
            "esp32_otp_post": "POST /api/device/otp",
            "esp32_otp_get": "GET /api/device/otp?device_key=...",
            "otp_get": "GET /api/otp?device_key=...",
            "enroll": "POST /api/enroll",
            "attendance_mark": "POST /api/attendance",
            "attendance_receipt": "GET /api/attendance/receipt/{attendance_id}",
            "attendance_list": "GET /api/attendance/{session}",
            "attendance_csv": "GET /api/attendance/{session}?format=csv",
            "members": "GET /api/members",
            "sessions": "GET /api/sessions | POST /api/sessions",
            "device_status": "GET /api/device/status",
        },
    }


@app.get("/api/health", summary="System Health & Database Connection Status")
async def health_check():
    return {
        "status": "ok",
        "org_name": ORG_NAME,
        "default_org_mode": DEFAULT_ORG_MODE,
        "database": get_db_status(),
        "face_engine": face_engine.backend_type,
        "active_session": get_active_session(),
    }


@app.get("/api/success", summary="Attendance Success & Verification Endpoint")
async def api_success(attendance_id: Optional[str] = Query(None, description="Optional attendance receipt ID to verify")):
    """
    Dedicated API endpoint returning structured success confirmation.
    If `attendance_id` is passed, returns the verified attendance receipt details.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    if attendance_id:
        record = get_attendance_by_id(attendance_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Attendance receipt '{attendance_id}' not found")
        return {
            "success": True,
            "status": "verified",
            "message": "Attendance record confirmed.",
            "receipt": record,
            "verified_at": now_iso,
        }
    return {
        "success": True,
        "status": "ok",
        "message": "Smart Attendance API is healthy and ready.",
        "database": get_db_status(),
        "timestamp": now_iso,
    }


# -------------------------------------------------------------
# 1. Company / Administrator Management Endpoints
# -------------------------------------------------------------
@app.get("/api/companies", summary="List Registered Companies / Administrators")
async def api_list_companies():
    return list_companies()


@app.post("/api/companies/register", summary="Register a New Company / Administrator")
async def api_register_company(req: CompanyRegisterRequest):
    if not req.company_id.strip() or not req.company_name.strip() or not req.admin_name.strip() or len(req.admin_pin.strip()) < 4:
        raise HTTPException(status_code=400, detail="Provide company_id, company_name, admin_name, and admin_pin (min 4 chars).")
    try:
        company = register_company(
            company_id=req.company_id,
            company_name=req.company_name,
            admin_name=req.admin_name,
            admin_pin=req.admin_pin,
            org_type=req.org_type or "general",
        )
        if req.device_key and req.device_key.strip():
            register_company_device(company["company_id"], req.device_key.strip())
        return {
            "status": "success",
            "message": f"Company '{company['company_name']}' registered successfully.",
            "company": company,
            "default_session_id": f"{company['company_id']}_SESSION_01",
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/companies/login", summary="Authenticate Company Administrator")
async def api_login_company(req: CompanyLoginRequest):
    company = authenticate_company(req.company_id, req.admin_pin)
    if not company:
        raise HTTPException(status_code=401, detail="Invalid Company ID or Admin PIN.")
    active_sess = get_active_session(company["company_id"])
    return {
        "status": "success",
        "company": company,
        "active_session": active_sess,
    }


@app.post("/api/companies/device", summary="Assign ESP32 Device Key to a Company")
async def api_register_company_device(req: CompanyDeviceRequest):
    company = authenticate_company(req.company_id, req.admin_pin)
    if not company:
        raise HTTPException(status_code=401, detail="Invalid Company ID or Admin PIN.")
    register_company_device(company["company_id"], req.device_key, req.device_name or "ESP32-LED-Matrix")
    return {
        "status": "success",
        "message": f"ESP32 device key linked to {company['company_name']}.",
        "company_id": company["company_id"],
    }


# -------------------------------------------------------------
# 2. ESP32 Device OTP Endpoints (Device-Key Authenticated Only)
# -------------------------------------------------------------
@app.get("/api/otp", summary="ESP32 OTP Retrieval (GET with device_key)")
@app.get("/api/device/otp", summary="ESP32 OTP Retrieval (GET with device_key)")
async def device_otp_get(
    request: Request,
    device_key: Optional[str] = Query(None, description="ESP32 device secret key"),
    company_id: Optional[str] = Query(None, description="Optional company ID override"),
):
    key = device_key or request.headers.get("X-Device-Key", "")
    if not key:
        raise HTTPException(status_code=401, detail="device_key is required (ESP32 hardware only)")
    result = get_or_create_device_otp(key, company_id=company_id)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@app.post("/api/device/otp", summary="ESP32 OTP Retrieval (POST)")
async def device_otp(request: DeviceOtpRequest):
    result = get_or_create_device_otp(request.device_key, company_id=request.company_id)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


# -------------------------------------------------------------
# 3. Member / Student Enrollment
# -------------------------------------------------------------
@app.post("/api/enroll", summary="Enroll a Member Under a Company")
async def enroll_member(
    request: Request,
    company_id: Optional[str] = Form(None),
    student_id: Optional[str] = Form(None),
    member_id: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    group: Optional[str] = Form(None),
    photos: List[UploadFile] = File(None),
):
    content_type = request.headers.get("content-type", "")
    embeddings_list = []

    if "application/json" in content_type:
        body = await request.json()
        comp_id = (body.get("company_id") or DEFAULT_COMPANY_ID).strip().upper()
        s_id = body.get("member_id") or body.get("student_id")
        s_name = body.get("name")
        s_dept = body.get("group") or body.get("department") or "General"
        b64_list = body.get("images_base64", [])

        if not s_id or not s_name or not b64_list:
            raise HTTPException(status_code=400, detail="Missing member_id/student_id, name, or images_base64")

        for b64 in b64_list:
            try:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw_bytes = base64.b64decode(b64)
                img = face_engine.decode_image_bytes(raw_bytes)
                if img is not None:
                    emb, _ = face_engine.extract_embedding(img)
                    if emb is not None:
                        embeddings_list.append(emb)
            except Exception as e:
                print(f"Error parsing base64 image: {e}")
    else:
        comp_id = (company_id or DEFAULT_COMPANY_ID).strip().upper()
        s_id = member_id or student_id
        s_name = name
        s_dept = group or department or "General"
        if not s_id or not s_name or not photos:
            raise HTTPException(status_code=400, detail="Fields 'student_id'/'member_id', 'name', and at least one photo file are required")

        for photo in photos:
            raw_bytes = await photo.read()
            img = face_engine.decode_image_bytes(raw_bytes)
            if img is not None:
                emb, _ = face_engine.extract_embedding(img)
                if emb is not None:
                    embeddings_list.append(emb)

    if not embeddings_list:
        raise HTTPException(
            status_code=400,
            detail="No valid faces detected in the provided photos. Center your face with good lighting and try again.",
        )

    master_embedding = face_engine.compute_average_embedding(embeddings_list)
    save_student(s_id, s_name, s_dept, master_embedding, company_id=comp_id)

    return {
        "status": "success",
        "message": f"{s_name} ({s_id}) successfully enrolled under {comp_id}.",
        "company_id": comp_id,
        "member_id": s_id,
        "student_id": s_id,
        "name": s_name,
        "department": s_dept,
        "shots_processed": len(embeddings_list),
        "embedding_dimensions": len(master_embedding),
    }


def _compute_notice(cutoff_time: Optional[str], client_time: Optional[str]) -> tuple[str, str]:
    """Formats readable notice time and calculates ON_TIME / LATE status if cutoff_time is set."""
    now_utc = datetime.now(timezone.utc)
    display_time = client_time.strip() if client_time and client_time.strip() else now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    if not cutoff_time:
        return display_time, "RECORDED"

    try:
        # Compare client local HH:MM (or server UTC HH:MM if client time omitted)
        cutoff_parts = cutoff_time.strip().split(":")
        cutoff_mins = int(cutoff_parts[0]) * 60 + int(cutoff_parts[1])

        if client_time and ":" in client_time:
            # Extract HH:MM from client_time string (e.g. "10:35:12" or "2026-11-27 10:35:12")
            time_part = client_time.strip().split()[-1]
            t_parts = time_part.split(":")
            curr_mins = int(t_parts[0]) * 60 + int(t_parts[1])
        else:
            curr_mins = now_utc.hour * 60 + now_utc.minute

        notice = "LATE" if curr_mins > cutoff_mins else "ON_TIME"
        return f"{display_time} ({notice})", notice
    except Exception:
        return display_time, "RECORDED"


# -------------------------------------------------------------
# 4. Single-User-at-a-Time Attendance Verification
# -------------------------------------------------------------
@app.post("/api/attendance", summary="Verify Face + Single-Use OTP + Location + Notice Time")
async def mark_attendance(
    request: Request,
    company_id: Optional[str] = Form(None),
    student_id: Optional[str] = Form(None),
    member_id: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    otp: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    location_accuracy: Optional[float] = Form(None),
    client_time: Optional[str] = Form(None),
    face_image: Optional[UploadFile] = File(None),
):
    content_type = request.headers.get("content-type", "")
    raw_image_bytes = None

    if "application/json" in content_type:
        body = await request.json()
        comp_id = (body.get("company_id") or DEFAULT_COMPANY_ID).strip().upper()
        s_id = body.get("member_id") or body.get("student_id")
        sess_id = body.get("session_id")
        otp_code = body.get("otp")
        lat = body.get("latitude")
        lng = body.get("longitude")
        acc = body.get("location_accuracy")
        c_time = body.get("client_time")
        b64 = body.get("image_base64", "")
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw_image_bytes = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 face_image")
    else:
        comp_id = (company_id or DEFAULT_COMPANY_ID).strip().upper()
        s_id = member_id or student_id
        sess_id = session_id
        otp_code = otp
        lat = latitude
        lng = longitude
        acc = location_accuracy
        c_time = client_time
        if face_image:
            raw_image_bytes = await face_image.read()

    if not s_id or not sess_id or not otp_code or not raw_image_bytes:
        raise HTTPException(
            status_code=400,
            detail="Missing required parameters: member_id/student_id, session_id, otp, and face_image are required.",
        )

    # Decode image before acquiring critical attendance lock
    img = face_engine.decode_image_bytes(raw_image_bytes)
    if img is None:
        raise HTTPException(status_code=400, detail="Unable to decode uploaded face image.")

    # Serialize check-ins so ONLY ONE user can mark attendance at once
    with _attendance_lock:
        # 1. Non-destructive check of OTP validity
        if not check_student_otp(otp_code, sess_id, comp_id):
            raise HTTPException(
                status_code=403,
                detail="Invalid, expired, or already-used OTP. Wait for the next code on the ESP32 LED display.",
            )

        # 2. Check enrolled member within the selected company
        member = get_student(s_id, company_id=comp_id)
        if not member:
            raise HTTPException(
                status_code=404,
                detail=f"ID '{s_id}' is not enrolled under company '{comp_id}'. Please enroll first.",
            )

        # 3. Face detection, blur & single-face verification
        live_emb, meta = face_engine.extract_embedding(img)
        if not meta["blur_passed"]:
            raise HTTPException(
                status_code=422,
                detail=f"Photo is too blurry (sharpness: {meta['blur_score']}, min: {BLUR_THRESHOLD}). Hold still.",
            )

        if meta["faces_detected"] == 0 or live_emb is None:
            raise HTTPException(
                status_code=422,
                detail="No face detected. Ensure your face is centered and well-lit.",
            )

        if meta["faces_detected"] > 1:
            raise HTTPException(
                status_code=422,
                detail="Multiple faces detected! Only one person can mark attendance at a time.",
            )

        # 4. Cosine Similarity Check
        similarity = face_engine.cosine_similarity(live_emb, member["embedding"])
        if similarity < FACE_SIMILARITY_THRESHOLD:
            raise HTTPException(
                status_code=401,
                detail=f"Face verification failed. Match score ({similarity:.2f}) below threshold ({FACE_SIMILARITY_THRESHOLD:.2f}).",
            )

        # 5. Atomically consume OTP so a second concurrent/subsequent user cannot reuse it
        if not verify_and_consume_student_otp(otp_code, sess_id, comp_id):
            raise HTTPException(
                status_code=409,
                detail="This OTP was just claimed by another user. Please use the next OTP shown on the ESP32.",
            )

        # 6. Compute Location label and Notice Time
        sess_info = get_session(sess_id, comp_id)
        cutoff_time = sess_info.get("cutoff_time") if sess_info else None
        notice_time, attendance_notice = _compute_notice(cutoff_time, c_time)

        if lat is not None and lng is not None:
            acc_str = f" (±{int(acc)}m)" if acc is not None else ""
            loc_label = f"{float(lat):.5f}, {float(lng):.5f}{acc_str}"
        else:
            loc_label = "Location Unavailable"

        # 7. Record Attendance
        receipt = record_attendance(
            session_id=sess_id,
            student_id=s_id,
            confidence=similarity,
            status="PRESENT",
            otp_used=otp_code,
            company_id=comp_id,
            notice_time=notice_time,
            attendance_notice=attendance_notice,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lng) if lng is not None else None,
            location_accuracy=float(acc) if acc is not None else None,
            location_label=loc_label,
        )

        if not receipt:
            raise HTTPException(status_code=500, detail="Database error recording attendance.")

    company_info = get_company(comp_id)
    return {
        "success": True,
        "status": "success",
        "message": f"Attendance verified successfully for {member['name']}.",
        "attendance_id": receipt["attendance_id"],
        "company_id": comp_id,
        "company_name": company_info["company_name"] if company_info else comp_id,
        "member_id": s_id,
        "student_id": s_id,
        "name": member["name"],
        "department": member.get("department", "General"),
        "session_id": sess_id,
        "similarity_score": similarity,
        "threshold": FACE_SIMILARITY_THRESHOLD,
        "timestamp": receipt["timestamp"],
        "notice_time": notice_time,
        "attendance_notice": attendance_notice,
        "location": {
            "latitude": receipt["latitude"],
            "longitude": receipt["longitude"],
            "accuracy": receipt["location_accuracy"],
            "label": loc_label,
        },
        "success_url": f"/api/success?attendance_id={receipt['attendance_id']}",
    }


@app.get("/api/attendance/receipt/{attendance_id}", summary="Retrieve Attendance Confirmation Receipt")
async def get_receipt(attendance_id: str):
    record = get_attendance_by_id(attendance_id)
    if not record:
        raise HTTPException(status_code=404, detail="Attendance receipt not found")
    return {"success": True, "receipt": record}


# -------------------------------------------------------------
# 5. Session Attendance Listing & CSV Export
# -------------------------------------------------------------
@app.get("/api/attendance/{session}", summary="Get Attendance for Session (JSON or CSV)")
async def get_attendance(
    session: str,
    company_id: Optional[str] = Query(None, description="Filter by Company ID"),
    format: Optional[str] = Query("json", description="Output format: 'json' or 'csv'"),
):
    records = get_session_attendance(session, company_id=company_id)

    if format.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Receipt ID", "Company ID", "Session ID", "Member ID", "Full Name",
            "Group / Department", "UTC Timestamp", "Notice Time", "Attendance Notice",
            "Location", "Latitude", "Longitude", "Match Score", "Status", "OTP Used"
        ])
        for r in records:
            writer.writerow([
                r.get("id"),
                r.get("attendance_id", ""),
                r.get("company_id", DEFAULT_COMPANY_ID),
                r.get("session_id"),
                r.get("student_id"),
                r.get("student_name", "N/A"),
                r.get("department", "N/A"),
                r.get("timestamp"),
                r.get("notice_time", r.get("timestamp")),
                r.get("attendance_notice", "RECORDED"),
                r.get("location_label", "Not Provided"),
                r.get("latitude", ""),
                r.get("longitude", ""),
                r.get("confidence"),
                r.get("status"),
                r.get("otp_used"),
            ])
        output.seek(0)
        filename = f"attendance_{session}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return {
        "session_id": session,
        "company_id": company_id,
        "total_records": len(records),
        "records": records,
    }


# -------------------------------------------------------------
# 6. Management Endpoints (Scoped by Company)
# -------------------------------------------------------------
@app.post("/api/sessions", summary="Create or Activate Company Session / Shift")
def api_create_session(req: SessionCreateRequest):
    comp_id = (req.company_id or DEFAULT_COMPANY_ID).strip().upper()
    if req.admin_pin:
        if not authenticate_company(comp_id, req.admin_pin):
            raise HTTPException(status_code=401, detail="Invalid Admin PIN for this company.")
    title = req.session_title or req.course_name or req.session_id
    return create_session(req.session_id, title, company_id=comp_id, cutoff_time=req.cutoff_time)


@app.get("/api/sessions", summary="List Sessions for a Company")
def api_list_sessions(company_id: Optional[str] = Query(None)):
    return list_sessions(company_id=company_id)


@app.get("/api/session/current", summary="Get Current Active Session for a Company")
def api_current_session(company_id: Optional[str] = Query(None)):
    sess = get_active_session(company_id=company_id or DEFAULT_COMPANY_ID)
    if not sess:
        return {"session_id": "NONE", "company_id": company_id or DEFAULT_COMPANY_ID, "course_name": "No active session", "is_active": 0}
    return sess


@app.get("/api/students", summary="List Enrolled Members (Alias)")
@app.get("/api/members", summary="List Enrolled Members for a Company")
def api_list_members(company_id: Optional[str] = Query(None)):
    return list_students(company_id=company_id)


@app.get("/api/device/status", summary="Check ESP32 & Database Status")
def api_device_status(company_id: Optional[str] = Query(None)):
    comp_id = (company_id or DEFAULT_COMPANY_ID).strip().upper()
    sess = get_active_session(comp_id)
    device_info = get_device_info(comp_id) or get_device_info()
    esp32_online = False
    last_seen = device_info.get("last_seen") if device_info else None
    if last_seen:
        try:
            dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            esp32_online = (datetime.now(timezone.utc) - dt).total_seconds() <= 90
        except Exception:
            pass

    return {
        "company_id": comp_id,
        "active_session": sess,
        "database": get_db_status(),
        "esp32": {
            "online": esp32_online,
            "device_name": device_info.get("device_name") if device_info else "ESP32-LED-Matrix",
            "last_seen": last_seen,
        },
    }


# Serve Frontend static assets
static_path = BASE_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
def serve_index():
    index_file = BASE_DIR / "static" / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Universal Smart Attendance API is active. Go to /docs for Swagger UI."}
