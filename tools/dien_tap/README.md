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

### Chạy nhiều lượt: `--lan N`

Khách chạy chốt tháng một lần là phải đúng lần đó, nên một lượt đạt may mắn chưa đủ. `--lan 5` chạy cùng case 5 lượt liền và chỉ ĐẠT khi **cả 5** lượt đều đạt. Giữa hai lượt, robot tự thoát Clicky và đóng (không lưu) các file mà lượt trước đã mở. Lượt cuối thì để nguyên cho người xem.
Có `--out` thì mỗi lượt nằm trong `lan-1\`, `lan-2\`…, và bảng tổng hợp nằm ở `tong-hop.md`.
Mốc trước khi người thật chạy tay: Case 01 phải đạt `--lan 3` ở mức phóng 100, và đạt một lượt ở mỗi mức 85 và 130.

Giữa các lượt, robot ghi tình trạng Excel vào `excel-giua-cac-luot.json` ở thư mục `--out`: số tiến trình, bộ nhớ (MB), số handle, số file đang mở, số bản sao của robot và số kết nối và query trong các bản sao đó. Robot chỉ đếm, không in tên file. Ở mỗi lượt nói, robot ghi bộ nhớ và handle của Excel vào `excel-moi-luot-noi.json` trong thư mục của lượt. Phần này không gọi COM, để không làm chậm Clicky. `--excel-moi-luot` thoát hẳn Excel giữa các lượt, như khi người dùng mở máy làm tháng mới. Robot chỉ thoát Excel khi trước lượt 1 không có file nào mở.

Mỗi lượt chạy, robot ghi nguồn điện của máy vào đầu `ket-qua.md` và `meta.power` trong `ket-qua.json`: cắm sạc hay chạy pin, % pin, chế độ nguồn, và tốc độ CPU. Robot đọc nguồn điện trước và sau lượt chạy, và đọc tốc độ CPU mỗi 5 giây trong suốt lượt chạy. Ở chế độ Balanced, CPU lúc rảnh cố ý chạy chậm, nên robot lấy mẫu nhanh nhất. Nếu máy chạy pin, hoặc mẫu CPU nhanh nhất vẫn dưới 90% tốc độ tối đa, các dòng chấm thời gian vẫn hiện nhưng có chữ "KHÔNG TÍNH" và không làm lượt chạy đạt hay trượt. Lý do: tối 07/11, máy DN-LAP-231 chạy pin còn 11%, CPU 67%, và mọi lượt chậm gấp 2–3 lần trên cả hai bản build.

Robot cũng đo, ở mỗi lượt, quãng lâu nhất không có gì mới hiện ra, tính từ lúc bắt đầu lượt tới cảnh hay câu hỏi đầu tiên. Chữ báo từng chặng (`close_step` `caption`, có từ #52), thẻ, nét vẽ và con trỏ đều tính là có hiện ra. Mục tiêu là dưới 2 giây. Dòng này hiện giờ chỉ để đo; đặt `"silent_blocking": true` trong `layout.latency` thì nó thành dòng chặn. `"silent_max_ms"` đổi ngưỡng.

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

## Diễn tập 4 chặng (bhc_e2e)

Một lệnh chạy trọn một tháng trên case giả lập kiểu công ty đường (không phải số thật của công ty nào):

```powershell
python tools\dien_tap\bhc_e2e.py                 # tự tạo case có lỗi cài sẵn
python tools\dien_tap\bhc_e2e.py --sach          # bản sạch: bộ BOD không có lỗi
python tools\dien_tap\bhc_e2e.py --chang 3,4     # chỉ chạy vài chặng
```

| Chặng | Robot làm | ĐẠT khi |
| --- | --- | --- |
| 1. Thả thư mục | Mở `Clicky.exe --drop <thư mục>`, như khi chị thả cả thư mục vào ô thả file | Phiếu nhận file hiện trước khi sửa gì; mỗi file có dòng dùng + dòng bỏ = tổng; mở đúng `BaoCao_QuanTri_T10.xlsx` giữa nhiều file |
| 2. Chốt số | Bấm "Đúng rồi, làm tiếp", "Sửa giúp tôi" nếu có, "Lưu và kiểm tra" | Lợi nhuận sau thuế đúng `dap_an.json`; mở lại file đã lưu thấy khớp; còn bản `_ban-goc`; file nguồn không đổi; không có lỗi |
| 3. Soát bộ BOD | Chạy `bod_robot.py --dap-an` trên cùng thư mục | Bắt đủ lỗi cài sẵn đúng ô đúng số, báo động sai không quá 1, đủ 7 nhóm |
| 4. Thuyết trình + hỏi đáp | Chạy `present_robot.py` trên `trinh_bay/` | Kiểm tra trước đạt, chỉ đúng hình, trả lời đúng câu |

Trước 4 chặng, robot chạy chặng 0 (khoảng 30 giây): `pq_smoke.py` mở một Excel riêng, tải một query nhỏ qua
đúng đường làm mới của Clicky, rồi thử một query chậm để chắc Clicky dừng chờ đúng hạn. Chạy riêng được:
`python tools\dien_tap\pq_smoke.py`.

Đối chiếu query đọc từng file (khoảng 2 phút): `python tools\dien_tap\m_agree.py`. Robot ghi 8 file đơn hàng giả
lập vào thư mục tạm (.xls thật, HTML lưu thành .xls hai kiểu, SpreadsheetML, CSV tiếng Anh có dòng tiêu đề, ô ngày
có giờ trong .xlsx lưu tên .xls, CSV ngày có giờ dạng chữ, file POS có tên cột lạ mà chị đã chỉ cột ngày và số tiền),
cho Excel tải từng file bằng đúng query Clicky dựng, rồi so từng dòng với phiếu nhận file. ĐẠT khi cả 8 dạng khớp số
dòng, tổng tiền và từng dòng. Cột chị đã chỉ chỉ được nhớ trong lúc chạy, không ghi vào dữ liệu Clicky trên máy. Script từ chối chạy khi Excel/PowerPoint đang mở file khác, chỉ làm trong một
workbook nó tự thêm và hiện rõ, không lưu, đóng khi xong, không in đường dẫn thư mục nào.

Kết quả: `dien-tap-4-chang-<giờ>\ket-qua-4-chang.md` và một thư mục con cho mỗi chặng. Không cần Excel,
có thể tự kiểm phần tính của cả 4 chặng: `python tools\dien_tap\bhc_case.py --tao <thư mục> --kiem`.

## File đáp án

`numbers`: `{"name", "label", "value", "tolerance", "sheet"}`. Robot tìm ô có nhãn rồi lấy số đầu tiên bên phải; một tiêu đề chỉ nhắc nhãn mà không có số thì bỏ qua và tìm tiếp.
`texts`: `{"name", "text", "where": "sheets" | "said" | "any", "sheet", "must": true/false}`. Dùng để chấm cảnh báo phải có và báo động sai không được có, mà không lộ số.

Hai dòng chặn chạy cho mọi case, kể cả case kín, không cần đáp án:

- **Không chặng nào bị đứng quá giờ.**
- **Thư mục có file dữ liệu thì Clicky phải tới phiếu nhận file.** Trước khi mở Clicky, robot đếm file `.xlsx/.xlsm/.xlsb/.xls/.csv` cạnh file Excel của case. Robot chỉ đếm tên, không mở file nào, và không tính chính file đó, bản `_ban-goc`, file khoá `~$` hay bản sao `_dientap_` của robot. Nếu có ít nhất 1 file mà nhật ký không có sự kiện `intake`, lượt chạy KHÔNG ĐẠT. Chi tiết ghi số cảnh và câu cuối Clicky nói. Case cố ý cho Clicky dừng trước phiếu thì đặt `"stop_before_receipt_ok": true` trong file đáp án.

### Case không có `final_title` (các case kín)

Robot chờ tới khi Clicky làm xong việc mà cú bấm cuối cùng giao. Dấu hiệu là một `story_end` đến sau cú bấm hay lệnh cuối, và thẻ cuối của nó không hỏi gì mà robot sắp bấm. Sau đó robot chờ thêm 6 giây không có sự kiện mới. Thời gian chờ tối đa là `second_stage_seconds`. Robot không coi một quãng im lặng là dấu hiệu xong: ở Case 05 bản ebffea4, Clicky ghi sheet gần 50 giây mà không có sự kiện nào. Nếu Clicky đề nghị "Lưu và kiểm tra", robot bấm, chờ sự kiện `verify`, rồi chấm dòng chặn "Clicky lưu, mở lại file và đối chiếu khớp". Nếu Clicky không đề nghị lưu, robot chỉ ghi một dòng thông tin. Muốn tắt bước lưu thì đặt `"save": false`. Case 01 có `final_title`, nên chỉ lưu khi đáp án có `verify`.

### Câu hỏi tên cột lạ (`columns`)

Khi một file giống file đơn hàng mà tên cột lạ, Clicky hỏi cột nào là ngày bán, cột nào là số tiền, trước khi hiện phiếu nhận file. Nhật ký ghi `close_step` `column_question` (file, need, options), sau đó là `column_answer`. Robot chỉ trả lời theo mục `columns` trong file đáp án:

```json
"columns": [{"file": "Shopee_T10.csv", "date": "Order date", "amount": "Total"},
            {"file": "ghi_chu.csv", "not_orders": true}]
