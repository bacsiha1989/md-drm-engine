#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MD-DRM Cloud Engine — Micro-service đóng dấu bản quyền y khoa độc lập 24/7
========================================================================
Sử dụng chuẩn thư viện chuẩn Python (ThreadingHTTPServer) + PyMuPDF.
Chạy siêu nhẹ, khởi động tức thì, tương thích 100% Docker / Render / Railway / Mac.
"""

import os
import sys
import json
import time
import uuid
import re
import shutil
import unicodedata
import urllib.request
import urllib.parse
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Import watermarker
try:
    from forensic_watermark import ForensicWatermarker
except ImportError:
    from .forensic_watermark import ForensicWatermarker

R2_PUBLIC_BASE = os.environ.get(
    "CLOUDFLARE_R2_PUBLIC_URL",
    "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev"
).rstrip("/")

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache_books"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STAMPED_DIR = BASE_DIR / "output_stamped"
STAMPED_DIR.mkdir(parents=True, exist_ok=True)

watermarker = ForensicWatermarker(output_dir=str(STAMPED_DIR))

# Sổ cái 8 cuốn sách chuyên khảo
BOOKS_REGISTRY = {
    "01_tim_thai": {
        "title": "Siêu Âm Tim Thai (Fetal Echocardiography 4e)",
        "r2_key": "01_Sieu_am_tim_thai_Fetal_Echocardiography_4e_Tieng_Viet.pdf",
        "size_mb": 200.0,
        "edition": "4th Edition",
        "authors": "Alfred Abuhamad & Rabih Chaoui"
    },
    "02_timor_nao": {
        "title": "Siêu Âm Não Thai (Timor Prenatal Brain 4e)",
        "r2_key": "02_Timor_Sieu_am_nao_thai_Prenatal_Brain_4e_Tieng_Viet.pdf",
        "size_mb": 98.0,
        "edition": "4th Edition",
        "authors": "Ilan Timor-Tritsch, Ana Monteagudo et al."
    },
    "03_di_tat_quy_1": {
        "title": "Chẩn Đoán Dị Tật Thai Quý 1 (First Trimester Ultrasound)",
        "r2_key": "03_First_Trimester_Ultrasound_Diagnosis_Tieng_Viet.pdf",
        "size_mb": 85.6,
        "edition": "Standard Edition",
        "authors": "Alfred Abuhamad & Rabih Chaoui"
    },
    "04_san_khoa_williams": {
        "title": "Sản Khoa Williams 26e Toàn Tập",
        "r2_key": "04_San_khoa_Williams_26e_Tieng_Viet.pdf",
        "size_mb": 65.2,
        "edition": "26th Edition",
        "authors": "F. Gary Cunningham, Kenneth J. Leveno et al."
    },
    "05_phu_khoa_williams": {
        "title": "Phụ Khoa Williams 4e Toàn Tập",
        "r2_key": "05_Phu_khoa_Williams_4e_Tieng_Viet.pdf",
        "size_mb": 83.5,
        "edition": "4th Edition",
        "authors": "Barbara L. Hoffman, John O. Schorge et al."
    },
    "06_noi_soi_buong_tc": {
        "title": "Atlas Phẫu Thuật Nội Soi Buồng Tử Cung",
        "r2_key": "06_Atlas_of_Hysteroscopy_Tieng_Viet.pdf",
        "size_mb": 36.2,
        "edition": "Springer Clinical Atlas",
        "authors": "Andrea Tinelli & Luis Alonso Pacheco"
    },
    "07_phoi_thai_langman": {
        "title": "Phôi Thai Học Y Học Langman 14e",
        "r2_key": "07_Langman_Medical_Embryology_14e_Tieng_Viet.pdf",
        "size_mb": 60.4,
        "edition": "14th Edition",
        "authors": "T.W. Sadler"
    },
    "08_gabbe": {
        "title": "Sản Khoa Gabbe Essentials (Gabbe's Obstetrics Essentials)",
        "r2_key": "08_Gabbe_Obstetrics_Essentials_Tieng_Viet.pdf",
        "size_mb": 19.5,
        "edition": "Essentials Edition",
        "authors": "Mark B. Landon & Henry L. Galan"
    }
}

TOKENS_FILE = STAMPED_DIR / "tokens.json"
DOWNLOAD_TOKENS = {}

def _load_tokens():
    global DOWNLOAD_TOKENS
    if TOKENS_FILE.exists():
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                DOWNLOAD_TOKENS.update(saved)
        except Exception:
            pass
    return DOWNLOAD_TOKENS

def _save_token(token_id: str, data: dict):
    tokens = _load_tokens()
    tokens[token_id] = data
    try:
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _ensure_book_in_cache(r2_key: str) -> Path:
    local_path = CACHE_DIR / r2_key
    if local_path.exists() and local_path.stat().st_size > 1024:
        return local_path

    # Kiểm tra xem có file sẵn ở thư mục gốc không (khi chạy local)
    local_root_dir = Path("/Users/phamngocha/Build AI/Minh Translate/Sach_Tieng_Viet_8_quyen_2026-10-07")
    if local_root_dir.exists():
        for f in local_root_dir.glob("*.pdf"):
            norm_key = r2_key.replace("_", "").lower()
            norm_name = f.name.replace("_", "").replace("-", "").replace(" ", "").lower()
            if r2_key[:2] in f.name:
                shutil.copyfile(f, local_path)
                print(f"📦 [Local Cache Link] Đã sao chép từ local: {local_path.name}")
                return local_path

    r2_url = f"{R2_PUBLIC_BASE}/{r2_key}"
    print(f"📥 [R2 Fetch] Đang kéo sách gốc từ Cloudflare R2: {r2_url}...")
    req = urllib.request.Request(
        r2_url,
        headers={"User-Agent": "Mozilla/5.0 (MD-DRM-Cloud-Engine/2.0)"}
    )
    temp_target = local_path.with_suffix(".tmp")
    with urllib.request.urlopen(req, timeout=300) as resp, open(temp_target, "wb") as f_out:
        shutil.copyfileobj(resp, f_out)
    temp_target.replace(local_path)
    print(f"✅ [R2 Fetch] Đã lưu sách vào cache: {local_path.name} ({local_path.stat().st_size / (1024*1024):.1f} MB)")
    return local_path


class DRMRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/health"):
            self._json(HTTPStatus.OK, {
                "status": "healthy",
                "service": "md-drm-engine",
                "cloud": "24/7 independent",
                "r2_url": R2_PUBLIC_BASE,
                "books_total": len(BOOKS_REGISTRY),
                "cached_books": [p.name for p in CACHE_DIR.glob("*.pdf")]
            })
            return

        if path == "/books":
            self._json(HTTPStatus.OK, {
                "ok": True,
                "books": [
                    {
                        "id": k,
                        "title": v["title"],
                        "edition": v["edition"],
                        "authors": v["authors"],
                        "size_mb": v["size_mb"],
                        "r2_key": v["r2_key"],
                        "r2_url": f"{R2_PUBLIC_BASE}/{v['r2_key']}"
                    }
                    for k, v in BOOKS_REGISTRY.items()
                ]
            })
            return

        if path.startswith("/api/drm/download/"):
            token = path.split("/api/drm/download/")[-1].strip("/")
            tokens = _load_tokens()
            item = tokens.get(token)
            if not item or item.get("exp", 0) < time.time():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Đường link tải sách đã hết hạn hoặc không tồn tại (hiệu lực 24 giờ)."})
                return

            file_path = Path(item["path"])
            if not file_path.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File sách không còn trên máy chủ."})
                return

            file_size = file_path.stat().st_size
            filename = item["filename"]
            norm_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
            ascii_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', norm_name)
            encoded_filename = urllib.parse.quote(filename, encoding='utf-8')

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}')
            self.send_header("Cache-Control", "private, max-age=3600")
            self._send_cors_headers()
            self.end_headers()

            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Endpoint không tồn tại: {path}"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/api/drm/stamp", "/api/drm/generate-stamped-link"):
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len == 0:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Thiếu dữ liệu JSON trong body."})
                return

            try:
                body_bytes = self.rfile.read(content_len)
                payload = json.loads(body_bytes.decode("utf-8"))
            except Exception as e:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"JSON không hợp lệ: {str(e)}"})
                return

            book_id = str(payload.get("book_id", "")).strip()
            buyer_name = str(payload.get("buyer_name", "")).strip() or "Bác sĩ"
            buyer_phone = str(payload.get("buyer_phone", "")).strip()
            buyer_email = str(payload.get("buyer_email", "")).strip() or "doctor@momdiary.vn"
            license_id = str(payload.get("license_id", "")).strip()
            custom_note = str(payload.get("custom_note", "")).strip()

            book_info = BOOKS_REGISTRY.get(book_id)
            if not book_info:
                for k, v in BOOKS_REGISTRY.items():
                    if book_id in (v["r2_key"], k, v["title"]):
                        book_info = v
                        break

            if not book_info:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Không tìm thấy sách với ID: '{book_id}'"})
                return

            t0 = time.time()
            try:
                source_pdf = _ensure_book_in_cache(book_info["r2_key"])
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"Lỗi kéo sách từ Cloudflare R2: {str(exc)}"})
                return

            try:
                stamp_res = watermarker.stamp_pdf(
                    input_pdf_path=source_pdf,
                    buyer_name=buyer_name,
                    buyer_phone=buyer_phone,
                    buyer_email=buyer_email,
                    license_id=license_id,
                    custom_note=custom_note,
                    visible_footer=True,
                    visible_diagonal=True,
                    invisible_steganography=True,
                    metadata_trap=True,
                    lock_permissions=True,
                )
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"Lỗi đóng dấu PyMuPDF: {str(exc)}"})
                return

            dt = time.time() - t0
            token = uuid.uuid4().hex
            out_file = Path(stamp_res["output_path"])
            token_data = {
                "path": str(out_file),
                "filename": out_file.name,
                "exp": time.time() + 86400  # 24 giờ
            }
            _save_token(token, token_data)

            self._json(HTTPStatus.OK, {
                "ok": True,
                "book_title": book_info["title"],
                "buyer_name": stamp_res["buyer_name"],
                "buyer_phone": stamp_res["buyer_phone"],
                "license_id": stamp_res["license_id"],
                "total_pages": stamp_res["total_pages"],
                "file_size_mb": round(stamp_res["file_size_mb"], 1),
                "execution_time_sec": round(dt, 2),
                "download_token": token,
                "download_url": f"/api/drm/download/{token}",
                "filename": out_file.name
            })
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Endpoint POST không tồn tại: {path}"})


def run_server(port: int = 8000):
    server = ThreadingHTTPServer(("0.0.0.0", port), DRMRequestHandler)
    print(f"🚀 MD-DRM Cloud Engine running on http://0.0.0.0:{port} (PID: {os.getpid()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MD-DRM Engine...")
        server.server_close()


if __name__ == "__main__":
    port_env = int(os.environ.get("PORT", 8000))
    run_server(port=port_env)
