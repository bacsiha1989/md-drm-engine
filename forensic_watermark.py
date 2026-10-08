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
        clean_email = buyer_email.strip() or "doctor@momdiary.vn"

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

        footer_text = (
            f"Bản quyền cấp cho: {clean_buyer_name} | {clean_email} | ĐT: {clean_phone} | "
            f"Mã: {license_id} | Ngày: {timestr.split()[0]} | Nghiêm cấm sao chép, phát tán, mua bán"
        )
        if custom_note:
            footer_text += f" ({custom_note})"

        diag_line1 = " · ".join(x for x in (clean_email, clean_phone) if x) or clean_buyer_name
        diag_line2 = f"{license_id} · Cấm sao chép, mua bán"

        font_file = _vn_font_file()
        vn_font = fitz.Font(fontfile=font_file) if font_file else fitz.Font("helv")
        fname = "vnstamp" if font_file else "helv"

        for page_idx in range(total_pages):
            page = doc[page_idx]
            rect = page.rect
            if font_file:
                page.insert_font(fontname=fname, fontfile=font_file)

            # Tầng 1: Footer
            if visible_footer:
                footer_rect = fitz.Rect(rect.x0 + 24, rect.y1 - 11, rect.x1 - 24, rect.y1 - 1)
                try:
                    page.insert_textbox(
                        footer_rect,
                        footer_text,
                        fontsize=5.5,
                        fontname=fname,
                        color=(0.45, 0.45, 0.45),
                        align=fitz.TEXT_ALIGN_CENTER
                    )
                except Exception:
                    page.insert_text(
                        fitz.Point(rect.x0 + 36, rect.y1 - 15),
                        footer_text[:90],
                        fontsize=7,
                        color=(0.45, 0.45, 0.45)
                    )

            # Tầng 1B: Dấu chéo mờ
            if visible_diagonal:
                diag = rect.width * 0.9
                fs = max(10.0, min(26.0, diag / max(1.0, vn_font.text_length(diag_line1, fontsize=1))))
                fs2 = fs * 0.6
                for cy in (rect.height * 0.33, rect.height * 0.70):
                    center = fitz.Point(rect.width / 2, cy)
                    w1 = vn_font.text_length(diag_line1, fontsize=fs)
                    w2 = vn_font.text_length(diag_line2, fontsize=fs2)
                    mat = fitz.Matrix(-30)
                    page.insert_text(fitz.Point(center.x - w1 / 2, center.y), diag_line1, fontsize=fs, fontname=fname,
                                     color=(0.55, 0.55, 0.55), fill_opacity=0.18, morph=(center, mat))
                    page.insert_text(fitz.Point(center.x - w2 / 2, center.y + fs * 1.1), diag_line2, fontsize=fs2,
                                     fontname=fname, color=(0.55, 0.55, 0.55), fill_opacity=0.18, morph=(center, mat))

            # Tầng 2: Steganography
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

        # Tầng 3: Metadata
        if metadata_trap:
            meta = doc.metadata or {}
            trap_tag = f"DRM:{license_id}:{signature}"
            meta["keywords"] = f"{meta.get('keywords', '')} {trap_tag}".strip()
            meta["author"] = clean_buyer_name
            meta["subject"] = f"Bản quyền: {clean_buyer_name} | SĐT: {clean_phone} | Lic: {license_id}"
            meta["producer"] = f"MinhTranslate AI DRM Engine v2.0 [Lic:{license_id}]"
            doc.set_metadata(meta)

        # Lưu file
        perm = (
            fitz.PDF_PERM_PRINT
            | fitz.PDF_PERM_PRINT_HQ
            | fitz.PDF_PERM_ACCESSIBILITY
        )
        owner_pw = f"OWNER_KEY_{license_id}_{signature[:12]}"
        
        doc.save(
            str(output_path),
            deflate=True,
            clean=True,
            encryption=fitz.PDF_ENCRYPT_AES_256 if lock_permissions else fitz.PDF_ENCRYPT_NONE,
            owner_pw=owner_pw if lock_permissions else None,
            user_pw="",
            permissions=perm if lock_permissions else None,
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
