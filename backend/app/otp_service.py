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


def generate_numeric_otp(digits: int = 6) -> str:
    """Generate cryptographically randomized numeric OTP string."""
    lower = 10 ** (digits - 1)
    upper = (10 ** digits) - 1
    return str(secrets.randbelow(upper - lower + 1) + lower)


def get_or_create_active_otp(source: str = "esp32", company_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns the current active OTP for the company's active session, or generates a fresh
    rotating 60-second OTP if none exists or it is about to expire.
    """
    comp_id = (company_id or DEFAULT_COMPANY_ID).strip()
    active_session = get_active_session(comp_id)
    session_id = active_session["session_id"] if active_session else "GLOBAL"

    now = datetime.now(timezone.utc)
    current_otp = get_current_valid_otp(session_id, comp_id)

    if current_otp:
        try:
            expires_at = datetime.fromisoformat(current_otp["expires_at"])
            remaining_seconds = int((expires_at - now).total_seconds())
            if remaining_seconds > 5:
                return {
                    "otp": current_otp["code"],
                    "expires_in": remaining_seconds,
                    "expiry": current_otp["expires_at"],
                    "company_id": comp_id,
                    "session_id": session_id,
                    "status": "active",
                }
        except Exception:
            pass

    code = generate_numeric_otp(6)
    expires_at_dt = now + timedelta(seconds=OTP_LIFETIME_SECONDS)
    created_iso = now.isoformat()
    expires_iso = expires_at_dt.isoformat()

    save_new_otp(
        code=code,
        session_id=session_id,
        device_id=source,
        created_at=created_iso,
        expires_at=expires_iso,
        company_id=comp_id,
    )

    print(f"[OTP] New OTP for {comp_id}/{session_id}: {code} (valid {OTP_LIFETIME_SECONDS}s, source={source})")

    return {
        "otp": code,
        "expires_in": OTP_LIFETIME_SECONDS,
        "expiry": expires_iso,
        "company_id": comp_id,
        "session_id": session_id,
        "status": "new",
    }


def get_or_create_device_otp(device_key: str, company_id: Optional[str] = None) -> Dict[str, Any]:
    """Validates the ESP32 device key and returns the active OTP for its associated company."""
    device_company = verify_device(device_key)
    if not device_company:
        if device_key == DEVICE_SECRET_KEY:
            device_company = DEFAULT_COMPANY_ID
        else:
            return {
                "error": "Unauthorized device key",
                "status": "unauthorized",
            }
    target_company = (company_id or device_company or DEFAULT_COMPANY_ID).strip()
    return get_or_create_active_otp(source="esp32", company_id=target_company)


def check_student_otp(code: str, session_id: str, company_id: Optional[str] = None) -> bool:
    return is_otp_valid(code, session_id, company_id or DEFAULT_COMPANY_ID)


def verify_and_consume_student_otp(code: str, session_id: str, company_id: Optional[str] = None) -> bool:
    return validate_and_consume_otp(code, session_id, company_id or DEFAULT_COMPANY_ID)
