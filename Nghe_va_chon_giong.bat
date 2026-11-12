@echo off
rem Nghe hai giọng tiếng Việt offline đọc cùng mấy câu báo cáo (số giả lập), rồi chọn giọng Clicky dùng.
rem Lần đầu tải giọng VieNeu (khoảng 740 MB, chỉ tải mô hình giọng, không gửi dữ liệu). Sau đó chạy không cần mạng.
chcp 65001 >nul
setlocal
set CLICKY_LOCAL_ONLY=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call tim_python.bat || (pause & exit /b 1)
if not exist "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi\tokens.txt" if not exist "voice\tts-piper-vi\tokens.txt" (
  echo Tải giọng Piper (khoảng 60 MB^)...
  %PY% tools\giong_noi\tai_model.py --model piper-vais1000 --out "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi"
)
echo Cài bộ đọc giọng VieNeu (một lần)...
%PY% -m pip install -q --disable-pip-version-check "sherpa-onnx>=2.0.1,<3" -r requirements-vieneu.txt
%PY% -m pip install -q --disable-pip-version-check --no-deps vieneu==3.8.3
if not exist "%LOCALAPPDATA%\Clicky\voice\vieneu\NGUON.txt" (
  echo Tải giọng VieNeu (một lần, vài phút^)...
  %PY% tools\giong_noi\tai_vieneu.py --out "%LOCALAPPDATA%\Clicky\voice\vieneu"
)
echo Đang cho hai giọng đọc thử...
%PY% tools\giong_noi\nghe_so_sanh.py --ra "So sanh giong.html"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "So sanh giong.html"
echo.
choice /c 12 /n /m "Nghe xong, gõ 1 để dùng VieNeu, gõ 2 để dùng Piper: "
if errorlevel 2 (%PY% tools\giong_noi\nghe_so_sanh.py --chon piper) else (%PY% tools\giong_noi\nghe_so_sanh.py --chon vieneu)
echo Lần sau kéo thả báo cáo vào Gan_Clicky_vao_bao_cao.bat, Clicky đọc bằng giọng này.
pause
