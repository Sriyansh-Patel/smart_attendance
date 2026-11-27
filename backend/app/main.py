import io
import csv
import base64
import threading
from typing import List, Optional, Dict, Any
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
    DEVICE_SECRET_KEY,
    OTP_LIFETIME_SECONDS,
    SUPER_ADMIN_PIN,
)
from .database import (
    init_db,
    get_db_status,
    get_device_info,
    list_devices,
    verify_device,
    register_company,
    authenticate_company,
    get_company,
    list_companies,
    update_company,
    delete_company,
    get_companies_overview,
    register_company_device,
    save_student,
    get_student,
    delete_student,
    list_students,
    create_session,
    activate_session,
    delete_session,
    get_active_session,
    get_session,
    list_sessions,
    record_attendance,
    get_pending_attendance_event,
    acknowledge_attendance_event,
    get_attendance_by_id,
    get_session_attendance,
)
from .face_engine import face_engine
from .otp_service import (
    get_or_create_active_otp,
    get_or_create_device_otp,
    rotate_otp_after_attendance,
    get_otp_public_status,
    check_student_otp,
    verify_and_consume_student_otp,
)

app = FastAPI(
    title="Universal Smart Face & OTP Attendance System",
    description="Multi-Organization Attendance Platform with ESP8266/ESP32 60s Single-Use OTP, Post-Attendance Rotation, ArcFace/SFace Face ID, MediaPipe Liveness & Super Admin Control",
    version="3.1.0",
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


class DeviceAckRequest(BaseModel):
    device_key: str
    event_id: Optional[str] = None
    company_id: Optional[str] = None


class CompanyRegisterRequest(BaseModel):
    company_id: str
    company_name: str
    admin_name: str
    admin_pin: str
    org_type: Optional[str] = "general"
    device_key: Optional[str] = None


class CompanyUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    admin_name: Optional[str] = None
    org_type: Optional[str] = None
    admin_pin: Optional[str] = None
    device_key: Optional[str] = None
    device_name: Optional[str] = "ESP8266-LED-Matrix"


class CompanyLoginRequest(BaseModel):
    company_id: str
    admin_pin: str


class CompanyDeviceRequest(BaseModel):
    company_id: str
    admin_pin: Optional[str] = None
    admin_company_id: Optional[str] = None
    device_key: str
    device_name: Optional[str] = "ESP8266-LED-Matrix"


class SessionCreateRequest(BaseModel):
    session_id: str
    company_id: Optional[str] = DEFAULT_COMPANY_ID
    admin_pin: Optional[str] = None
    admin_company_id: Optional[str] = None
    course_name: Optional[str] = None
    session_title: Optional[str] = None
    cutoff_time: Optional[str] = None  # HH:MM (24h)


def _norm_comp(val: Optional[str]) -> str:
    return (val or DEFAULT_COMPANY_ID).strip().upper().replace(" ", "_")


def _is_super_admin_credentials(company_id: str, pin: str) -> bool:
    c_id = _norm_comp(company_id)
    if c_id in ("ALL", "SUPER_ADMIN", DEFAULT_COMPANY_ID):
        if pin.strip() == SUPER_ADMIN_PIN:
            return True
        if authenticate_company(DEFAULT_COMPANY_ID, pin):
            return True
    return False


def _verify_admin_access(
    request: Request,
    target_company_id: Optional[str] = None,
    explicit_admin_company: Optional[str] = None,
    explicit_admin_pin: Optional[str] = None,
) -> Dict[str, Any]:
    admin_comp = (
        explicit_admin_company
        or request.headers.get("X-Admin-Company")
        or target_company_id
        or ""
    ).strip()
    admin_pin = (
        explicit_admin_pin
        or request.headers.get("X-Admin-Pin")
        or ""
    ).strip()

    if not admin_comp or not admin_pin:
        raise HTTPException(status_code=401, detail="Administrator credentials required (X-Admin-Company and X-Admin-Pin).")

    if _is_super_admin_credentials(admin_comp, admin_pin):
        return {"is_super_admin": True, "company_id": DEFAULT_COMPANY_ID}

    company = authenticate_company(admin_comp, admin_pin)
    if not company:
        raise HTTPException(status_code=401, detail="Invalid Administrator ID or PIN.")

    if target_company_id and _norm_comp(target_company_id) not in ("ALL", "*", company["company_id"]):
        raise HTTPException(status_code=403, detail="Only Super Admin can manage other organizations.")

    return {"is_super_admin": False, "company_id": company["company_id"]}


def _extract_embeddings_from_images(raw_images: List[bytes]) -> tuple[List[List[float]], List[str]]:
    embeddings_list: List[List[float]] = []
    warnings: List[str] = []
    for idx, raw_bytes in enumerate(raw_images, start=1):
        img = face_engine.decode_image_bytes(raw_bytes)
        if img is None:
            warnings.append(f"Image {idx}: unreadable image format")
            continue
        emb, meta = face_engine.extract_embedding(img)
        if emb is None:
            warnings.append(f"Image {idx}: no face detected")
            continue
        if meta.get("faces_detected", 0) > 1:
            warnings.append(f"Image {idx}: multiple faces detected (using primary face)")
        embeddings_list.append(emb)
    return embeddings_list, warnings


# -------------------------------------------------------------
# 0. API Index, Health & Success Endpoints
# -------------------------------------------------------------
@app.get("/api", summary="List Available API Endpoints")
async def api_index():
    return {
        "status": "ok",
        "docs": "/docs",
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "endpoints": {
            "health": "GET /api/health",
            "success": "GET /api/success",
            "companies_list": "GET /api/companies",
            "company_register": "POST /api/companies/register",
            "company_login": "POST /api/companies/login",
            "company_update": "PUT /api/companies/{company_id}",
            "company_delete": "DELETE /api/companies/{company_id}",
            "admin_overview": "GET /api/admin/overview",
            "esp8266_otp_post": "POST /api/device/otp",
            "esp8266_attendance_event": "GET /api/device/attendance-event?device_key=...",
            "esp8266_attendance_ack": "POST /api/device/attendance-event/ack",
            "enroll_or_update_face": "POST /api/enroll",
            "member_update_face": "PUT /api/members/{company_id}/{student_id}/face",
            "attendance_mark": "POST /api/attendance",
            "attendance_receipt": "GET /api/attendance/receipt/{attendance_id}",
            "attendance_list": "GET /api/attendance/{session}?sort_order=desc",
            "attendance_csv": "GET /api/attendance/{session}?format=csv",
            "members": "GET /api/members?sort_order=desc",
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
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "database": get_db_status(),
        "face_engine": face_engine.backend_type,
        "active_session": get_active_session(),
    }


@app.get("/api/success", summary="Attendance Success & Verification Endpoint")
async def api_success(attendance_id: Optional[str] = Query(None, description="Optional attendance receipt ID to verify")):
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
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "database": get_db_status(),
        "timestamp": now_iso,
    }


# -------------------------------------------------------------
# 1. Company / Organization & Super Admin Management Endpoints
# -------------------------------------------------------------
@app.get("/api/companies", summary="List Registered Organizations (Sorted by Date)")
async def api_list_companies(
    sort_order: str = Query("desc", description="Sort by created_at date: 'desc' or 'asc'"),
    include_stats: bool = Query(False, description="Include member, session, attendance & device stats"),
):
    if include_stats:
        return get_companies_overview(sort_order=sort_order)
    return list_companies(sort_order=sort_order)


@app.post("/api/companies/register", summary="Register a New Organization / Administrator")
async def api_register_company(req: CompanyRegisterRequest):
    if not req.company_id.strip() or not req.company_name.strip() or not req.admin_name.strip() or len(req.admin_pin.strip()) < 4:
        raise HTTPException(status_code=400, detail="Provide company_id, company_name, admin_name, and admin_pin (min 4 chars).")
    if _norm_comp(req.company_id) in ("ALL", "SUPER_ADMIN"):
        raise HTTPException(status_code=400, detail="Reserved Organization ID.")
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
            "message": f"Organization '{company['company_name']}' registered successfully.",
            "company": company,
            "default_session_id": f"{company['company_id']}_SESSION_01",
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/companies/login", summary="Authenticate Organization Admin or Super Admin")
async def api_login_company(req: CompanyLoginRequest):
    req_comp = _norm_comp(req.company_id)
    if _is_super_admin_credentials(req_comp, req.admin_pin):
        default_comp = get_company(DEFAULT_COMPANY_ID) or {
            "company_id": DEFAULT_COMPANY_ID,
            "company_name": "Global Super Admin",
            "admin_name": "Primary Administrator",
            "org_type": DEFAULT_ORG_MODE,
        }
        active_sess = get_active_session(DEFAULT_COMPANY_ID)
        return {
            "status": "success",
            "is_super_admin": True,
            "company": default_comp,
            "active_session": active_sess,
            "total_organizations": len(list_companies()),
        }

    company = authenticate_company(req.company_id, req.admin_pin)
    if not company:
        raise HTTPException(status_code=401, detail="Invalid Organization ID or Admin PIN.")
    active_sess = get_active_session(company["company_id"])
    return {
        "status": "success",
        "is_super_admin": False,
        "company": company,
        "active_session": active_sess,
    }


@app.get("/api/admin/overview", summary="Super Admin Overview Across Every Organization")
async def api_admin_overview(
    request: Request,
    sort_order: str = Query("desc"),
):
    auth = _verify_admin_access(request)
    if auth["is_super_admin"]:
        orgs = get_companies_overview(sort_order=sort_order)
    else:
        orgs = [o for o in get_companies_overview(sort_order=sort_order) if o["company_id"] == auth["company_id"]]

    return {
        "is_super_admin": auth["is_super_admin"],
        "total_organizations": len(orgs),
        "total_members": sum(o["member_count"] for o in orgs),
        "total_sessions": sum(o["session_count"] for o in orgs),
        "total_attendance": sum(o["attendance_count"] for o in orgs),
        "organizations": orgs,
    }


@app.put("/api/companies/{company_id}", summary="Update an Organization (Super Admin or Org Admin)")
async def api_update_company(company_id: str, req: CompanyUpdateRequest, request: Request):
    target_id = _norm_comp(company_id)
    _verify_admin_access(request, target_company_id=target_id)
    if req.admin_pin is not None and req.admin_pin.strip() and len(req.admin_pin.strip()) < 4:
        raise HTTPException(status_code=400, detail="Admin PIN must be at least 4 characters.")

    updated = update_company(
        company_id=target_id,
        company_name=req.company_name,
        admin_name=req.admin_name,
        org_type=req.org_type,
        admin_pin=req.admin_pin,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Organization '{target_id}' not found.")

    if req.device_key and req.device_key.strip():
        register_company_device(target_id, req.device_key.strip(), req.device_name or "ESP8266-LED-Matrix")

    return {
        "status": "success",
        "message": f"Organization '{updated['company_name']}' updated.",
        "company": updated,
    }


@app.delete("/api/companies/{company_id}", summary="Delete an Organization (Super Admin)")
async def api_delete_company(company_id: str, request: Request):
    target_id = _norm_comp(company_id)
    _verify_admin_access(request, target_company_id=target_id)
    try:
        deleted = delete_company(target_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Organization '{target_id}' not found.")
    return {
        "status": "success",
        "message": f"Organization '{target_id}' and its scoped records were deleted.",
        "company_id": target_id,
    }


@app.post("/api/companies/device", summary="Assign ESP8266/ESP32 Device Key to an Organization")
async def api_register_company_device(req: CompanyDeviceRequest, request: Request):
    target_id = _norm_comp(req.company_id)
    _verify_admin_access(
        request,
        target_company_id=target_id,
        explicit_admin_company=req.admin_company_id or target_id,
        explicit_admin_pin=req.admin_pin,
    )
    company = get_company(target_id)
    if not company:
        raise HTTPException(status_code=404, detail=f"Organization '{target_id}' not found.")
    register_company_device(company["company_id"], req.device_key, req.device_name or "ESP8266-LED-Matrix")
    return {
        "status": "success",
        "message": f"Hardware device key linked to {company['company_name']}.",
        "company_id": company["company_id"],
    }


@app.get("/api/admin/devices", summary="List Registered Hardware Devices")
async def api_list_devices(request: Request, company_id: Optional[str] = Query(None)):
    auth = _verify_admin_access(request, target_company_id=company_id)
    target = company_id if auth["is_super_admin"] else auth["company_id"]
    return list_devices(company_id=target)


def _resolve_device_company(device_key: str, company_id: Optional[str] = None) -> str:
    key = device_key.strip()
    if not key:
        raise HTTPException(status_code=401, detail="device_key is required (ESP8266 hardware only)")
    comp = verify_device(key)
    if not comp:
        if key == DEVICE_SECRET_KEY:
            comp = DEFAULT_COMPANY_ID
        else:
            raise HTTPException(status_code=401, detail="Unauthorized device key")
    return (company_id or comp or DEFAULT_COMPANY_ID).strip().upper()


# -------------------------------------------------------------
# 2. ESP8266 / ESP32 Hardware APIs (60s OTP, Event, Ack)
# -------------------------------------------------------------
@app.get("/api/otp", summary="ESP8266 OTP Retrieval (GET with device_key)")
@app.get("/api/device/otp", summary="ESP8266 OTP Retrieval (GET with device_key)")
async def device_otp_get(
    request: Request,
    device_key: Optional[str] = Query(None, description="ESP8266 device secret key"),
    company_id: Optional[str] = Query(None, description="Optional company ID override"),
):
    key = device_key or request.headers.get("X-Device-Key", "")
    if not key:
        raise HTTPException(status_code=401, detail="device_key is required (ESP8266 hardware only)")
    result = get_or_create_device_otp(key, company_id=company_id)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@app.post("/api/device/otp", summary="ESP8266 Fetch OTP (60s or Post-Attendance Refresh)")
async def device_otp(request: DeviceOtpRequest):
    result = get_or_create_device_otp(request.device_key, company_id=request.company_id)
    if "error" in result:
        raise HTTPException(status_code=401, detail=result["error"])
    return result


@app.get("/api/device/attendance-event", summary="ESP8266 Check Pending Attendance Event")
async def device_check_attendance_event(
    request: Request,
    device_key: Optional[str] = Query(None, description="ESP8266 device secret key"),
    company_id: Optional[str] = Query(None, description="Optional company ID"),
):
    key = device_key or request.headers.get("X-Device-Key", "")
    comp_id = _resolve_device_company(key, company_id)
    event = get_pending_attendance_event(comp_id)
    active_otp = get_or_create_active_otp(source="esp8266", company_id=comp_id)
    if not event:
        return {
            "has_event": False,
            "company_id": comp_id,
            "otp": active_otp["otp"],
            "otp_expires_in": active_otp["expires_in"],
            "otp_duration_seconds": OTP_LIFETIME_SECONDS,
            "message": "No pending attendance events",
        }
    return {
        "has_event": True,
        "event_id": event["event_id"],
        "attendance_id": event["attendance_id"],
        "company_id": event["company_id"],
        "session_id": event["session_id"],
        "member_id": event["student_id"],
        "student_id": event["student_id"],
        "name": event["student_name"],
        "status": event["status"],
        "attendance_notice": event["attendance_notice"],
        "notice_time": event["notice_time"],
        "similarity_score": event["similarity_score"],
        "timestamp": event["timestamp"],
        "otp_rotated": True,
        "next_otp": active_otp["otp"],
        "otp_expires_in": active_otp["expires_in"],
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
    }


@app.post("/api/device/attendance-event/ack", summary="ESP8266 Acknowledge Attendance Event")
async def device_ack_attendance_event(req: DeviceAckRequest):
    comp_id = _resolve_device_company(req.device_key, req.company_id)
    acked = acknowledge_attendance_event(comp_id, event_id=req.event_id)
    active_otp = get_or_create_active_otp(source="esp8266", company_id=comp_id)
    if not acked:
        return {
            "status": "no_pending_event",
            "acknowledged": False,
            "company_id": comp_id,
            "otp": active_otp["otp"],
            "otp_expires_in": active_otp["expires_in"],
            "message": "No pending attendance event found to acknowledge.",
        }
    return {
        "status": "acknowledged",
        "acknowledged": True,
        "event_id": acked["event_id"],
        "attendance_id": acked.get("attendance_id"),
        "company_id": comp_id,
        "member_id": acked.get("student_id"),
        "name": acked.get("student_name"),
        "device_status": "ACK_BY_ESP8266",
        "acknowledged_at": acked.get("acknowledged_at"),
        "otp": active_otp["otp"],
        "otp_expires_in": active_otp["expires_in"],
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
    }


# -------------------------------------------------------------
# 3. Member / Student Enrollment & Face ID Update
# -------------------------------------------------------------
@app.post("/api/enroll", summary="Enroll a Member or Update Existing Member Face ID")
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
    raw_images: List[bytes] = []

    if "application/json" in content_type:
        body = await request.json()
        comp_id = _norm_comp(body.get("company_id"))
        s_id = (body.get("member_id") or body.get("student_id") or "").strip()
        s_name = (body.get("name") or "").strip()
        s_dept = (body.get("group") or body.get("department") or "").strip()
        b64_list = body.get("images_base64", [])

        existing = get_student(s_id, company_id=comp_id) if s_id else None
        if existing:
            s_name = s_name or existing["name"]
            s_dept = s_dept or existing.get("department") or "General"
        else:
            s_dept = s_dept or "General"

        if not s_id or not s_name or not b64_list:
            raise HTTPException(status_code=400, detail="Missing member_id/student_id, name, or images_base64")

        for b64 in b64_list:
            try:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw_images.append(base64.b64decode(b64))
            except Exception as e:
                print(f"Error parsing base64 image: {e}")
    else:
        comp_id = _norm_comp(company_id)
        s_id = (member_id or student_id or "").strip()
        s_name = (name or "").strip()
        s_dept = (group or department or "").strip()

        existing = get_student(s_id, company_id=comp_id) if s_id else None
        if existing:
            s_name = s_name or existing["name"]
            s_dept = s_dept or existing.get("department") or "General"
        else:
            s_dept = s_dept or "General"

        if not s_id or not s_name or not photos:
            raise HTTPException(status_code=400, detail="Fields 'student_id'/'member_id', 'name', and at least one photo file are required")

        for photo in photos:
            raw_images.append(await photo.read())

    embeddings_list, warnings = _extract_embeddings_from_images(raw_images)
    if not embeddings_list:
        detail_msg = "No valid faces detected in the provided photos. Center your face with good lighting and try again."
        if warnings:
            detail_msg += f" ({'; '.join(warnings)})"
        raise HTTPException(status_code=400, detail=detail_msg)

    master_embedding = face_engine.compute_average_embedding(embeddings_list)
    saved = save_student(s_id, s_name, s_dept, master_embedding, company_id=comp_id)
    verb = "Face ID updated" if saved["action"] == "updated" else "enrolled"

    return {
        "status": "success",
        "action": saved["action"],
        "updated": saved["action"] == "updated",
        "message": f"{saved['name']} ({saved['student_id']}) {verb} under {comp_id}.",
        "company_id": comp_id,
        "member_id": saved["student_id"],
        "student_id": saved["student_id"],
        "name": saved["name"],
        "department": saved["department"],
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
        "shots_processed": len(embeddings_list),
        "embedding_dimensions": len(master_embedding),
    }


@app.put("/api/members/{company_id}/{student_id}/face", summary="Update Existing Member Face ID")
@app.post("/api/members/{company_id}/{student_id}/face", summary="Update Existing Member Face ID")
async def update_member_face(
    company_id: str,
    student_id: str,
    request: Request,
    name: Optional[str] = Form(None),
    department: Optional[str] = Form(None),
    photos: List[UploadFile] = File(None),
):
    comp_id = _norm_comp(company_id)
    existing = get_student(student_id, company_id=comp_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Member '{student_id}' not found under '{comp_id}'.")

    content_type = request.headers.get("content-type", "")
    raw_images: List[bytes] = []
    s_name = existing["name"]
    s_dept = existing.get("department") or "General"

    if "application/json" in content_type:
        body = await request.json()
        if body.get("name") and str(body["name"]).strip():
            s_name = str(body["name"]).strip()
        if body.get("department") and str(body["department"]).strip():
            s_dept = str(body["department"]).strip()
        elif body.get("group") and str(body["group"]).strip():
            s_dept = str(body["group"]).strip()
        b64_list = body.get("images_base64", [])
        for b64 in b64_list:
            try:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw_images.append(base64.b64decode(b64))
            except Exception as e:
                print(f"Error parsing base64 image: {e}")
    else:
        if name and name.strip():
            s_name = name.strip()
        if department and department.strip():
            s_dept = department.strip()
        if photos:
            for photo in photos:
                raw_images.append(await photo.read())

    if not raw_images:
        raise HTTPException(status_code=400, detail="Provide at least one photo or images_base64 to update Face ID.")

    embeddings_list, warnings = _extract_embeddings_from_images(raw_images)
    if not embeddings_list:
        detail_msg = "No valid face detected in the provided photo(s)."
        if warnings:
            detail_msg += f" ({'; '.join(warnings)})"
        raise HTTPException(status_code=400, detail=detail_msg)

    master_embedding = face_engine.compute_average_embedding(embeddings_list)
    saved = save_student(existing["student_id"], s_name, s_dept, master_embedding, company_id=comp_id)
    return {
        "status": "success",
        "action": "updated",
        "updated": True,
        "message": f"Face ID updated for {saved['name']} ({saved['student_id']}) under {comp_id}.",
        "company_id": comp_id,
        "member_id": saved["student_id"],
        "student_id": saved["student_id"],
        "name": saved["name"],
        "department": saved["department"],
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
        "shots_processed": len(embeddings_list),
        "embedding_dimensions": len(master_embedding),
    }


@app.delete("/api/members/{company_id}/{student_id}", summary="Delete Enrolled Member (Admin)")
async def api_delete_member(company_id: str, student_id: str, request: Request):
    comp_id = _norm_comp(company_id)
    _verify_admin_access(request, target_company_id=comp_id)
    deleted = delete_student(student_id, comp_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Member '{student_id}' not found under '{comp_id}'.")
    return {
        "status": "success",
        "message": f"Member '{student_id}' removed from '{comp_id}'.",
        "company_id": comp_id,
        "student_id": student_id,
    }


def _compute_notice(cutoff_time: Optional[str], client_time: Optional[str]) -> tuple[str, str]:
    """Formats readable notice time and calculates ON_TIME / LATE status if cutoff_time is set."""
    now_utc = datetime.now(timezone.utc)
    display_time = client_time.strip() if client_time and client_time.strip() else now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    if not cutoff_time:
        return display_time, "RECORDED"

    try:
        cutoff_parts = cutoff_time.strip().split(":")
        cutoff_mins = int(cutoff_parts[0]) * 60 + int(cutoff_parts[1])

        if client_time and ":" in client_time:
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
@app.post("/api/attendance", summary="Verify Face + 60s Single-Use OTP + Immediate OTP Rotation")
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
        comp_id = _norm_comp(body.get("company_id"))
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
        comp_id = _norm_comp(company_id)
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

    img = face_engine.decode_image_bytes(raw_image_bytes)
    if img is None:
        raise HTTPException(status_code=400, detail="Unable to decode uploaded face image.")

    with _attendance_lock:
        # 1. Non-destructive check of OTP validity (60s window)
        if not check_student_otp(otp_code, sess_id, comp_id):
            raise HTTPException(
                status_code=403,
                detail="Invalid, expired, or already-used OTP. Wait for the next 60s code on the physical LED display.",
            )

        # 2. Check enrolled member within the selected organization
        member = get_student(s_id, company_id=comp_id)
        if not member:
            raise HTTPException(
                status_code=404,
                detail=f"ID '{s_id}' is not enrolled under organization '{comp_id}'. Please enroll first.",
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

        # 4. Cosine Similarity Check (Face ID verification)
        active_threshold = face_engine.get_threshold()
        try:
            similarity = face_engine.cosine_similarity(live_emb, member["embedding"])
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))

        print(f"[FaceVerify] company={comp_id} member={member['student_id']} score={similarity} threshold={active_threshold} engine={face_engine.backend_type}")
        if similarity < active_threshold:
            raise HTTPException(
                status_code=401,
                detail=f"Face ID verification failed. Match score ({similarity:.2f}) below required threshold ({active_threshold:.2f}).",
            )

        # 5. Atomically consume OTP so a second user cannot reuse it
        if not verify_and_consume_student_otp(otp_code, sess_id, comp_id):
            raise HTTPException(
                status_code=409,
                detail="This OTP was just claimed by another user. Please use the next OTP shown on the display.",
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

        # 7. Record Attendance & Queue ESP8266 Attendance Event
        receipt = record_attendance(
            session_id=sess_id,
            student_id=member["student_id"],
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
            student_name=member["name"],
        )

        if not receipt:
            raise HTTPException(status_code=500, detail="Database error recording attendance.")

        # 8. Immediately rotate OTP to a brand-new 60s code after successful attendance mark
        rotated_otp = rotate_otp_after_attendance(comp_id, sess_id, previous_otp=otp_code)

    company_info = get_company(comp_id)
    return {
        "success": True,
        "status": "success",
        "message": f"Face ID verified and attendance marked for {member['name']}. OTP rotated for next user.",
        "attendance_id": receipt["attendance_id"],
        "event_id": receipt.get("event_id"),
        "device_status": receipt.get("device_status", "PENDING_ESP8266"),
        "company_id": comp_id,
        "company_name": company_info["company_name"] if company_info else comp_id,
        "member_id": member["student_id"],
        "student_id": member["student_id"],
        "name": member["name"],
        "department": member.get("department", "General"),
        "session_id": sess_id,
        "face_verified": True,
        "similarity_score": similarity,
        "threshold": active_threshold,
        "timestamp": receipt["timestamp"],
        "notice_time": notice_time,
        "attendance_notice": attendance_notice,
        "otp_rotated": True,
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "next_otp_expires_in": rotated_otp["expires_in"],
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
# 5. Session Attendance Listing (Sorted by Date) & CSV Export
# -------------------------------------------------------------
@app.get("/api/attendance/{session}", summary="Get Attendance for Session or ALL (Sorted by Date, JSON or CSV)")
async def get_attendance(
    session: str,
    company_id: Optional[str] = Query(None, description="Filter by Organization ID (or ALL)"),
    sort_order: str = Query("desc", description="Sort by date: 'desc' (newest first) or 'asc' (oldest first)"),
    date: Optional[str] = Query(None, description="Filter by date YYYY-MM-DD"),
    format: Optional[str] = Query("json", description="Output format: 'json' or 'csv'"),
):
    records = get_session_attendance(session, company_id=company_id, sort_order=sort_order, date_filter=date)

    if format.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Receipt ID", "Company ID", "Session ID", "Member ID", "Full Name",
            "Group / Department", "UTC Timestamp", "Notice Time", "Attendance Notice",
            "Location", "Latitude", "Longitude", "Match Score", "Status", "ESP8266 Status", "OTP Used"
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
                r.get("device_status", "PENDING_ESP8266"),
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
        "sort_order": sort_order,
        "date_filter": date,
        "total_records": len(records),
        "records": records,
    }


# -------------------------------------------------------------
# 6. Management Endpoints (Scoped or Super Admin Across All Orgs)
# -------------------------------------------------------------
@app.post("/api/sessions", summary="Create or Activate Organization Session / Shift")
def api_create_session(req: SessionCreateRequest, request: Request):
    comp_id = _norm_comp(req.company_id)
    if req.admin_pin or request.headers.get("X-Admin-Pin"):
        _verify_admin_access(
            request,
            target_company_id=comp_id,
            explicit_admin_company=req.admin_company_id or comp_id,
            explicit_admin_pin=req.admin_pin,
        )
    title = req.session_title or req.course_name or req.session_id
    return create_session(req.session_id, title, company_id=comp_id, cutoff_time=req.cutoff_time)


@app.post("/api/sessions/{company_id}/{session_id}/activate", summary="Activate an Existing Session (Admin)")
def api_activate_session(company_id: str, session_id: str, request: Request):
    comp_id = _norm_comp(company_id)
    _verify_admin_access(request, target_company_id=comp_id)
    activated = activate_session(comp_id, session_id)
    if not activated:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found under '{comp_id}'.")
    return {"status": "success", "session": activated}


@app.delete("/api/sessions/{company_id}/{session_id}", summary="Delete a Session (Admin)")
def api_delete_session(company_id: str, session_id: str, request: Request):
    comp_id = _norm_comp(company_id)
    _verify_admin_access(request, target_company_id=comp_id)
    deleted = delete_session(comp_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found under '{comp_id}'.")
    return {"status": "success", "message": f"Session '{session_id}' deleted."}


@app.get("/api/sessions", summary="List Sessions (Sorted by Date)")
def api_list_sessions(
    company_id: Optional[str] = Query(None),
    sort_order: str = Query("desc", description="Sort by date: 'desc' or 'asc'"),
):
    return list_sessions(company_id=company_id, sort_order=sort_order)


@app.get("/api/session/current", summary="Get Current Active Session for an Organization")
def api_current_session(company_id: Optional[str] = Query(None)):
    comp_id = _norm_comp(company_id)
    sess = get_active_session(company_id=comp_id)
    if not sess:
        return {"session_id": "NONE", "company_id": comp_id, "course_name": "No active session", "is_active": 0}
    return sess


@app.get("/api/students", summary="List Enrolled Members (Alias, Sorted by Date)")
@app.get("/api/members", summary="List Enrolled Members (Sorted by Date)")
def api_list_members(
    company_id: Optional[str] = Query(None),
    sort_order: str = Query("desc", description="Sort by date: 'desc' or 'asc'"),
):
    return list_students(company_id=company_id, sort_order=sort_order)


@app.get("/api/device/status", summary="Check ESP8266, 60s OTP Window & Database Status")
def api_device_status(company_id: Optional[str] = Query(None)):
    comp_id = _norm_comp(company_id)
    sess = get_active_session(comp_id)
    device_info = get_device_info(comp_id) or get_device_info()
    esp8266_online = False
    last_seen = device_info.get("last_seen") if device_info else None
    if last_seen:
        try:
            dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            esp8266_online = (datetime.now(timezone.utc) - dt).total_seconds() <= 90
        except Exception:
            pass

    device_payload = {
        "online": esp8266_online,
        "device_name": device_info.get("device_name") if device_info else "ESP8266-LED-Matrix",
        "last_seen": last_seen,
    }
    return {
        "company_id": comp_id,
        "active_session": sess,
        "database": get_db_status(),
        "esp8266": device_payload,
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "otp_status": get_otp_public_status(comp_id, sess["session_id"] if sess else None),
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
