---
name: dien-tap
description: Chuẩn bị và chấm một lượt diễn tập robot cho bản build Clicky mới (Case 01, beta checks, nhiều lượt) trước khi người thật chạy tay. Dùng sau mỗi bản build hoặc khi được nhờ "cho robot chạy".
---
Bản build cần diễn tập: $ARGUMENTS

Robot chạy trên máy Windows thật của người dùng. Phiên AI trên cloud không chạy được Excel. Việc của skill này là chuẩn bị đúng lệnh, đọc kết quả và kết luận có cho người thật chạy tay hay không.

1. Đọc `tools/dien_tap/README.md` và các file đáp án trong `tools/dien_tap/*.json`. Không mở đáp án kín của luồng chấm độc lập.
2. Trước khi chạy, nhắc người dùng ba việc: thoát Clicky đang chạy, **đóng hết file công ty trong Excel**, chỉ dùng thư mục Case 01 (số giả lập).
3. Đưa người dùng đúng các lệnh sau, theo thứ tự, mỗi lệnh một dòng để dán vào PowerShell:
   - `python tools\dien_tap\rehearse.py --workbook "<Case01>\BaoCao_T10.xlsx" --lan 3 --zoom 100 --out "<Case01>\dien-tap\lan-<build>"`
   - mỗi mức `--zoom 85` và `--zoom 130`, chạy một lượt
   - `--expected tools\dien_tap\case01_beta_expected.json` (các việc 1, 2, 3, 4, 6), thêm một lượt có `--local-only` (số phải giống hệt)
   - `--tamper "BC!C30=1000000"` (Clicky phải bắt được đúng ô và đúng số lệch, và không giao file)
   - `--sample` (file mẫu "xem em làm")
4. Đọc `tong-hop.md` và `ket-qua.md` của từng lượt. Kết luận:
   - **Cho chạy tay** khi mọi lượt chặn đều ĐẠT.
   - **Chưa cho** khi có bất kỳ lượt KHÔNG ĐẠT nào. Nêu tên kiểm tra hỏng, cảnh số mấy và ảnh `loi-canh-NN.png`.
   - Mã 2 nghĩa là robot không chạy được, đó là lỗi của robot, chưa phải lỗi của Clicky.
5. Trả về: build đã chấm, bảng lượt (đạt/không đạt, thời gian), các kiểm tra hỏng, kết luận và một việc tiếp theo cụ thể.
