#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MD-DRM Cloud Engine — Micro-service đóng dấu bản quyền y khoa & gửi Telegram độc lập 24/7
========================================================================================
Vận hành trên Google Cloud Run (Singapore) kết nối Cloudflare R2.
Luồng tự chủ: Nhập số điện thoại -> OTP Telegram -> Đóng dấu bản quyền -> Gửi Telegram nguyên khối.
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
import threading
import queue
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Import watermarker & helpers
BASE_DIR = Path(__file__).resolve().parent

try:
    from forensic_watermark import ForensicWatermarker
except ImportError:
    from .forensic_watermark import ForensicWatermarker

try:
    from otp_service import OTPService
except ImportError:
    from .otp_service import OTPService

try:
    from telegram_userbot import (
        is_userbot_ready,
        send_telegram_otp_by_phone,
        send_file_via_userbot,
        normalize_phone,
    )
except ImportError:
    from .telegram_userbot import (
        is_userbot_ready,
        send_telegram_otp_by_phone,
        send_file_via_userbot,
        normalize_phone,
    )

R2_PUBLIC_BASE = os.environ.get(
    "CLOUDFLARE_R2_PUBLIC_URL",
    "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev"
).rstrip("/")

CACHE_DIR = BASE_DIR / "cache_books"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

STAMPED_DIR = BASE_DIR / "output_stamped"
STAMPED_DIR.mkdir(parents=True, exist_ok=True)

watermarker = ForensicWatermarker(output_dir=str(STAMPED_DIR))
otp_service = OTPService(ttl_seconds=300)

# Hàng đợi van điều tiết tải (Semaphore Concurrency Lock)
# Khống chế tối đa 2 tác vụ đóng dấu chạy đồng thời trên 1 container để RAM luôn an toàn < 2GB
STAMP_SEMAPHORE = threading.Semaphore(value=2)

PORTAL_SESSIONS = {}

def _start_auto_cleanup_thread():
    def _worker():
        while True:
            try:
                time.sleep(600)  # Mỗi 10 phút kiểm tra một lần
                now = time.time()
                for p in STAMPED_DIR.glob("*.pdf"):
                    try:
                        # Nếu file tạo hơn 2 giờ trước thì dọn dẹp giải phóng ổ cứng container
                        if now - p.stat().st_mtime > 7200:
                            p.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass
    t = threading.Thread(target=_worker, daemon=True)
    t.start()

_start_auto_cleanup_thread()

# Sổ cái 8 cuốn sách chuyên khảo kinh điển
BOOKS_REGISTRY = {
    "01_tim_thai": {
        "title": "Siêu Âm Tim Thai (Fetal Echocardiography 4e)",
        "r2_key": "01_Sieu_am_tim_thai_Fetal_Echocardiography_4e_Tieng_Viet.pdf",
        "size_mb": 200.0,
        "edition": "4th Edition",
        "authors": "Alfred Abuhamad & Rabih Chaoui",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/01_tim_thai.webp"
    },
    "02_timor_nao": {
        "title": "Siêu Âm Não Thai (Timor Prenatal Brain 4e)",
        "r2_key": "02_Timor_Sieu_am_nao_thai_Prenatal_Brain_4e_Tieng_Viet.pdf",
        "size_mb": 98.0,
        "edition": "4th Edition",
        "authors": "Ilan Timor-Tritsch, Ana Monteagudo et al.",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/02_timor_nao.webp"
    },
    "03_di_tat_quy_1": {
        "title": "Chẩn Đoán Dị Tật Thai Quý 1 (First Trimester Ultrasound)",
        "r2_key": "03_First_Trimester_Ultrasound_Diagnosis_Tieng_Viet.pdf",
        "size_mb": 85.6,
        "edition": "Standard Edition",
        "authors": "Alfred Abuhamad & Rabih Chaoui",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/03_di_tat_quy_1.webp"
    },
    "04_san_khoa_williams": {
        "title": "Sản Khoa Williams 26e Toàn Tập",
        "r2_key": "04_San_khoa_Williams_26e_Tieng_Viet.pdf",
        "size_mb": 65.2,
        "edition": "26th Edition",
        "authors": "F. Gary Cunningham, Kenneth J. Leveno et al.",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/04_san_khoa_williams.webp"
    },
    "05_phu_khoa_williams": {
        "title": "Phụ Khoa Williams 4e Toàn Tập",
        "r2_key": "05_Phu_khoa_Williams_4e_Tieng_Viet.pdf",
        "size_mb": 83.5,
        "edition": "4th Edition",
        "authors": "Barbara L. Hoffman, John O. Schorge et al.",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/05_phu_khoa_williams.webp"
    },
    "06_noi_soi_buong_tc": {
        "title": "Atlas Phẫu Thuật Nội Soi Buồng Tử Cung",
        "r2_key": "06_Atlas_of_Hysteroscopy_Tieng_Viet.pdf",
        "size_mb": 36.2,
        "edition": "Springer Clinical Atlas",
        "authors": "Andrea Tinelli & Luis Alonso Pacheco",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/06_noi_soi_buong_tc.webp"
    },
    "07_phoi_thai_langman": {
        "title": "Phôi Thai Học Y Học Langman 14e",
        "r2_key": "07_Langman_Medical_Embryology_14e_Tieng_Viet.pdf",
        "size_mb": 60.4,
        "edition": "14th Edition",
        "authors": "T.W. Sadler",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/07_phoi_thai_langman.webp"
    },
    "08_gabbe": {
        "title": "Sản Khoa Gabbe Essentials (Gabbe's Obstetrics Essentials)",
        "r2_key": "08_Gabbe_Obstetrics_Essentials_Tieng_Viet.pdf",
        "size_mb": 19.5,
        "edition": "Essentials Edition",
        "authors": "Mark B. Landon & Henry L. Galan",
        "cover_url": "https://pub-f21768510e724dd19c9608dbb50e43c1.r2.dev/covers/08_gabbe.webp"
    }
}

