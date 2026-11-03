# Diễn tập tự động (robot người dùng thử)

Robot đóng vai kế toán, chạy trọn một case báo cáo tháng trên máy Windows thật, rồi chấm ĐẠT/KHÔNG ĐẠT.
Mỗi bản build mới phải được robot chấm ĐẠT trước khi người thật chạy tay.

Chỉ dùng **số giả lập**. Robot không chạy nếu Excel đang mở file khác. Kết quả chỉ nằm trên máy.

## Chạy

1. Thoát Clicky đang chạy (khay hệ thống → Thoát).
2. Trong PowerShell, tại thư mục repo:

```powershell
python tools\dien_tap\rehearse.py --workbook "C:\...\Case01\BaoCao_T10.xlsx"
```

Robot tự tìm bản Clicky đã cài trong `%LOCALAPPDATA%`; muốn dùng bản khác thì thêm `--clicky "<đường dẫn Clicky.exe>"`.
Case khác: `--expected <file đáp án .json>` (thread chấm độc lập giữ file cho Case 02–04).

Kết quả nằm trong `dien-tap\<giờ>\` cạnh file Excel:
- `ket-qua.md`: bảng kiểm tra và thời gian từng bước.
- `ket-qua.json`: cùng nội dung cho máy đọc.
- `scene.jsonl`: nhật ký cảnh Clicky ghi (thẻ nào, khoanh ô nào, khung ở đâu, có đọc thành tiếng không).

Mã thoát: 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = không chạy được.

## Robot bấm bằng gì

- Mặc định thử **agent-desktop** (Vercel Labs, Apache-2.0): tải bản Windows x64 từ
  https://github.com/vercel-labs/agent-desktop/releases, giải nén, thêm vào PATH hoặc truyền `--agent-desktop <đường dẫn>`.
  Bản .exe chưa ký số; nếu antivirus công ty chặn thì bỏ qua.
- Không có agent-desktop thì tự dùng thư viện `uiautomation` Clicky đã có sẵn.

## Nhật ký cảnh

Clicky chỉ ghi nhật ký khi biến môi trường `CLICKY_SCENE_LOG` trỏ tới một file; robot tự bật khi mở Clicky.
Người dùng bình thường không bao giờ có file này.

## File đáp án

`numbers`: `{"name", "label", "value", "tolerance", "sheet"}`. Robot tìm ô có nhãn rồi lấy số đầu tiên bên phải; một tiêu đề chỉ nhắc nhãn mà không có số thì bỏ qua và tìm tiếp.
`texts`: `{"name", "text", "where": "sheets" | "said" | "any", "sheet", "must": true/false}`. Dùng để chấm cảnh báo phải có và báo động sai không được có, mà không lộ số.
