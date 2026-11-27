import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from .config import DEVICE_SECRET_KEY, OTP_LIFETIME_SECONDS, DEFAULT_COMPANY_ID
from .database import (
    verify_device,
    save_new_otp,
    get_current_valid_otp,
    get_active_session,
    validate_and_consume_otp,
    is_otp_valid,
)


def _parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def generate_numeric_otp(digits: int = 6, exclude: Optional[str] = None) -> str:
    """Generate cryptographically randomized numeric OTP string."""
    lower = 10 ** (digits - 1)
    upper = (10 ** digits) - 1
    for _ in range(10):
        code = str(secrets.randbelow(upper - lower + 1) + lower)
        if not exclude or code != exclude:
            return code
    return str(secrets.randbelow(upper - lower + 1) + lower)


def get_or_create_active_otp(
    source: str = "esp8266",
    company_id: Optional[str] = None,
    session_id: Optional[str] = None,
    force_new: bool = False,
    exclude_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns the current active OTP for the company's active session, or generates a fresh
    rotating 60-second OTP if none exists, it is about to expire, or force_new is True.
    """
    comp_id = (company_id or DEFAULT_COMPANY_ID).strip().upper()
    if session_id and session_id.strip():
        target_session_id = session_id.strip()
    else:
        active_session = get_active_session(comp_id)
        target_session_id = active_session["session_id"] if active_session else "GLOBAL"

    now = datetime.now(timezone.utc)
    if not force_new:
        current_otp = get_current_valid_otp(target_session_id, comp_id)
        if current_otp:
            try:
                expires_at = _parse_iso_utc(current_otp["expires_at"])
                remaining_seconds = int((expires_at - now).total_seconds())
                if remaining_seconds > 2:
                    return {
                        "otp": current_otp["code"],
                        "expires_in": remaining_seconds,
                        "duration_seconds": OTP_LIFETIME_SECONDS,
                        "expiry": expires_at.isoformat(),
                        "created_at": current_otp.get("created_at"),
                        "company_id": comp_id,
                        "session_id": target_session_id,
                        "status": "active",
                    }
            except Exception:
                pass

    code = generate_numeric_otp(6, exclude=exclude_code)
    expires_at_dt = now + timedelta(seconds=OTP_LIFETIME_SECONDS)
    created_iso = now.isoformat()
    expires_iso = expires_at_dt.isoformat()

    save_new_otp(
        code=code,
        session_id=target_session_id,
        device_id=source,
        created_at=created_iso,
        expires_at=expires_iso,
        company_id=comp_id,
    )

    print(f"[OTP] New OTP for {comp_id}/{target_session_id}: {code} (valid {OTP_LIFETIME_SECONDS}s, source={source})")

    return {
        "otp": code,
        "expires_in": OTP_LIFETIME_SECONDS,
        "duration_seconds": OTP_LIFETIME_SECONDS,
        "expiry": expires_iso,
        "created_at": created_iso,
        "company_id": comp_id,
        "session_id": target_session_id,
        "status": "new",
    }


def rotate_otp_after_attendance(
    company_id: str,
    session_id: str,
    previous_otp: Optional[str] = None,
) -> Dict[str, Any]:
    """Immediately issues a fresh 60-second OTP after a successful attendance check-in."""
    return get_or_create_active_otp(
        source="post_attendance",
        company_id=company_id,
        session_id=session_id,
        force_new=True,
        exclude_code=previous_otp,
    )


def get_otp_public_status(company_id: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Returns non-secret OTP window metadata (remaining seconds, duration) without exposing the code."""
    comp_id = (company_id or DEFAULT_COMPANY_ID).strip().upper()
    if session_id and session_id.strip():
        target_session_id = session_id.strip()
    else:
        active_session = get_active_session(comp_id)
        target_session_id = active_session["session_id"] if active_session else "GLOBAL"

    now = datetime.now(timezone.utc)
    current_otp = get_current_valid_otp(target_session_id, comp_id)
    if current_otp:
        try:
            expires_at = _parse_iso_utc(current_otp["expires_at"])
            remaining = max(0, int((expires_at - now).total_seconds()))
            if remaining > 0:
                return {
                    "otp_active": True,
                    "otp_duration_seconds": OTP_LIFETIME_SECONDS,
                    "otp_expires_in": remaining,
                    "otp_expires_at": expires_at.isoformat(),
                    "otp_created_at": current_otp.get("created_at"),
                    "otp_source": current_otp.get("device_id"),
                    "session_id": target_session_id,
                }
        except Exception:
            pass

    return {
        "otp_active": False,
        "otp_duration_seconds": OTP_LIFETIME_SECONDS,
        "otp_expires_in": 0,
        "otp_expires_at": None,
        "otp_created_at": None,
        "otp_source": None,
        "session_id": target_session_id,
    }


def get_or_create_device_otp(device_key: str, company_id: Optional[str] = None) -> Dict[str, Any]:
    """Validates the ESP8266 device key and returns the active OTP for its associated company."""
    device_company = verify_device(device_key)
    if not device_company:
        if device_key == DEVICE_SECRET_KEY:
            device_company = DEFAULT_COMPANY_ID
        else:
            return {
                "error": "Unauthorized device key",
                "status": "unauthorized",
            }
    target_company = (company_id or device_company or DEFAULT_COMPANY_ID).strip().upper()
    return get_or_create_active_otp(source="esp8266", company_id=target_company)


def check_student_otp(code: str, session_id: str, company_id: Optional[str] = None) -> bool:
    return is_otp_valid(code, session_id, company_id or DEFAULT_COMPANY_ID)


def verify_and_consume_student_otp(code: str, session_id: str, company_id: Optional[str] = None) -> bool:
    return validate_and_consume_otp(code, session_id, company_id or DEFAULT_COMPANY_ID)