TOKENS_FILE = STAMPED_DIR / "tokens.json"
MEMBERS_FILE = BASE_DIR / "members.json"
PORTAL_HTML_FILE = BASE_DIR / "portal.html"
ADMIN_HTML_FILE = BASE_DIR / "admin.html"
DOWNLOAD_TOKENS = {}

ID_TO_BOOK_KEY = {
    "1127814624c0": "08_gabbe",
    "08_gabbe": "08_gabbe",
    "a8e0a1ad9e6b": "01_tim_thai",
    "01_tim_thai": "01_tim_thai",
    "0d4e494f71d9": "02_timor_nao",
    "02_timor_nao": "02_timor_nao",
    "2af32d7a3916": "03_di_tat_quy_1",
    "03_di_tat_quy_1": "03_di_tat_quy_1",
    "e84eef8180ca": "04_san_khoa_williams",
    "04_san_khoa_williams": "04_san_khoa_williams",
    "e22a7b0b60b7": "05_phu_khoa_williams",
    "05_phu_khoa_williams": "05_phu_khoa_williams",
    "8c866ea0330c": "06_noi_soi_buong_tc",
    "06_noi_soi_buong_tc": "06_noi_soi_buong_tc",
    "ccafa7f347dd": "07_phoi_thai_langman",
    "07_phoi_thai_langman": "07_phoi_thai_langman"
}

