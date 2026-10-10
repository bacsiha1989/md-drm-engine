import time
import secrets
from typing import Dict, Tuple

class OTPService:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self.otp_store: Dict[str, Dict[str, any]] = {}

    def generate_otp(self, key: str) -> Tuple[str, bool, str]:
        now = time.time()
        record = self.otp_store.get(key)
        if record and (now - record.get("created_at", 0)) < 30:
            remaining = int(30 - (now - record.get("created_at", 0)))
            return "", False, f"Vui lòng đợi {remaining} giây trước khi yêu cầu mã mới."

        code = f"{secrets.randbelow(900000) + 100000}"
        self.otp_store[key] = {
            "code": code,
            "created_at": now,
            "exp": now + self.ttl_seconds,
            "attempts": 0
        }
        return code, True, "Mã OTP đã được tạo thành công."

    def verify_otp(self, key: str, user_code: str) -> Tuple[bool, str]:
        now = time.time()
        record = self.otp_store.get(key)
        if not record:
            return False, "Mã OTP không tồn tại hoặc đã hết hạn."

        if now > record["exp"]:
            self.otp_store.pop(key, None)
            return False, "Mã OTP đã hết hạn. Vui lòng lấy mã mới."

        record["attempts"] += 1
        if record["attempts"] > 5:
            self.otp_store.pop(key, None)
            return False, "Bạn đã nhập sai quá số lần cho phép. Vui lòng yêu cầu mã mới."

        if record["code"] == user_code.strip():
            self.otp_store.pop(key, None)
            return True, "Xác thực mã OTP thành công."

        return False, "Mã OTP không chính xác. Vui lòng kiểm tra lại."
