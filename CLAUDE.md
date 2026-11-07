# Clicky BI AI: bản đồ cho AI làm việc trên repo này

Clicky BI AI là trợ lý trên màn hình Windows dành cho phòng tài chính và FP&A Việt Nam. Từ file của khách, nó làm ra báo cáo quản trị tháng với số đúng tuyệt đối và có thể bảo vệ trước Ban Giám đốc. Làm Excel trước, Power Query sau, PPT và Power BI để sau nữa.
Repo này fork từ clicky-windows (Python, PyQt6, giấy phép MIT). Phần BI nằm trong `engine/`, `office/` và `tools/dien_tap/`. Các phần còn lại (overlay, giọng nói, trỏ chuột, AI provider) là của Clicky gốc.

## Luật không được phá

1. **Số do code tính, AI không bịa số.** `engine/` dùng `Decimal` cho tiền, không dùng float. AI chỉ ghép nhãn, đặt câu hỏi và viết lời bình. Mọi con số trong báo cáo phải truy ngược được về tài khoản và dòng nguồn.
2. **Chỉ "Số chính thức" mới được đưa số vào báo cáo** (`engine/roles.py`). File bán hàng và ảnh chụp chỉ dùng để giải thích và đối chiếu.
3. **Không bao giờ ghi đè file gốc của khách.** Luôn ghi ra bản mới, giữ `_ban-goc.xlsx`. Mọi thay đổi phải ghi vào sheet "Bien ban Clicky" (`engine/audit.py`).
4. **Không bao giờ bỏ qua trong im lặng.** Mỗi file phải có số dòng dùng + số dòng bỏ = tổng số dòng, và phải nêu lý do bỏ (`engine/intake.py`). Kết quả "chưa rõ" phải hiện ra cho người dùng thấy.
5. **"Xong" nghĩa là đã mở lại file vừa lưu và đối chiếu khớp** (`engine/saved_check.py`). AI tự nói "đã làm xong" thì chưa tính là xong. Báo cáo ở trạng thái SƠ BỘ cho đến khi người dùng chuyển sang CHÍNH THỨC.
6. **Mọi lần gọi AI đều đi qua một cửa duy nhất:** `office/outgoing.py`. Ở đó dữ liệu được che (`engine/redact.py`), người dùng xem trước, ghi vào `gui-di.jsonl`, và tôn trọng `CLICKY_LOCAL_ONLY=1`. Không được gọi provider vòng qua cửa này.
7. **Người dùng không bao giờ thấy API key, console hay trang thanh toán.** Chi phí AI đã gói qua proxy của mình và mã kích hoạt.
8. **Mỗi màn hình chỉ một ý, không đổ chữ.** Thẻ không được che ô có số. Con trỏ nằm dưới khung, không có quầng sáng. Lời nói và chữ trên thẻ là tiếng Việt đời thường, xưng "em", gọi "chị".
9. **Tiếng Việt không bao giờ được đọc bằng giọng tiếng Anh.** Giọng Edge online chỉ bật cho số giả lập, và phải trả `tts_provider` về "auto" trước khi dùng file thật.
10. **Giữ các lệnh `scene_log.record(...)`** trong `office/month_close.py` (`play`, `_locate`) và ở mọi thẻ hay nút mới. Robot diễn tập chấm dựa trên nhật ký này.

## Bản đồ thư mục

| Đường dẫn | Việc |
| --- | --- |
| `engine/` | Lõi tính toán thuần Python, không cần Excel. Gồm `trial_balance` (đọc bảng cân đối tài khoản), `monthly_report` (báo cáo lãi lỗ và chênh lệch), `checks` (bẫy), `report_sheet` (công thức SUMIFS sống), `build` (một lệnh ra cả báo cáo), `cli` (chạy không cần Excel). |
| `office/` | Cầu nối Excel qua COM (`excel_com`), Power Query (`power_query`, `pq_skill`), chốt tháng bằng một câu lệnh giọng nói (`month_close`), file mẫu "xem em làm" (`sample`), chỗ thả file (`drop`), cửa gửi đi (`outgoing`). |
| `scene_log.py` | Ghi mỗi thứ Clicky hiện, vẽ hay nói thành một dòng JSON khi `CLICKY_SCENE_LOG` bật. |
| `tools/dien_tap/` | Robot đóng vai kế toán và chấm ĐẠT/KHÔNG ĐẠT. Đọc `tools/dien_tap/README.md` trước khi sửa. |
| `addin/` | Bản thử Office.js (để sau, chưa phải hướng chính). |
| `ui/`, `ai/`, `audio/`, `screen/` | Phần của Clicky gốc: overlay, panel chat, provider, giọng nói, chụp màn hình. |

## Kiểm tra trước khi đẩy code

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
node tests/browser_extension.test.js
```

CI (`.github/workflows/tests.yml`) chạy cùng các lệnh này trên Windows. Bản build đóng gói phải qua `Clicky.exe --smoke-test` (xem `TESTING.md`, `BUILD.md`). Mỗi lỗi đã sửa phải có một test giữ lại trong `tests/`.

## Quy trình sau mỗi bản build

1. Robot diễn tập trước (`/dien-tap`, xem `.claude/skills/dien-tap/SKILL.md`). Case 01 phải đạt `--lan 3`.
2. Sau đó người thật mới chạy tay trên máy test.
3. Không cài bản mới lên máy test trong lúc luồng chấm độc lập đang chạy các case kín.

## Quyền riêng tư khi làm việc

- Chỉ dùng **số giả lập** (Case 01 Sông Hồng và các case trong `tests/`). Máy test của người dùng hay mở file công ty thật. Không đọc, chụp hay chép các file đó. Robot từ chối chạy khi Excel đang mở file khác.
- **Không mở đáp án kín** của luồng chấm độc lập (`cham-doc-lap/`, các file `*_khoa.zip`, `Dap_an_kin*`). Đọc đáp án kín thì bài chấm mất giá trị. `.claude/settings.json` đã chặn các đường dẫn này.
- Video và ảnh chụp chỉ dùng nội bộ.

## Cách viết

- Code, docstring và commit viết bằng tiếng Anh, câu ngắn, nói việc và lý do.
- Chữ người dùng thấy (thẻ, nút, lỗi trong `engine/errors_vi.py`) và tài liệu cho người dùng viết bằng tiếng Việt có dấu, dễ hiểu với người không chuyên kỹ thuật.
- Nhánh theo chồng PR: `bi-ai-report` → `bi-ai-pq` → `bi-ai-month-close` → `engine-blind-case-fixes` → robot → `beta-ui`. Hãy hỏi nên đặt nhánh mới lên nhánh nào trước khi mở PR.
