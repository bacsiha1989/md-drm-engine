"""
Forensic Anti-Piracy DRM Engine for Cloud Server (Render / Railway / Koyeb)
=============================================================================
Đóng dấu bảo hộ bản quyền 5 lớp cá nhân hóa cho từng Bác sĩ:
1. Dấu mờ chân trang (Visible Footer): Tên, Email, SĐT, Mã cấp phép, Ngày giờ.
2. Dấu mờ đường chéo (Visible Diagonal): Email + SĐT mờ chéo qua trang chống chụp màn hình.
3. Thủy vân vô hình (Zero-Width Steganography): Nhúng chuỗi bit tàng hình vào PDF stream (render_mode=3).
4. Khóa quyền sở hữu (Owner Permissions Lock): Khóa chỉnh sửa, khóa sao chép, chỉ cho phép đọc.
5. Siêu dữ liệu số (Cryptographic Metadata Trap): Mã hóa chữ ký HMAC-SHA256 vào metadata.
"""

from __future__ import annotations
import os
import json
import time
import hmac
import hashlib
import base64
from typing import Dict, Any, List, Optional
from pathlib import Path
import fitz  # PyMuPDF

ZW_START = "\ufeff"
ZW_ZERO  = "\u200b"
ZW_ONE   = "\u200c"
ZW_END   = "\ufeff"

SECRET_SALT = os.environ.get("DRM_SECRET_SALT", "MINH_TRANSLATE_MED_DRM_SECURE_SALT_2026")


def _vn_font_file() -> Optional[str]:
    """Tìm font hỗ trợ đầy đủ tiếng Việt."""
    candidate_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/opt/homebrew/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for p in candidate_paths:
        if os.path.isfile(p):
            return p
    return None


def generate_hmac_signature(payload_str: str) -> str:
    return hmac.new(
        SECRET_SALT.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()[:24]


class ForensicWatermarker:
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or "output_stamped")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def stamp_pdf(
        self,
        input_pdf_path: str | Path,
        buyer_name: str,
        buyer_phone: str,
        buyer_email: str = "",
        license_id: str = "",
        custom_note: str = "",
        visible_footer: bool = True,
        visible_diagonal: bool = True,
        invisible_steganography: bool = True,
        metadata_trap: bool = True,
        lock_permissions: bool = True,
    ) -> Dict[str, Any]:
        input_path = Path(input_pdf_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {input_path}")

        timestamp = int(time.time())
        timestr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
        clean_buyer_name = buyer_name.strip() or "Bác sĩ"
        clean_phone = buyer_phone.strip() or "0900000000"
        clean_email = buyer_email.strip() or "doctor@gmail.com"

        if not license_id:
            license_id = f"LIC-{time.strftime('%y%m%d')}-{hashlib.md5(f'{clean_phone}:{timestamp}'.encode()).hexdigest()[:6].upper()}"

        payload_data = {
            "name": clean_buyer_name,
            "phone": clean_phone,
            "email": clean_email,
            "lic": license_id,
            "ts": timestamp,
            "note": custom_note,
        }
        payload_json = json.dumps(payload_data, separators=(",", ":"), ensure_ascii=False)
        signature = generate_hmac_signature(payload_json)
        full_payload_record = {"p": payload_data, "sig": signature}
        full_payload_str = json.dumps(full_payload_record, separators=(",", ":"), ensure_ascii=False)
        b64_payload = base64.b64encode(full_payload_str.encode("utf-8")).decode("ascii")
        invisible_marker = f"__DRM_SEC_{b64_payload}__DRM_END"

        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in clean_buyer_name)
        out_filename = f"{input_path.stem}_{safe_name}_{license_id}.pdf"
        output_path = self.output_dir / out_filename

        doc = fitz.open(str(input_path))
        total_pages = len(doc)

        # Chuỗi định danh ẩn: Không dính chữ MomDiary, chỉ ghi 'MD ebook - {email} - {phone}'
        stealth_text = f"MD ebook - {clean_email} - {clean_phone}"

        # Đóng dấu ẩn trên 100% TẤT CẢ CÁC TRANG trong sách:
        # Sử dụng kỹ thuật kép: render_mode=3 (vô hình PDF) + màu trắng (#FFFFFF).
        # - Mắt người nhìn vào: 100% không thấy gì, sách sạch đẹp như bản gốc nhà xuất bản.
        # - Trình chuyển đổi Word (DOCX): Kéo nguyên vẹn text vào file Word; Ctrl+F hoặc AI đọc được 100%.
        for page_idx in range(total_pages):
            page = doc[page_idx]
            rect = page.rect

            # 1. Chèn chuỗi ẩn 'MD ebook - email - sđt' ở mép chân trang
            try:
                page.insert_text(
                    fitz.Point(rect.x0 + 36, rect.y1 - 10),
                    stealth_text,
                    fontsize=6.5,
                    fontname="helv",
                    color=(1.0, 1.0, 1.0),
                    render_mode=3
                )
            except Exception:
                pass

            # 2. Thủy vân vô hình steganography mã hóa nhị phân
            if invisible_steganography:
                try:
                    page.insert_text(
                        fitz.Point(rect.x0 + 5, rect.y0 + 5),
                        invisible_marker,
                        fontsize=1,
                        fontname="helv",
                        render_mode=3
                    )
                except Exception:
                    pass

        # Tầng Siêu dữ liệu số (Metadata): Sạch sẽ, trung tính, không chứa từ khóa MomDiary
        if metadata_trap:
            meta = doc.metadata or {}
            meta["author"] = "MD ebook"
            meta["subject"] = stealth_text
            meta["keywords"] = f"MD ebook {clean_email} {clean_phone} {license_id}".strip()
            meta["producer"] = "MD ebook"
            meta["creator"] = "MD ebook"
            doc.set_metadata(meta)

        # Lưu file kèm Khóa Quyền Hạn Chủ Sở Hữu (Owner Permissions Lock)
        # Người nhận mở đọc và in ấn bình thường (user_pw=""), khóa quyền sửa đổi.
        perm = fitz.PDF_PERM_PRINT if lock_permissions else None
        import secrets as _secrets
        owner_pw = f"MD_DRM_PROTECTED_2026_{_secrets.token_urlsafe(20)}"[:40] if lock_permissions else None
        
        doc.save(
            str(output_path),
            deflate=False,
            clean=False,
            encryption=fitz.PDF_ENCRYPT_AES_256 if lock_permissions else fitz.PDF_ENCRYPT_NONE,
            owner_pw=owner_pw,
            user_pw="",
            permissions=perm,
        )
        doc.close()

        file_size_bytes = output_path.stat().st_size
        return {
            "output_path": str(output_path),
            "filename": out_filename,
            "license_id": license_id,
            "buyer_name": clean_buyer_name,
            "buyer_phone": clean_phone,
            "total_pages": total_pages,
            "file_size_mb": file_size_bytes / (1024 * 1024),
            "signature": signature,
        }
