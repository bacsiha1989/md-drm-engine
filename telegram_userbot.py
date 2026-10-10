import os
import sys
import json
import ssl
import urllib.request
import asyncio
import threading
from pathlib import Path
from typing import Union
from telethon import TelegramClient
from telethon.sessions import StringSession

BASE_DIR = Path(__file__).resolve().parent
STRING_SESSION_FILE = BASE_DIR / "telegram_userbot.session_string"
TELEGRAM_LOCK = threading.Lock()

def get_credentials():
    api_id = os.environ.get("TELEGRAM_API_ID", "36124513")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "439c8b8d65b786d7ec3227281c40d5fe")
    phone = os.environ.get("TELEGRAM_USER_PHONE", "+84971521618")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "8963323266:AAG8BhFu1rrzm71KFJJzKwqbT0xMZifu_OU")
    allowed_chat_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "5904560281")

    # Nếu có file .env thì nạp thêm
    env_file = BASE_DIR.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_API_ID="):
                api_id = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_API_HASH="):
                api_hash = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_USER_PHONE="):
                phone = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_BOT_TOKEN="):
                bot_token = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_ALLOWED_USER_ID="):
                allowed_chat_id = line.split("=", 1)[1].strip()

    return api_id, api_hash, phone, bot_token, allowed_chat_id

def get_session():
    # Ưu tiên đọc từ biến môi trường
    env_session = os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
    if env_session:
        return StringSession(env_session)
    # Tiếp theo đọc từ file
    if STRING_SESSION_FILE.exists():
        content = STRING_SESSION_FILE.read_text(encoding="utf-8").strip()
        if content:
            return StringSession(content)
    return None

def is_userbot_ready() -> bool:
    api_id, api_hash, _, _, _ = get_credentials()
    session = get_session()
    return bool(api_id and api_hash and session)

def normalize_phone(phone: str) -> str:
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace(".", "")
    if cleaned.startswith("0"):
        return "+84" + cleaned[1:]
    if not cleaned.startswith("+"):
        return "+" + cleaned
    return cleaned

async def _resolve_user_entity(client, destination: Union[int, str]):
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    dest_str = str(destination).strip()

    is_phone = dest_str.startswith("0") or dest_str.startswith("+") or (dest_str.startswith("84") and len(dest_str) in [11, 12] and dest_str.isdigit())

    if not is_phone:
        if dest_str.isdigit() or (dest_str.startswith("-") and dest_str[1:].isdigit()):
            return int(dest_str)
        if dest_str.lower() in ("me", "self"):
            return "me"
        # Hỗ trợ tìm kiếm theo Username Telegram (ví dụ: @bacsi_ha hoặc bacsi_ha)
        clean_user = dest_str.lstrip("@").strip()
        if clean_user:
            try:
                entity = await client.get_input_entity(clean_user)
                return entity
            except Exception:
                pass

    norm_phone = normalize_phone(dest_str)

    try:
        me = await client.get_me()
        if me and me.phone and normalize_phone(me.phone) == norm_phone:
            return "me"
    except Exception:
        pass

    try:
        contact = InputPhoneContact(client_id=0, phone=norm_phone, first_name="BS", last_name="")
        res = await client(ImportContactsRequest([contact]))
        if res.users:
            return res.users[0]
    except Exception:
        pass

    try:
        entity = await client.get_input_entity(norm_phone)
        return entity
    except Exception:
        pass

    raise ValueError(f"Không tìm thấy tài khoản Telegram cho số điện thoại: {norm_phone} (có thể do tài khoản cài đặt chặn người lạ tìm kiếm hoặc chưa đăng ký Telegram)")

def _send_via_telegram_bot_api(bot_token: str, chat_id: Union[int, str], text: str, remove_keyboard: bool = False) -> bool:
    try:
        ssl_ctx = ssl._create_unverified_context()
        payload_data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if remove_keyboard:
            payload_data["reply_markup"] = {"remove_keyboard": True}
        payload = json.dumps(payload_data).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
            return resp.status == 200
    except Exception as exc:
        print(f"[Telegram Bot Fallback Warning] {exc}", file=sys.stderr)
        return False

