import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from .config import DEVICE_SECRET_KEY, OTP_LIFETIME_SECONDS
from .database import (
    verify_device,
    save_new_otp,
    get_current_valid_otp,
    get_active_session,
    validate_and_consume_otp,
)


def generate_numeric_otp(digits: int = 6) -> str:
    """Generate cryptographically randomized numeric OTP string."""
    lower = 10 ** (digits - 1)
    upper = (10 ** digits) - 1
    return str(secrets.randbelow(upper - lower + 1) + lower)


def get_or_create_active_otp(source: str = "api") -> Dict[str, Any]:
    """
    Returns the current active OTP for the active session, or generates a fresh
    rotating OTP if none exists or it is about to expire.
    """
    active_session = get_active_session()
    session_id = active_session["session_id"] if active_session else "GLOBAL"

    now = datetime.now(timezone.utc)
    current_otp = get_current_valid_otp(session_id)

    if current_otp:
        try:
            expires_at = datetime.fromisoformat(current_otp["expires_at"])
            remaining_seconds = int((expires_at - now).total_seconds())
            if remaining_seconds > 5:
                return {
                    "otp": current_otp["code"],
                    "expires_in": remaining_seconds,
                    "expiry": current_otp["expires_at"],
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
    )

    print(f"[OTP] New OTP generated for {session_id}: {code} (valid {OTP_LIFETIME_SECONDS}s, source={source})")

    return {
        "otp": code,
        "expires_in": OTP_LIFETIME_SECONDS,
        "expiry": expires_iso,
        "session_id": session_id,
        "status": "new",
    }


def get_or_create_device_otp(device_key: str) -> Dict[str, Any]:
    """Validates the ESP32 device key and returns the shared active OTP."""
    is_valid_key = verify_device(device_key) or (device_key == DEVICE_SECRET_KEY)
    if not is_valid_key:
        return {
            "error": "Unauthorized device key",
            "status": "unauthorized",
        }
    return get_or_create_active_otp(source="esp32")


def verify_student_otp(code: str, session_id: str) -> bool:
    return validate_and_consume_otp(code, session_id)