```

- Câu hỏi "Đúng không chị?": robot bấm "Đúng, dùng file này" chỉ khi Clicky đoán đúng cả hai cột.
- Câu hỏi chọn cột: robot bấm "Cột <tên>" theo đáp án.
- Nếu đáp án không có mục cho file đó, hoặc nút đúng không có trên thẻ, robot không bấm gì và lượt chạy KHÔNG ĐẠT, kèm lý do. Robot không bao giờ đoán.
- Nếu Clicky không hỏi về một file có trong `columns`, robot ghi một dòng không chặn.

Clicky nhớ câu trả lời trong `cot_da_nho.json` ở thư mục dữ liệu. Robot cất file này sang `cot_da_nho.json.truoc-dien-tap` trước mỗi lượt và trả lại sau lượt, nên lượt nào cũng bắt đầu khi chưa nhớ gì và câu trả lời của người thử máy vẫn còn.

### Thẻ "bớt được bao lâu"

Sau khi báo cáo trên file của khách đã lưu, mở lại và khớp, Clicky hiện một thẻ riêng hỏi "bớt được bao lâu". Robot bấm "Để sau" để thẻ đóng mà không ghi câu trả lời nào (`first_feedback.py`). Báo cáo có ba dòng:

- "Hỏi \"bớt được bao lâu\" sau báo cáo đã lưu và khớp": chỉ ghi nhận, không chặn.
- "Thẻ góp ý đóng bằng \"Để sau\"": chặn. Thẻ hiện mà không đóng được là lỗi.
- Với `--sample`: "Bản mẫu không hỏi \"bớt được bao lâu\"": chặn. Bản mẫu không bao giờ được hỏi.

Clicky đếm số lần đã hỏi trong `settings.json` (`feedback_asked`, `feedback_done`, tối đa ba lần). Robot xoá hai khoá này trước mỗi lượt để thẻ luôn đến hạn, rồi trả lại đúng giá trị của người thử máy sau lượt. Các khoá khác trong `settings.json` giữ nguyên. Robot chờ thẻ đóng tối đa `feedback_seconds` giây (mặc định 45).

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

### Diễn tập trình bày (Trợ lý bên cạnh, PowerPoint thật)

Chỉ dùng **bộ giả lập** (`dien_tap/tao_gia_lap.py` trong repo riêng của khách sinh ra file Excel và deck 20 slide có chữ "SỐ GIẢ LẬP"). Kịch bản `kich_ban.yaml`, `hoi_dap.yaml`, `kiem_tra_truoc.yaml` nằm trong thư mục riêng của khách, không nằm trong repo này.

```bat
python tools\dien_tap\present_robot.py --spec <thu_muc_kich_ban> --xlsx <gia_lap.xlsx> --deck <gia_lap.pptx>
```

Robot làm như người trình bày:

1. Từ chối chạy khi PowerPoint hoặc Excel đang mở file khác.
2. Chép file Excel và deck vào thư mục kết quả. File gốc không bị đụng.
3. Bật "Cho phép quay màn hình Clicky" trong lúc chạy để ảnh chụp thấy khung, chạy xong trả về như cũ.
4. Mở Clicky với `--present-*`, `CLICKY_SCENE_LOG` và `CLICKY_LOCAL_ONLY=1`.
5. Bấm PageDown qua từng slide. Sau mỗi slide, robot chụp mọi màn hình và tìm khung xanh quanh từng hình kịch bản chỉ vào.
6. Với từng câu mẫu trong ngân hàng câu hỏi: bấm Ctrl+Alt+H, gõ vào ô "Câu hỏi", bấm "Hỏi", đọc câu Clicky chọn, rồi bấm Ctrl+Alt+B.
7. Thoát trình chiếu, kiểm tra biên bản và mã băm của bản sao Excel.

**Cổng ĐẠT:**

- Kiểm tra trước đạt.
- Theo đủ mọi slide và chỉ đúng mọi hình: khung có trong nhật ký cảnh và thấy được trên ảnh chụp.
- Mỗi câu mẫu vào đúng câu của nó.
- Ctrl+Alt+B về đúng slide trước khi hỏi.
- Với 2 màn hình: ô hỏi không bao giờ nằm trên màn chiếu.
- File Excel không đổi.

Máy chỉ có 1 màn hình thì robot bật `CLICKY_PRESENT_FORCE_PANEL=1` để vẫn tập được hỏi đáp. Buổi thật thì cần 2 màn hình ở chế độ Mở rộng (Extend).

Kết quả: `ket-qua.md`, `ket-qua.json`, `scene.jsonl`, `anh/slide-NN.png`. Mã thoát: 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = không chạy được.

Clicky ghi các sự kiện sau vào nhật ký cảnh: `present_preflight`, `present_start`, `present_slide` (gồm `targets`, `rects` theo pixel thật, `missed`, `missing`), `present_frames`, `present_key`, `present_answer`, `present_back`, `present_end`.

### Diễn tập soát bộ BOD (Case 07 lớp 2)

Chỉ dùng **số giả lập**. Case của người chấm độc lập do thread chấm giải nén ra thư mục; robot không mở file đáp án.

```bat
:: 1. Tự kiểm trước: case tự tạo có 9 lỗi biết trước (phải ĐẠT)
python tools\dien_tap\bod_robot.py --tu-tao
:: 2. Case thật của người chấm (thư mục đã giải nén)
python tools\dien_tap\bod_robot.py --thu-muc <thu_muc_case_07>
```

Robot làm:

1. Từ chối chạy khi Excel hoặc PowerPoint đang mở file khác.
2. Chép thư mục case vào thư mục kết quả (`case\`). Thư mục gốc không bị đụng.
3. Bật "Cho phép quay màn hình Clicky" trong lúc chạy, chạy xong trả về như cũ.
4. Mở Clicky với `--bod-review <case>`, `CLICKY_SCENE_LOG` và `CLICKY_LOCAL_ONLY=1`.
5. Mỗi lần Clicky sáng một ô sai trên bộ BOD, robot chụp mọi màn hình (`anh\loi-NN.png`).
6. Đóng bộ BOD trong Excel không lưu, kiểm mã băm của bộ BOD.

**Cổng ĐẠT:** Clicky soát xong và ghi kết quả; mở lại file kết quả thấy khớp; mỗi lỗi một thẻ chỉ vào ô; mọi thẻ nằm đúng ô trên màn hình; bộ BOD không bị sửa. Với `--tu-tao` thêm: bắt đủ 9/9 lỗi đúng ô đúng số, báo động sai không quá 1.

Kết quả cho người chấm: `ket-qua-robot.md`, `ket-qua-robot.json`, `scene.jsonl`, `anh\loi-NN.png`, và trong `case\Clicky_soat_BOD\`: `ket-qua.md` của Clicky, file `Clicky_soat_BOD_<kỳ>.xlsx` (sheet "Ket qua soat" và "Bien ban Clicky"). Mã thoát: 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = không chạy được.

Không cần bản cài vẫn chạy được phần tính: `python -m engine.bod_check <thu_muc_case>`.

Clicky ghi các sự kiện: `bod_start` (`folder` là mã thư mục, không phải tên), `bod_receipt` (mọi file, vai trò, dòng dùng, dòng bỏ), `bod_finding` (nhóm, mã kiểm, ô, số đúng, số bộ BOD ghi), `bod_done` (số lỗi, đường dẫn kết quả, `verified`), cùng `beat`, `locate`, `spoken` như lúc chốt tháng.

`show_book` và `close_book` mang `book` (8 ký tự hex của sha1 đường dẫn, không bao giờ là tên file) và `pack` (đúng khi là bộ BOD). Dòng kiểm "File nguồn mở để chỉ ô đã đóng, không lưu" đếm theo từng file: bộ BOD được phép để mở, mọi file khác đã `show_book` phải có `close_book` cùng `book`, và không lần đóng nào có `saved` đúng. Bản cài chưa ghi `close_book` hoặc `book` thì dòng này hiện CHƯA KIỂM ĐƯỢC, không tính là đạt.
