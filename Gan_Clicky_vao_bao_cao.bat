@echo off
rem Kéo thả file báo cáo .html vào đây (có thể kéo kèm file kịch bản .yaml anh đã sửa). Clicky soạn lời trình bày,
rem đọc bằng giọng offline và ghi ra <tên>_Clicky.html. Chạy hoàn toàn trên máy này, không gửi số liệu đi đâu.
rem File báo cáo gốc không bị sửa. Kịch bản anh đã sửa không bao giờ bị ghi đè.
rem Màn hình chỉ hiện 3 dòng: ĐẠT hay KHÔNG ĐẠT, việc cần xem, tên file. Mọi chi tiết ghi vào thư mục nhat_ky trong "Clicky tri nho".
setlocal EnableDelayedExpansion
rem Explorer không đặt dấu nháy quanh tên file không có dấu cách: "P&L_T10.html" bị cắt ở dấu &. Dòng lệnh đầy
rem đủ vẫn còn trong cmdcmdline; đọc bằng !...! để dấu & không chạy thành lệnh. Python tự tách tên file.
set "CLICKY_CMDLINE=!cmdcmdline!"
rem Mở bằng kéo thả (cmd /c): khi xong phải đóng hẳn cmd bằng exit, không thì phần tên file sau dấu & (L_T10.html)
rem bị cmd chạy như một lệnh. Mở từ cửa sổ cmd thì chỉ thoát file .bat, giữ cửa sổ.
set "CLICKY_TU_EXPLORER="
if /i not "!cmdcmdline:/c =!"=="!cmdcmdline!" set "CLICKY_TU_EXPLORER=1"
setlocal DisableDelayedExpansion
chcp 65001 >nul
set CLICKY_LOCAL_ONLY=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call tim_python.bat || (set "CODE=1" & goto :ra)
call :tim_tri_nho
if not defined CLICKY_TU_DOC set "CLICKY_TU_DOC=%CLICKY_TRI_NHO%\TU_DOC.txt"
if exist giong_chon.txt set /p CLICKY_GIONG=<giong_chon.txt
if exist "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi\tokens.txt" goto :co_giong
if exist "voice\tts-piper-vi\tokens.txt" goto :co_giong
echo Lần đầu: tải giọng tiếng Việt offline (khoảng 60 MB, chỉ tải mô hình giọng, không gửi dữ liệu)...
%PY% tools\giong_noi\tai_model.py --model piper-vais1000 --out "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi"
if errorlevel 1 goto :loi_giong
:co_giong
%PY% -m pip install -q --disable-pip-version-check "sherpa-onnx>=2.0.1,<3" PyYAML openpyxl python-pptx numpy "playwright>=1.47,<2" lameenc
if errorlevel 1 goto :loi_pip
%PY% -m present.clicky_html --tu-bat "%~1" "%~2" "%~3"
if errorlevel 1 goto :dung
set "CODE=0"
goto :ra
:dung
set "CODE=1"
goto :ra
:loi_pip
echo.
echo KHÔNG ĐẠT: chưa cài được các thư viện Clicky cần (lệnh pip báo lỗi ở trên).
echo Lần đầu chạy, máy cần Internet để tải thư viện. Nếu máy công ty chặn tải, anh nhờ IT cho phép pypi.org rồi chạy lại.
set "CODE=1"
goto :ra
:loi_giong
echo.
echo KHÔNG ĐẠT: chưa tải được giọng tiếng Việt offline. Anh kiểm tra Internet rồi chạy lại.
set "CODE=1"
goto :ra
:ra
pause
if defined CLICKY_TU_EXPLORER exit %CODE%
exit /b %CODE%
:tim_tri_nho
rem Thư mục Documents thật: máy có OneDrive sao lưu thì nó không nằm ở %USERPROFILE%\Documents.
if defined CLICKY_TRI_NHO exit /b 0
set "DOCS="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('MyDocuments')"`) do set "DOCS=%%D"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "CLICKY_TRI_NHO=%DOCS%\Clicky tri nho"
exit /b 0
