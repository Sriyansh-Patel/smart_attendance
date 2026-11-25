import io
import csv
import base64
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
)
from .database import (
    init_db,
    get_db_status,
    get_device_info,
    verify_device,
    save_student,
    get_student,
    list_students,
    create_session,
    get_active_session,
    list_sessions,
    record_attendance,
    get_session_attendance,
    get_current_valid_otp,
)
from .face_engine import face_engine
from .otp_service import (
    get_or_create_active_otp,
    get_or_create_device_otp,
    verify_student_otp,
)

app = FastAPI(
    title="Universal Smart Face & OTP Attendance System",
    description="General-purpose Attendance API for Offices, Schools, Universities & Events with ESP32 LED Matrix OTP, ArcFace Recognition, MediaPipe Liveness & MongoDB",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    init_db()
    db_info = get_db_status()
    print(f"[Server] Startup complete. Database={db_info['backend']} ({db_info['database_name']})")


# --- Request Models ---
class DeviceOtpRequest(BaseModel):
    device_key: str


class SessionCreateRequest(BaseModel):
    session_id: str
    course_name: Optional[str] = None
    session_title: Optional[str] = None


# -------------------------------------------------------------
# 0. Health & System Info
# -------------------------------------------------------------
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


# -------------------------------------------------------------
# 1. ESP32 Device OTP Endpoint (Hardware Only)
# -------------------------------------------------------------
@app.post("/api/device/otp", summary="ESP32 OTP Retrieval")
async def device_otp(request: DeviceOtpRequest):
    """
    Called exclusively by the ESP32 hardware via HTTPClient:
    Sends device_key, authenticates device heartbeat, and returns active rotating OTP.
    """
    result = get_or_create_device_otp(request.device_key)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


# -------------------------------------------------------------
# 2. Member / Student Enrollment
# POST /api/enroll
# -------------------------------------------------------------
@app.post("/api/enroll", summary="Enroll a Member (Employee / Student / Attendee)")
async def enroll_member(
    request: Request,
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
            detail="No valid faces detected in the provided photos. Ensure proper lighting and unobstructed face.",
        )

    master_embedding = face_engine.compute_average_embedding(embeddings_list)
    save_student(s_id, s_name, s_dept, master_embedding)

    return {
        "status": "success",
        "message": f"{s_name} ({s_id}) successfully enrolled.",
        "member_id": s_id,
        "student_id": s_id,
        "name": s_name,
        "department": s_dept,
        "shots_processed": len(embeddings_list),
        "embedding_dimensions": len(master_embedding),
    }


# -------------------------------------------------------------
# 3. Attendance Verification
# POST /api/attendance
# -------------------------------------------------------------
@app.post("/api/attendance", summary="Verify Face + OTP Attendance")
async def mark_attendance(
    request: Request,
    student_id: Optional[str] = Form(None),
    member_id: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    otp: Optional[str] = Form(None),
    face_image: Optional[UploadFile] = File(None),
):
    content_type = request.headers.get("content-type", "")
    raw_image_bytes = None

    if "application/json" in content_type:
        body = await request.json()
        s_id = body.get("member_id") or body.get("student_id")
        sess_id = body.get("session_id")
        otp_code = body.get("otp")
        b64 = body.get("image_base64", "")
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            raw_image_bytes = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 face_image")
    else:
        s_id = member_id or student_id
        sess_id = session_id
        otp_code = otp
        if face_image:
            raw_image_bytes = await face_image.read()

    if not s_id or not sess_id or not otp_code or not raw_image_bytes:
        raise HTTPException(
            status_code=400,
            detail="Missing required parameters: member_id/student_id, session_id, otp, and face_image are required.",
        )

    # 1. Verify OTP
    if not verify_student_otp(otp_code, sess_id):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired OTP. Please check the active code on the ESP32 LED display or web console.",
        )

    # 2. Check enrolled member record
    member = get_student(s_id)
    if not member:
        raise HTTPException(
            status_code=404,
            detail=f"ID '{s_id}' not found. Please enroll first.",
        )

    # 3. Process submitted face
    img = face_engine.decode_image_bytes(raw_image_bytes)
    if img is None:
        raise HTTPException(status_code=400, detail="Unable to decode uploaded face image.")

    live_emb, meta = face_engine.extract_embedding(img)
    if not meta["blur_passed"]:
        raise HTTPException(
            status_code=422,
            detail=f"Photo is too blurry (sharpness: {meta['blur_score']}, min: {BLUR_THRESHOLD}). Please hold still.",
        )

    if meta["faces_detected"] == 0 or live_emb is None:
        raise HTTPException(
            status_code=422,
            detail="No face detected. Please ensure your face is well-lit and centered.",
        )

    if meta["faces_detected"] > 1:
        raise HTTPException(
            status_code=422,
            detail="Multiple faces detected in frame. Only one person allowed per verification.",
        )

    # 4. Cosine Similarity Check
    similarity = face_engine.cosine_similarity(live_emb, member["embedding"])
    if similarity < FACE_SIMILARITY_THRESHOLD:
        raise HTTPException(
            status_code=401,
            detail=f"Face verification failed. Match score ({similarity:.2f}) below threshold ({FACE_SIMILARITY_THRESHOLD:.2f}).",
        )

    # 5. Record Attendance
    if not record_attendance(
        session_id=sess_id,
        student_id=s_id,
        confidence=similarity,
        status="PRESENT",
        otp_used=otp_code,
    ):
        raise HTTPException(status_code=500, detail="Database error recording attendance.")

    return {
        "status": "success",
        "message": f"Attendance verified successfully for {member['name']}.",
        "member_id": s_id,
        "student_id": s_id,
        "name": member["name"],
        "department": member.get("department", "General"),
        "session_id": sess_id,
        "similarity_score": similarity,
        "threshold": FACE_SIMILARITY_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# -------------------------------------------------------------
# 4. Session Attendance Listing & CSV Export
# -------------------------------------------------------------
@app.get("/api/attendance/{session}", summary="Get Attendance for Session (JSON or CSV)")
async def get_attendance(
    session: str,
    format: Optional[str] = Query("json", description="Output format: 'json' or 'csv'"),
):
    records = get_session_attendance(session)

    if format.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Session ID", "Member ID", "Full Name", "Group / Department", "Timestamp", "Match Score", "Status", "OTP Used"])
        for r in records:
            writer.writerow([
                r.get("id"),
                r.get("session_id"),
                r.get("student_id"),
                r.get("student_name", "N/A"),
                r.get("department", "N/A"),
                r.get("timestamp"),
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
        "total_records": len(records),
        "records": records,
    }


# -------------------------------------------------------------
# 5. Management Endpoints
# -------------------------------------------------------------
@app.post("/api/sessions", summary="Create or Activate Attendance Session / Shift")
def api_create_session(req: SessionCreateRequest):
    title = req.session_title or req.course_name or req.session_id
    return create_session(req.session_id, title)


@app.get("/api/sessions", summary="List All Sessions")
def api_list_sessions():
    return list_sessions()


@app.get("/api/session/current", summary="Get Current Active Session")
def api_current_session():
    sess = get_active_session()
    if not sess:
        return {"session_id": "NONE", "course_name": "No active session", "is_active": 0}
    return sess


@app.get("/api/students", summary="List Enrolled Members (Alias)")
@app.get("/api/members", summary="List All Enrolled Members")
def api_list_members():
    return list_students()


@app.get("/api/device/status", summary="Check ESP32 & Database Status")
def api_device_status():
    sess = get_active_session()
    device_info = get_device_info()
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