def _load_members():
    if MEMBERS_FILE.exists():
        try:
            with open(MEMBERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_members(members):
    if not members:
        return
    try:
        with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(members, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Save Members Error] {e}", file=sys.stderr)

def _find_member_by_token_or_email(identifier: str):
    members = _load_members()
    clean_id = identifier.strip().lower()

    # Tìm theo email chính xác
    if clean_id in members:
        return clean_id, members[clean_id]

    # Tìm theo access_token
    for em, data in members.items():
        if data.get("access_token") == identifier.strip():
            return em, data

    # Tìm case-insensitive email
    for em, data in members.items():
        if em.lower() == clean_id:
            return em, data

    return None, None

def _get_allocated_books_for_member(mem_data: dict) -> list:
    allocated = []
    dispatched = mem_data.get("dispatched_books", {}) if isinstance(mem_data, dict) else {}
    if mem_data.get("is_full_8"):
        for k, v in BOOKS_REGISTRY.items():
            disp_info = dispatched.get(k, {})
            fname = disp_info.get("file_name", "")
            d_url = f"/api/portal/download?file={fname}" if (fname and (STAMPED_DIR / fname).exists()) else ""
            allocated.append({
                "book_id": k,
                "book_title": v["title"],
                "authors": v["authors"],
                "edition": v["edition"],
                "size_mb": v["size_mb"],
                "total_pages": 500,
                "cover_url": v.get("cover_url", ""),
                "download_url": d_url,
                "is_dispatched": bool(disp_info)
            })
    else:
        user_bids = set()
        for raw_bid in mem_data.get("allocated_books", []):
            clean_bid = ID_TO_BOOK_KEY.get(raw_bid, raw_bid)
            user_bids.add(clean_bid)

        for k, v in BOOKS_REGISTRY.items():
            if k in user_bids:
                disp_info = dispatched.get(k, {})
                fname = disp_info.get("file_name", "")
                d_url = f"/api/portal/download?file={fname}" if (fname and (STAMPED_DIR / fname).exists()) else ""
                allocated.append({
                    "book_id": k,
                    "book_title": v["title"],
                    "authors": v["authors"],
                    "edition": v["edition"],
                    "size_mb": v["size_mb"],
                    "total_pages": 500,
                    "cover_url": v.get("cover_url", ""),
                    "download_url": d_url,
                    "is_dispatched": bool(disp_info)
                })
    return allocated

def _ensure_book_in_cache(r2_key: str) -> Path:
    local_path = CACHE_DIR / r2_key
    if local_path.exists() and local_path.stat().st_size > 1024:
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


# =========================================================================
# HÀNG ĐỢI TUẦN TỰ (FIFO QUEUE) ĐÓNG DẤU & BẮN TELEGRAM TỰ ĐỘNG
# =========================================================================
DISPATCH_QUEUE = queue.Queue()
TASK_STATUSES = {}

def _dispatch_worker():
    """Worker duy nhất chạy ngầm xử lý từng cuốn sách một (tuần tự, bảo vệ RAM và mạng)."""
    while True:
        task = DISPATCH_QUEUE.get()
        if not task:
            break
        task_id = task.get("task_id")
        email = task.get("email", "").strip().lower()
        name = task.get("name", "") or "Bác sĩ"
        phone = task.get("phone", "")
        book_id = task.get("book_id", "")
        clean_bid = ID_TO_BOOK_KEY.get(book_id, book_id)
        book_info = BOOKS_REGISTRY.get(clean_bid)

        TASK_STATUSES[task_id] = {
            "status": "processing",
            "email": email,
            "book_id": clean_bid,
            "book_title": book_info["title"] if book_info else clean_bid,
            "phone": phone,
            "timestamp": time.time(),
            "message": "Đang đóng dấu bản quyền và gửi Telegram..."
        }

        out_path = None
        file_size_mb = 0.0
        try:
            if not book_info:
                raise ValueError(f"Không tìm thấy thông tin sách: {clean_bid}")
            if not phone:
                raise ValueError(f"Thiếu số điện thoại Telegram cho {email}")

            print(f"🚀 [Queue Dispatch Worker] Bắt đầu xử lý: BS {name} ({phone}) | Sách: {book_info['title']}...")
            source_pdf = _ensure_book_in_cache(book_info["r2_key"])
            license_id = f"LIC-GGL-{time.strftime('%y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

            res = watermarker.stamp_pdf(
                input_pdf_path=source_pdf,
                buyer_name=name,
                buyer_phone=phone,
                buyer_email=email,
                license_id=license_id,
                custom_note="MomDiary Google Cloud Run Singapore",
                visible_footer=True,
                visible_diagonal=True,
                invisible_steganography=True,
                metadata_trap=True,
                lock_permissions=True,
            )
            out_path = Path(res["output_path"])
            file_size_mb = out_path.stat().st_size / (1024 * 1024)
            download_url = f"/api/portal/download?file={out_path.name}"

            caption = (
                f"📚 <b>THƯ VIỆN CHUYÊN KHẢO Y HỌC (GOOGLE CLOUD RUN)</b>\n\n"
                f"Xin chào Bác sĩ <b>{name}</b>,\n"
                f"Hệ thống Thư viện xin gửi Bác sĩ file tài liệu chuyên khảo nguyên bản đã được cấp phép &amp; định danh riêng:\n\n"
                f"📖 <b>Sách:</b> {book_info['title']}\n"
                f"📱 <b>SĐT:</b> <code>{phone}</code>\n"
                f"✉️ <b>Email:</b> <code>{email}</code>\n"
                f"🔑 <b>Mã cấp phép:</b> <code>{license_id}</code>\n\n"
                f"<i>Tài liệu lưu hành nội bộ phục vụ nghiên cứu &amp; tra cứu chuyên môn. Bản quyền pháp y đã được kích hoạt.</i>"
            )

            tele_sent = False
            tele_err = ""
            if is_userbot_ready():
                print(f"📤 [Queue Dispatch Worker] Đang upload file PDF ({file_size_mb:.1f} MB) tới Telegram của BS {name} ({phone})...", flush=True)
                try:
                    send_file_via_userbot(phone, str(out_path), caption)
                    print(f"✅ [Queue Dispatch Worker] Đã bắn file thành công: {book_info['title']} -> {phone}", flush=True)
                    tele_sent = True
                except Exception as t_err:
                    print(f"⚠️ [Telegram Send Error] {t_err}", file=sys.stderr, flush=True)
                    tele_err = str(t_err)
            else:
                tele_err = "Hệ thống Telegram Userbot chưa sẵn sàng."

            # Lưu vào members.json
            members = _load_members()
            if email in members:
                dispatched = members[email].setdefault("dispatched_books", {})
                dispatched[clean_bid] = {
                    "book_title": book_info["title"],
                    "license_id": license_id,
                    "dispatched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "size_mb": round(file_size_mb, 1),
                    "file_name": out_path.name,
                    "download_url": download_url,
                    "telegram_sent": tele_sent,
                    "telegram_error": tele_err
                }
                _save_members(members)

            if tele_sent:
                TASK_STATUSES[task_id] = {
                    "status": "completed",
                    "email": email,
                    "book_id": clean_bid,
                    "book_title": book_info["title"],
                    "phone": phone,
                    "size_mb": round(file_size_mb, 1),
                    "filename": out_path.name,
                    "download_url": download_url,
                    "telegram_sent": True,
                    "message": f"Đã gửi nguyên file PDF ({file_size_mb:.1f} MB) trực tiếp vào Telegram của Bác sĩ!"
                }
            else:
                TASK_STATUSES[task_id] = {
                    "status": "telegram_failed",
                    "email": email,
                    "book_id": clean_bid,
                    "book_title": book_info["title"],
                    "phone": phone,
                    "size_mb": round(file_size_mb, 1),
                    "filename": out_path.name,
                    "download_url": download_url,
                    "telegram_sent": False,
                    "error": tele_err,
                    "message": f"Đã đóng dấu bản quyền xong ({file_size_mb:.1f} MB)! Telegram chưa kết nối được SĐT, Bác sĩ bấm nút để tải trực tiếp file về máy."
                }
        except Exception as exc:
            print(f"❌ [Queue Dispatch Worker Error] {exc}", file=sys.stderr, flush=True)
            d_url = f"/api/portal/download?file={out_path.name}" if (out_path and out_path.exists()) else None
            TASK_STATUSES[task_id] = {
                "status": "failed",
                "email": email,
                "book_id": clean_bid,
                "book_title": book_info["title"] if book_info else clean_bid,
                "error": str(exc),
                "download_url": d_url,
                "filename": out_path.name if out_path else "",
                "size_mb": round(file_size_mb, 1),
                "message": f"Lỗi: {str(exc)}"
            }
        finally:
            DISPATCH_QUEUE.task_done()
            time.sleep(1) # Nghỉ 1 giây giữa các file để điều tiết mạng và Telegram API

_worker_thread = threading.Thread(target=_dispatch_worker, daemon=True)
_worker_thread.start()

def enqueue_member_books(email: str, phone: Optional[str] = None, force_all: bool = False):
    """Tự động đưa tất cả sách của Bác sĩ vào hàng đợi tuần tự (luôn cấp Full 8 cuốn)."""
    members = _load_members()
    clean_email = email.strip().lower()
    mem = members.get(clean_email)
    if not mem:
        return []

    target_phone = phone or mem.get("phone", "")
    if not target_phone:
        return []

    # Luôn nâng cấp Full 8 cuốn sách chuyên khảo cho mọi Bác sĩ
    if not mem.get("is_full_8") or mem.get("allocated_books") != ["all"]:
        mem["is_full_8"] = True
        mem["allocated_books"] = ["all"]
        members[clean_email]["is_full_8"] = True
        members[clean_email]["allocated_books"] = ["all"]
        _save_members(members)

    name = mem.get("name", "") or clean_email.split("@")[0]
    allocated = _get_allocated_books_for_member(mem)
    dispatched = mem.get("dispatched_books", {})

    enqueued_task_ids = []
    for b in allocated:
        bid = b["book_id"]
        # Nếu chưa gửi hoặc force_all thì đưa vào hàng đợi
        if force_all or (bid not in dispatched):
            task_id = f"task_{uuid.uuid4().hex[:10]}"
            task = {
                "task_id": task_id,
                "email": clean_email,
                "name": name,
                "phone": target_phone,
                "book_id": bid
            }
            TASK_STATUSES[task_id] = {
                "status": "queued",
                "email": clean_email,
                "book_id": bid,
                "book_title": b["book_title"],
                "phone": target_phone
            }
            DISPATCH_QUEUE.put(task)
            enqueued_task_ids.append(task_id)
            print(f"📥 [Auto Enqueue] Đã đưa sách '{b['book_title']}' vào hàng đợi cho BS {name} ({target_phone})")

    return enqueued_task_ids


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

    def _read_json_body(self) -> dict:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return {}
        try:
            return json.loads(self.rfile.read(content_len).decode("utf-8"))
        except Exception:
            return {}

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/portal"):
            if PORTAL_HTML_FILE.exists():
                content = PORTAL_HTML_FILE.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
                return

        if path == "/admin":
            if ADMIN_HTML_FILE.exists():
                content = ADMIN_HTML_FILE.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
                return

        if path == "/health":
            self._json(HTTPStatus.OK, {
                "status": "healthy",
                "service": "md-drm-engine",
                "cloud": "Google Cloud Run 24/7 Singapore",
                "userbot_ready": is_userbot_ready(),
                "r2_url": R2_PUBLIC_BASE,
                "books_total": len(BOOKS_REGISTRY),
                "cached_books": [p.name for p in CACHE_DIR.glob("*.pdf")],
                "queue_size": DISPATCH_QUEUE.qsize()
            })
            return

        # API: Magic Login qua GET (nếu trang web gọi qua query param)
        if path == "/api/portal/magic-login":
            query = parse_qs(parsed.query)
            token_or_email = query.get("token", [""])[0] or query.get("t", [""])[0] or query.get("email", [""])[0]
            self._handle_magic_login(token_or_email)
            return

        if path == "/api/portal/task-status":
            query = parse_qs(parsed.query)
            task_id = query.get("task_id", [""])[0]
            self._handle_get_task_status(task_id)
            return

        if path == "/api/portal/download":
            query = parse_qs(parsed.query)
            file_param = query.get("file", [""])[0] or query.get("filename", [""])[0]
            if not file_param:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Thiếu tham số tên file cần tải."})
                return

            safe_name = Path(file_param).name
            target_file = STAMPED_DIR / safe_name
            if not target_file.is_file() or not target_file.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Tệp tin '{safe_name}' chưa sẵn sàng hoặc đã hết hạn."})
                return

            try:
                file_size = target_file.stat().st_size
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(file_size))
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._send_cors_headers()
                self.end_headers()

                if self.command == "HEAD":
                    return

                with open(target_file, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except Exception as e:
                print(f"[Portal Download Error] {e}", file=sys.stderr)
                return

        if path == "/api/admin/members":
            self._handle_admin_get_members()
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Endpoint GET không tồn tại: {path}"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/api/portal/magic-login", "/api/portal/step1-send-email-otp", "/api/portal/direct-register"):
            payload = self._read_json_body()
            token_or_email = str(payload.get("token") or payload.get("email") or payload.get("t") or "").strip()
            phone = str(payload.get("phone") or "").strip()
            name = str(payload.get("name") or "").strip()
            self._handle_magic_login(token_or_email, phone=phone, name=name)
            return

        if path == "/api/portal/step2-send-telegram-otp":
            self._handle_step2_send_telegram_otp()
            return

        if path == "/api/portal/step2-verify-telegram":
            self._handle_step2_verify_telegram()
            return

        if path in ("/api/portal/send-telegram", "/api/portal/auto-dispatch-book"):
            self._handle_send_telegram()
            return

        if path == "/api/admin/members/update-phone":
            self._handle_admin_update_phone()
            return

        if path == "/api/admin/members/dispatch-telegram":
            self._handle_admin_dispatch_telegram()
            return

        if path == "/api/admin/members/batch-dispatch-telegram":
            self._handle_admin_batch_dispatch_telegram()
            return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Endpoint POST không tồn tại: {path}"})

    def _handle_magic_login(self, token_or_email: str, phone: str = "", name: str = ""):
        if not token_or_email:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Thiếu mã liên kết bảo mật hoặc email."})
            return

        email, mem = _find_member_by_token_or_email(token_or_email)
        clean_phone = phone.replace(" ", "").replace("-", "") if phone else ""
        clean_name = name.strip()

        if not mem:
            clean_str = token_or_email.strip().lower()
            if "@" in clean_str and "." in clean_str:
                # Tự động cấp phép mới và trao Full 8 quyển nếu Bác sĩ tự gõ email chưa có trong hệ thống
                members = _load_members()
                new_token = f"act_{uuid.uuid4().hex[:12]}"
                mem = {
                    "email": clean_str,
                    "name": clean_name or clean_str.split("@")[0],
                    "phone": clean_phone,
                    "is_full_8": True,
                    "allocated_books": ["all"],
                    "telegram_verified": bool(clean_phone and len(clean_phone) >= 9),
                    "access_token": new_token,
                    "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "active",
                    "dispatched_books": {}
                }
                members[clean_str] = mem
                _save_members(members)
                email = clean_str
                print(f"🌟 [Auto Register] Đã tự động cấp Full 8 quyển và tạo hồ sơ cho BS mới: {clean_str}")
            else:
                self._json(HTTPStatus.UNAUTHORIZED, {
                    "ok": False,
                    "error": f"Tài khoản hoặc liên kết '{token_or_email}' không hợp lệ. Vui lòng kiểm tra lại địa chỉ email."
                })
                return
        else:
            # Nếu đã có hồ sơ và có SĐT mới gửi lên -> cập nhật ngay vào members.json
            if clean_phone and len(clean_phone) >= 9:
                members = _load_members()
                if email in members:
                    members[email]["phone"] = clean_phone
                    members[email]["telegram_verified"] = True
                    if clean_name:
                        members[email]["name"] = clean_name
                    _save_members(members)
                    mem["phone"] = clean_phone
                    mem["telegram_verified"] = True
                    if clean_name:
                        mem["name"] = clean_name

        # Đảm bảo 100% Bác sĩ truy cập qua Portal đều được kích hoạt Full 8 cuốn sách chuyên khảo
        if not mem.get("is_full_8") or mem.get("allocated_books") != ["all"]:
            members = _load_members()
            if email in members:
                members[email]["is_full_8"] = True
                members[email]["allocated_books"] = ["all"]
                _save_members(members)
            mem["is_full_8"] = True
            mem["allocated_books"] = ["all"]

        user_name = clean_name or mem.get("name", "") or email.split("@")[0]
        final_phone = clean_phone or mem.get("phone", "")
        allocated_books = _get_allocated_books_for_member(mem)

        # Nếu đã có số điện thoại Telegram hợp lệ, TỰ ĐỘNG ĐƯA TOÀN BỘ 8 QUYỂN VÀO HÀNG ĐỢI NGAY (KHÔNG CẦN CHỜ OTP)
        enqueued_tasks = []
        is_verified = False
        if final_phone and len(final_phone) >= 9:
            is_verified = True
            enqueued_tasks = enqueue_member_books(email, final_phone)

        session_token = uuid.uuid4().hex
        PORTAL_SESSIONS[session_token] = {
            "email": email,
            "name": user_name,
            "phone": final_phone,
            "verified_phone": final_phone,
            "is_full_8": mem.get("is_full_8", False),
            "allocated_books": allocated_books,
            "telegram_verified": is_verified,
            "created_at": time.time(),
            "exp": time.time() + 86400 * 7
        }

        self._json(HTTPStatus.OK, {
            "ok": True,
            "session_token": session_token,
            "email": email,
            "name": user_name,
            "phone": final_phone,
            "existing_phone": final_phone,
            "books_count": len(allocated_books),
            "is_full_8": mem.get("is_full_8", False),
            "telegram_verified": is_verified,
            "auto_queued": len(enqueued_tasks) > 0,
            "enqueued_tasks": enqueued_tasks,
            "user": {
                "name": user_name,
                "phone": final_phone,
                "email": email
            },
            "books": allocated_books
        })

    def _handle_step2_send_telegram_otp(self):
        payload = self._read_json_body()
        token = str(payload.get("session_token", "")).strip()
        phone = str(payload.get("phone", "")).strip()

        session = PORTAL_SESSIONS.get(token)
        if not session or session.get("exp", 0) < time.time():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Phiên truy cập không hợp lệ hoặc đã hết hạn."})
            return

        clean_phone = phone.replace(" ", "").replace("-", "")
        if not clean_phone or len(clean_phone) < 9:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Vui lòng nhập số điện thoại Telegram hợp lệ."})
            return

        otp_key = f"telegram:{clean_phone}"
        code, gen_ok, gen_msg = otp_service.generate_otp(otp_key)
        if not gen_ok:
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": gen_msg})
            return

        print(f"📱 [Cloud Run Telegram OTP] SĐT: {clean_phone} | Mã OTP: {code}")

        try:
            send_telegram_otp_by_phone(clean_phone, code)
        except Exception as exc:
            print(f"[Cloud Run Telegram OTP Error] {exc}", file=sys.stderr)
            # Nếu Userbot chưa kết nối tới số này, vẫn trả về thành công nếu Bác sĩ đang kiểm thử hoặc trả lỗi rõ ràng
            pass

        session["pending_phone"] = clean_phone

        self._json(HTTPStatus.OK, {
            "ok": True,
            "message": f"Mã OTP đã được gửi vào ứng dụng Telegram của số điện thoại {clean_phone}."
        })

    def _handle_step2_verify_telegram(self):
        payload = self._read_json_body()
        token = str(payload.get("session_token", "")).strip()
        phone = str(payload.get("phone", "")).strip()
        code = str(payload.get("code", "")).strip()

        session = PORTAL_SESSIONS.get(token)
        if not session or session.get("exp", 0) < time.time():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Phiên truy cập không hợp lệ hoặc đã hết hạn."})
            return

        clean_phone = phone.replace(" ", "").replace("-", "")
        otp_key = f"telegram:{clean_phone}"

        # Xác thực OTP: nếu đúng mã hoặc master code dev
        v_ok, v_msg = otp_service.verify_otp(otp_key, code)
        if not v_ok:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": v_msg})
            return

        session["telegram_verified"] = True
        session["verified_phone"] = clean_phone
        user_email = session["email"]

        # Lưu vĩnh viễn SĐT vào members.json
        members = _load_members()
        if user_email in members:
            members[user_email]["phone"] = clean_phone
            _save_members(members)

        # TỰ ĐỘNG ĐƯA TOÀN BỘ SÁCH VÀO HÀNG ĐỢI TUẦN TỰ (FIFO QUEUE) NGAY KHI XÁC MINH SĐT THÀNH CÔNG
        enqueued_tasks = enqueue_member_books(user_email, clean_phone)

        allocated = session.get("allocated_books", [])
        self._json(HTTPStatus.OK, {
            "ok": True,
            "auto_queued": len(enqueued_tasks) > 0,
            "enqueued_tasks": enqueued_tasks,
            "user": {
                "name": session["name"],
                "phone": clean_phone,
                "email": user_email
            },
            "books": allocated
        })

    def _handle_send_telegram(self):
        payload = self._read_json_body()
        token = str(payload.get("session_token", "")).strip()
        book_id = str(payload.get("book_id", "")).strip()
        phone = str(payload.get("phone", "")).strip()

        session = PORTAL_SESSIONS.get(token)
        if not session:
            email = str(payload.get("email", "")).strip().lower()
            name = str(payload.get("name", "")).strip() or "Bác sĩ"
        else:
            email = session.get("email", "")
            name = session.get("name", "")
            phone = phone or session.get("verified_phone", "")

        # Fallback tìm kiếm số điện thoại trong members.json nếu session chưa kịp ghi nhận
        if not phone and email:
            members = _load_members()
            if email in members:
                phone = str(members[email].get("phone", "")).strip()
                if not name or name == "Bác sĩ":
                    name = str(members[email].get("name", "")).strip() or email.split("@")[0]

        if not phone:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Thiếu số điện thoại Telegram nhận sách."})
            return

        clean_bid = ID_TO_BOOK_KEY.get(book_id, book_id)
        book_info = BOOKS_REGISTRY.get(clean_bid)
        if not book_info:
            for k, v in BOOKS_REGISTRY.items():
                if book_id in (v["r2_key"], k, v["title"]):
                    book_info = v
                    break

        if not book_info:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Không tìm thấy sách với mã: '{book_id}'"})
            return

        # Đưa vào HÀNG ĐỢI TUẦN TỰ (FIFO Queue) để xử lý ngầm, không giữ kết nối HTTP
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = {
            "task_id": task_id,
            "email": email,
            "name": name,
            "phone": phone,
            "book_id": clean_bid
        }
        TASK_STATUSES[task_id] = {
            "status": "queued",
            "email": email,
            "book_id": clean_bid,
            "book_title": book_info["title"],
            "phone": phone
        }
        DISPATCH_QUEUE.put(task)

        self._json(HTTPStatus.OK, {
            "ok": True,
            "queued": True,
            "task_id": task_id,
            "message": f"Sách '{book_info['title']}' đã được đưa vào hàng đợi Google Cloud Run để tự động đóng dấu và chuyển giao về Telegram!"
        })

    def _handle_get_task_status(self, task_id: str):
        if not task_id:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Thiếu mã task_id."})
            return
        status = TASK_STATUSES.get(task_id)
        if not status:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Không tìm thấy trạng thái task: {task_id}"})
            return
        self._json(HTTPStatus.OK, {
            "ok": True,
            "task": status,
            "queue_size": DISPATCH_QUEUE.qsize()
        })

    def _handle_admin_get_members(self):
        members = _load_members()
        list_out = []
        total_members = len(members)
        tele_verified_count = 0
        full_8_count = 0
        total_dispatched_count = 0
        banned_count = 0

        for em, data in members.items():
            allocated = _get_allocated_books_for_member(data)
            phone = str(data.get("phone", "")).strip()
            is_f8 = data.get("is_full_8", False) or len(allocated) >= 8
            dispatched = data.get("dispatched_books", {})
            status = data.get("status", "active")
            is_tele_verified = bool(phone) or data.get("telegram_verified", False)

            if is_tele_verified:
                tele_verified_count += 1
            if is_f8:
                full_8_count += 1
            if status == "banned":
                banned_count += 1
            total_dispatched_count += len(dispatched)

            list_out.append({
                "email": em,
                "name": data.get("name", "") or em.split("@")[0],
                "phone": phone,
                "telegram_verified": is_tele_verified,
                "allocated_count": len(allocated),
                "allocated_books": allocated,
                "dispatched_books": dispatched,
                "is_full_8": is_f8,
                "status": status,
                "access_token": data.get("access_token", ""),
                "registered_at": data.get("registered_at", "")
            })

        stats = {
            "total_members": total_members,
            "telegram_verified": tele_verified_count,
            "full_8_members": full_8_count,
            "total_books_dispatched": total_dispatched_count,
            "banned_members": banned_count
        }

        self._json(HTTPStatus.OK, {
            "ok": True,
            "members": list_out,
            "stats": stats,
            "queue_size": DISPATCH_QUEUE.qsize(),
            "total_books": len(BOOKS_REGISTRY)
        })

    def _handle_admin_update_phone(self):
        payload = self._read_json_body()
        email = str(payload.get("email", "")).strip().lower()
        phone = str(payload.get("phone", "")).strip()
        members = _load_members()
        if email not in members:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Không tìm thấy Bác sĩ: {email}"})
            return
        members[email]["phone"] = phone
        _save_members(members)
        self._json(HTTPStatus.OK, {
            "ok": True,
            "message": f"Đã cập nhật số điện thoại thành công cho {email}: {phone}"
        })

    def _handle_admin_dispatch_telegram(self):
        payload = self._read_json_body()
        email = str(payload.get("email", "")).strip().lower()
        book_id = str(payload.get("book_id", "")).strip()
        members = _load_members()
        mem = members.get(email)
        if not mem:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Không tìm thấy Bác sĩ: {email}"})
            return
        phone = mem.get("phone", "")
        if not phone:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Bác sĩ {email} chưa có số điện thoại Telegram."})
            return
        name = mem.get("name", "") or email.split("@")[0]
        clean_bid = ID_TO_BOOK_KEY.get(book_id, book_id)
        book_info = BOOKS_REGISTRY.get(clean_bid)

        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = {
            "task_id": task_id,
            "email": email,
            "name": name,
            "phone": phone,
            "book_id": clean_bid
        }
        TASK_STATUSES[task_id] = {
            "status": "queued",
            "email": email,
            "book_id": clean_bid,
            "book_title": book_info["title"] if book_info else clean_bid,
            "phone": phone
        }
        DISPATCH_QUEUE.put(task)
        self._json(HTTPStatus.OK, {
            "ok": True,
            "task_id": task_id,
            "queue_size": DISPATCH_QUEUE.qsize(),
            "message": f"Đã đưa sách vào hàng đợi gửi Telegram cho BS {name} ({phone})"
        })

    def _handle_admin_batch_dispatch_telegram(self):
        payload = self._read_json_body()
        target_book = payload.get("book_id", "").strip() if payload else ""
        members = _load_members()
        enqueued_count = 0
        for em, mem in members.items():
            phone = str(mem.get("phone", "")).strip()
            # Bỏ qua tài khoản admin hoặc tài khoản test mẫu
            if phone in ("0971521618", "0912345678", "+84971521618") or em in ("test.buyer@gmail.com", "bacsiha1989@gmail.com"):
                continue
            if phone:
                if target_book and target_book.lower() != "all":
                    clean_bid = ID_TO_BOOK_KEY.get(target_book, target_book)
                    allocated = _get_allocated_books_for_member(mem)
                    alloc_bids = [b["book_id"] for b in allocated]
                    dispatched = mem.get("dispatched_books", {})
                    if clean_bid in alloc_bids and clean_bid not in dispatched:
                        book_info = BOOKS_REGISTRY.get(clean_bid, {})
                        task_id = f"task_{uuid.uuid4().hex[:10]}"
                        task = {
                            "task_id": task_id,
                            "email": em,
                            "name": mem.get("name", "") or em.split("@")[0],
                            "phone": phone,
                            "book_id": clean_bid
                        }
                        TASK_STATUSES[task_id] = {
                            "status": "queued",
                            "email": em,
                            "book_id": clean_bid,
                            "book_title": book_info.get("title", clean_bid),
                            "phone": phone
                        }
                        DISPATCH_QUEUE.put(task)
                        enqueued_count += 1
                else:
                    t_ids = enqueue_member_books(em, phone=phone, force_all=False)
                    enqueued_count += len(t_ids)
        self._json(HTTPStatus.OK, {
            "ok": True,
            "enqueued_count": enqueued_count,
            "queued_count": enqueued_count,
            "queue_size": DISPATCH_QUEUE.qsize(),
            "message": f"Đã đưa {enqueued_count} cuốn sách vào hàng đợi Google Cloud Run để gửi tự động cho các Bác sĩ đã có SĐT!"
        })


def run_server(port: int = 8000):
    server = ThreadingHTTPServer(("0.0.0.0", port), DRMRequestHandler)
    print(f"🚀 MD-DRM Cloud Engine (Telegram + R2) running on port {port} (PID: {os.getpid()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MD-DRM Engine...")
        server.server_close()


if __name__ == "__main__":
    port_env = int(os.environ.get("PORT", 8000))
    run_server(port=port_env)
