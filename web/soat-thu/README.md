# Clicky soát thử (trang web, file không rời máy)

Một link, không cài đặt: chị kế toán kéo 2 file bảng cân đối tài khoản (tháng này, tháng trước) vào trang,
khoảng 1 phút sau có phiếu soát (một ý mỗi màn hình) và tải về báo cáo tháng Excel có công thức.

- Bộ máy soát là `engine/` của app desktop, chạy trong trình duyệt bằng Pyodide (Python biên dịch sang WebAssembly).
  Cùng code, cùng kết quả: Case 01 mẫu ra 556 → 436 triệu, file Excel tải về giống hệt bản chạy trên máy.
- File không được gửi đi: trang chỉ được phép tải file của chính nó (`connect-src 'self'` trong CSP, ở thẻ meta và `_headers`).
  Pyodide được tự host trong `pyodide/`, không lấy từ CDN. Nạp xong có thể ngắt mạng mà vẫn soát được.
- Không AI, không tài khoản, không key. Mọi câu chữ là mẫu câu điền số do code tính.
- Chỉ có phần tìm lỗi. Sửa giúp, biên bản, Sơ bộ/Chính thức nằm trong app desktop.

## Build và chạy thử

```
python tools/web/build_soat_thu.py            # → dist/soat-thu/ (~14 MB, tải Pyodide 0.27.7 từ npm registry một lần)
python -m http.server 8765 -d dist/soat-thu   # mở http://localhost:8765
```

Host: thư mục `dist/soat-thu/` là trang tĩnh. Cloudflare Pages đọc `_headers` (CSP áp cả cho worker);
GitHub Pages không đọc `_headers`, khi đó chỉ còn CSP ở thẻ meta của trang.

Nút cuối trang (tải app, nhắn đặt buổi làm cùng nhóm): điền `CONFIG.installUrl` / `CONFIG.contactUrl` đầu `app.js`.
Để trống thì trang chỉ ghi "nhắn lại cho người đã gửi link".
