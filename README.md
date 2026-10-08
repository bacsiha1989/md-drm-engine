# MD DRM Engine - Cloud Watermarking Micro-Service

Micro-service Python đám mây tự động lấy sách từ Cloudflare R2, đóng dấu bản quyền pháp y 5 lớp (Tên Bác sĩ, SĐT, Email, Timestamp, Mã kiểm tra SHA-256) theo thời gian thực và cấp link tải an toàn.

## Tính năng chính
- Tích hợp 100% Cloudflare R2 Bucket `md-books`
- Đóng dấu tốc độ cao bằng PyMuPDF C-extension
- REST API chuẩn:
  - `GET /health`: Kiểm tra trạng thái máy chủ
  - `POST /api/generate-stamped-link`: Yêu cầu đóng dấu & nhận token
  - `GET /api/download/{token}`: Tải sách PDF có dấu bản quyền
- Triển khai 1-Click trên Render.com hoặc Railway.app
