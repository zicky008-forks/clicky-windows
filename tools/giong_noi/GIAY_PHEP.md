# Giấy phép các model giọng nói (kiểm ngày 06/11/2026)

Câu hỏi: có được đóng gói vào bản Clicky bán kín mã không?

| Thành phần | Giấy phép | Bán kín mã? | Nguồn |
| --- | --- | --- | --- |
| sherpa-onnx 2.0.1 (thư viện chạy model) | Apache-2.0 | Được | PyPI `sherpa-onnx`, `sherpa-onnx-core` |
| Nghe: zipformer-vi 70k giờ (`sherpa-onnx-zipformer-vi-int8-2025-04-20`) | Apache-2.0 (thẻ HF; README trống, không ghi dữ liệu huấn luyện) | Được, **đây là bản đóng gói** | huggingface.co/zzasdf/viet_iter3_pseudo_label |
| Nghe: Zipformer-30M-RNNT-6000h (`...-30M-int8-2026-02-09`) | **CC-BY-NC-ND-4.0** | **Không** (cấm thương mại, cấm sửa) | huggingface.co/hynt/Zipformer-30M-RNNT-6000h |
| Đọc: Piper vi_VN-vais1000-medium | Dữ liệu CC-BY-4.0, nhưng tinh chỉnh từ giọng Anh "lessac" (giấy phép Blizzard, chỉ nghiên cứu) | Rủi ro | MODEL_CARD trong gói |
| Đọc: Piper vi_VN-25hours_single, vi_VN-vivos | Dữ liệu "Unknown" / CC-BY-NC-SA-4.0 | Không | MODEL_CARD trong gói |
| Phiên âm của Piper: espeak-ng, piper1-gpl | GPL-3.0 | Chỉ khi chạy như chương trình riêng và kèm mã nguồn; sherpa-onnx 2.0 đã gỡ espeak-ng | CHANGELOG sherpa-onnx 2.0.0 (#4055) |
| Đọc: VieNeu-TTS v3 Turbo (trọng số) | Apache-2.0, "trained from scratch", cho phép dùng thương mại giọng có sẵn | Được | huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo |
| VieNeu: sea-g2p (phiên âm), MOSS-Audio-Tokenizer-Nano (codec) | Apache-2.0 | Được | GitHub pnnbao97/sea-g2p; thẻ model |

Kết luận:
1. Bản cài chỉ chứa model nghe 70k giờ. Bản 30M chỉ dùng để so sánh nội bộ (`tai_model.py --model 30m`), không bao giờ vào bản cài.
2. Giọng đọc nên là VieNeu v3 (sạch giấy phép), không dùng Piper vi_VN trong bản bán.
3. Thẻ giấy phép trên Hugging Face do tác giả tự khai. Trước khi bán, cần luật sư xác nhận, nhất là bản 70k giờ (không nói dữ liệu huấn luyện lấy từ đâu).
