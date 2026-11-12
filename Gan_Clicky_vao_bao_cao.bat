@echo off
rem Kéo thả một file báo cáo .html vào đây: Clicky soạn lời trình bày, đọc giọng offline và ghi ra <tên>_Clicky.html.
rem Chạy hoàn toàn trên máy này. Không gửi số liệu đi đâu. File báo cáo gốc không bị sửa.
chcp 65001 >nul
setlocal
set CLICKY_LOCAL_ONLY=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if "%~1"=="" (
  echo Hãy kéo thả file báo cáo .html vào biểu tượng này.
  pause
  exit /b 1
)
if not exist "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi\tokens.txt" if not exist "voice\tts-piper-vi\tokens.txt" (
  echo Lần đầu: tải giọng tiếng Việt offline (khoảng 60 MB, chỉ tải mô hình giọng, không gửi dữ liệu)...
  python tools\giong_noi\tai_model.py --model piper-vais1000 --out "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi"
)
python -m pip install -q --disable-pip-version-check "sherpa-onnx>=2.0.1,<3" PyYAML openpyxl python-pptx numpy "playwright>=1.47,<2"
python -m present.clicky_html "%~1" %2 %3 %4 %5 %6
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Kiểm lại: mọi con số Clicky nói có nằm trong báo cáo không...
if exist "%~dpn1_Clicky.html" python -m present.kiem_bien_ban "%~dpn1_Clicky.html" --goc "%~1"
echo.
echo Kiểm hình: mở bản vừa dựng bằng Edge chạy ẩn, xem Clicky có che con số nào không...
if exist "%~dpn1_Clicky.html" python -m present.kiem_hinh "%~dpn1_Clicky.html"
echo.
echo Xong. Mở file _Clicky.html để xem. Muốn sửa lời: sửa file _kich_ban_Clicky.yaml rồi kéo thả lại kèm --kich-ban.
pause