def _send_bot_contact_request(bot_token: str, chat_id: Union[int, str], text: str) -> bool:
    try:
        ssl_ctx = ssl._create_unverified_context()
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "keyboard": [
                    [
                        {
                            "text": "📱 BẤM ĐỂ XÁC NHẬN SỐ ĐIỆN THOẠI & NHẬN SÁCH",
                            "request_contact": True
                        }
                    ]
                ],
                "resize_keyboard": True,
                "one_time_keyboard": True
            }
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
            return resp.status == 200
    except Exception as exc:
        print(f"[Telegram Bot Contact Request Error] {exc}", file=sys.stderr)
        return False

async def _send_otp_async(destination: Union[int, str], code: str) -> bool:
    api_id, api_hash, admin_phone, bot_token, admin_chat_id = get_credentials()
    if not api_id or not api_hash:
        raise ValueError("Chưa cấu hình TELEGRAM_API_ID hoặc TELEGRAM_API_HASH")

    session = get_session()
    if not session:
        raise ValueError("Chưa tìm thấy Telegram session hợp lệ.")

    dest_str = str(destination).strip()
    norm_dest = normalize_phone(dest_str)
    norm_admin = normalize_phone(admin_phone) if admin_phone else ""

    msg_html = (
        f"🔐 <b>MÃ XÁC MINH CỔNG TÀI LIỆU Y HỌC</b>\n\n"
        f"Số điện thoại: <code>{dest_str}</code>\n"
        f"Mã OTP xác thực Telegram của bạn là: <b>{code}</b>\n\n"
        f"<i>Mã có hiệu lực trong vòng 5 phút. Vui lòng nhập trên màn hình để hệ thống tự động mã hóa và chuyển giao toàn bộ tài liệu chuyên khảo.</i>"
    )

    # Nếu người nhận là chính Bác sĩ (trùng SĐT admin hoặc chat_id)
    # -> Bắn ngay qua Bot An (Bot API) để Telegram NỔ CHUÔNG VÀ BẬT THÔNG BÁO trên điện thoại!
    if bot_token and admin_chat_id and (norm_dest == norm_admin or dest_str in ("me", "self", admin_chat_id)):
        _send_via_telegram_bot_api(bot_token, admin_chat_id, msg_html)

    client = TelegramClient(session, int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Phiên đăng nhập Telegram Userbot chưa được xác thực.")

    try:
        target = await _resolve_user_entity(client, destination)
        await client.send_message(target, msg_html, parse_mode="html")
    finally:
        await client.disconnect()

    return True

def send_telegram_otp_by_phone(phone: str, code: str) -> bool:
    with TELEGRAM_LOCK:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(asyncio.wait_for(_send_otp_async(phone, code), timeout=25.0))
            loop.close()
            return res
        except Exception as e:
            print(f"[Userbot OTP Error] {e}", file=sys.stderr)
            raise e

async def _send_large_file_async(destination: Union[int, str], file_path: str, caption: str):
    api_id, api_hash, _, _, _ = get_credentials()
    if not api_id or not api_hash:
        raise ValueError("Chưa cấu hình TELEGRAM_API_ID hoặc TELEGRAM_API_HASH")

    session = get_session()
    if not session:
        raise ValueError("Chưa tìm thấy Telegram session hợp lệ.")

    client = TelegramClient(session, int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Phiên đăng nhập Telegram Userbot chưa được xác thực hoặc đã hết hạn.")

    try:
        target = await _resolve_user_entity(client, destination)
        abs_path = str(Path(file_path).resolve())
        await client.send_file(
            target,
            abs_path,
            caption=caption,
            parse_mode="html"
        )
    finally:
        await client.disconnect()

def send_file_via_userbot(destination: Union[int, str], file_path: str, caption: str) -> bool:
    with TELEGRAM_LOCK:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(asyncio.wait_for(_send_large_file_async(destination, file_path, caption), timeout=300.0))
            loop.close()
            return True
        except Exception as e:
            print(f"[Userbot Error] {e}", file=sys.stderr)
            raise e
