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

## Chấm bằng ảnh

Ở mỗi cảnh có ô, robot chờ 1 giây rồi tự chụp màn hình và tự hỏi Excel vị trí ô (không dùng số của Clicky). Nó chấm:

- Clicky tính vị trí ô lệch không quá 3 px so với robot tự đọc Excel;
- khung vẽ thật trên ảnh nằm đúng ô, lệch trung bình không quá 3 px;
- thẻ không che ô nào có số, và không đè lên chính vùng đang giải thích;
- mũi con trỏ chạm mép khung (cách tối đa 4 px), không đè lên con số;
- con trỏ và vòng tròn không che số ở ô khác, vòng tròn nằm trọn trong màn hình.

Ảnh từng cảnh nằm trong `anh\`, cảnh lỗi được lưu thêm `loi-canh-NN.png` có đánh dấu: xanh lá là ô đúng, đỏ là thẻ, xanh dương là con trỏ, cam là các ô bị che. Nên chạy ở 3 mức phóng: `--zoom 85`, `--zoom 100`, `--zoom 130`. `--no-visual` tắt phần này. Ảnh chụp có thể lộ cửa sổ khác trên màn hình, nên chỉ chạy khi đã đóng file công ty.

## File đáp án

`numbers`: `{"name", "label", "value", "tolerance", "sheet"}`. Robot tìm ô có nhãn rồi lấy số đầu tiên bên phải; một tiêu đề chỉ nhắc nhãn mà không có số thì bỏ qua và tìm tiếp.
`texts`: `{"name", "text", "where": "sheets" | "said" | "any", "sheet", "must": true/false}`. Dùng để chấm cảnh báo phải có và báo động sai không được có, mà không lộ số.

## 6 việc giao diện trước beta 16/11

Đặc tả: `/mnt/project-files/thiet-ke/giao-dien-5-sao/6 viec truoc beta 16-11.md`. Bước kiểm nằm trong `beta_checks.py`, chỉ chạy khi file đáp án có mục tương ứng:

| Việc | Mục trong file đáp án | Robot kiểm |
| --- | --- | --- |
| 1. Phiếu nhận file | `intake` | phiếu hiện trước cảnh đầu, đủ file, nêu dòng bị bỏ; robot so giá trị ô và mã băm file trước và lúc phiếu hiện, không có sự kiện `write` trước khi bấm, rồi bấm "Đúng rồi, làm tiếp" |
| 2. Không bỏ qua im lặng | `coverage` | mỗi file: dùng + bỏ = tổng; tổng phần bỏ liệt kê khớp |
| 3. Mở lại file và đối chiếu | `verify`; `--tamper "BC!C30=1000000"` | có sự kiện `verify`; không báo "Khớp sổ cái" khi lệch. Với `--tamper`, Clicky (móc thử `CLICKY_TEST_TAMPER`) cộng số đó vào ô ngay sau khi lưu; robot chấm đúng ô, đúng số lệch, không giao file |
| 4. Biên bản + Sơ bộ/Chính thức | `audit` | sheet "Bien ban Clicky" có các dòng cần có, nhãn SƠ BỘ, không tự chuyển CHÍNH THỨC, có `_ban-goc.xlsx` |
| 5. Vai trò từng nguồn | `roles` (`choose`) | hai nguồn cùng vai trò thì hỏi; robot bấm bản có tên trong `choose`; số từ ảnh không có trong file |
| 6. Xem trước dữ liệu rời máy | `egress` | nhật ký gửi đi không lộ tên trong danh mục, chuỗi 10/12/13 số, email; lần đầu có xem trước + duyệt; `--local-only` thì không có lần gửi nào và robot đếm 0 kết nối mạng ra ngoài của Clicky.exe (lấy mẫu mỗi giây, ghi vào ket-qua.json `meta.network`) |

Chạy: `--expected case01_beta_expected.json` (việc 1, 2, 3, 4, 6), thêm một lượt `--local-only` (số phải giống hệt), và `--expected case01_nguon_trung_expected.json` trên thư mục biến thể (việc 5). Robot chỉ báo loại lỗi lộ dữ liệu, không in giá trị bị lộ.

Clicky cần ghi vào nhật ký cảnh các sự kiện `intake`, `verify`, `status`, `roles`, `ai_call`, ghi nhật ký gửi đi `%LOCALAPPDATA%\Clicky\gui-di.jsonl`, và tôn trọng biến môi trường `CLICKY_LOCAL_ONLY=1`. Chi tiết từng trường ở đầu `beta_checks.py`.

### Việc 10: file mẫu "xem em làm"

`python rehearse.py --sample` gõ "xem em làm" (Excel phải trống), không bấm gì, rồi đọc workbook Clicky tự mở. Đáp án `mau_expected.json`: đối chiếu lại khớp, hàng trả lại 120 triệu, lợi nhuận sau thuế thực 436 triệu, còn `_ban-goc`, thẻ cuối "Xong trong N giây" lệch thời gian robot đo không quá 5 giây (hoặc 15%).

Lượt có việc 6: robot tự bấm "Gửi đi" trên thẻ xem trước lần gửi đầu. Nhật ký gửi đi mặc định ở `CLICKY_DATA_DIR` hoặc `%LOCALAPPDATA%\Clicky\gui-di.jsonl`. Với `--local-only`, `ai_call` dừng tại máy (`local_only=true`, chưa duyệt) được chấp nhận; có lần gửi thật hoặc nhật ký thêm dòng thì KHÔNG ĐẠT.
