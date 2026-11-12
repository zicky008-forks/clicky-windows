@echo off
rem Kéo thả file báo cáo .html vào đây (có thể kéo kèm file kịch bản .yaml anh đã sửa). Clicky soạn lời trình bày,
rem đọc bằng giọng offline và ghi ra <tên>_Clicky.html. Chạy hoàn toàn trên máy này, không gửi số liệu đi đâu.
rem File báo cáo gốc không bị sửa. Kịch bản anh đã sửa không bao giờ bị ghi đè.
chcp 65001 >nul
setlocal
set CLICKY_LOCAL_ONLY=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call tim_python.bat || (pause & exit /b 1)
call :tim_tri_nho
if not defined CLICKY_TU_DOC set "CLICKY_TU_DOC=%CLICKY_TRI_NHO%\TU_DOC.txt"
if exist giong_chon.txt set /p CLICKY_GIONG=<giong_chon.txt
set "REPORT="
set "KB="
:doc_tham_so
if "%~1"=="" goto :xong_tham_so
if /i "%~x1"==".yaml" set "KB=%~f1"
if /i "%~x1"==".yml" set "KB=%~f1"
if /i not "%~x1"==".yaml" if /i not "%~x1"==".yml" set "REPORT=%~f1"
shift
goto :doc_tham_so
:xong_tham_so
if not defined REPORT goto :thieu_bao_cao
for %%F in ("%REPORT%") do set "OUT=%%~dpnF_Clicky.html"
echo Trí nhớ của Clicky (câu treo, tên người chịu trách nhiệm, cách đọc riêng): "%CLICKY_TRI_NHO%"
if exist "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi\tokens.txt" goto :co_giong
if exist "voice\tts-piper-vi\tokens.txt" goto :co_giong
echo Lần đầu: tải giọng tiếng Việt offline (khoảng 60 MB, chỉ tải mô hình giọng, không gửi dữ liệu)...
%PY% tools\giong_noi\tai_model.py --model piper-vais1000 --out "%LOCALAPPDATA%\Clicky\voice\tts-piper-vi"
:co_giong
%PY% -m pip install -q --disable-pip-version-check "sherpa-onnx>=2.0.1,<3" PyYAML openpyxl python-pptx numpy "playwright>=1.47,<2" lameenc
%PY% -m present.tri_nho --thu-muc "%CLICKY_TRI_NHO%" chu
if errorlevel 1 goto :loi
set "KBARG="
if defined KB set KBARG=--kich-ban "%KB%"
%PY% -m present.clicky_html "%REPORT%" %KBARG% --tri-nho "%CLICKY_TRI_NHO%"
if errorlevel 1 goto :loi
echo.
echo Kiểm lại: mọi con số Clicky nói có nằm đúng chỗ trong báo cáo không...
%PY% -m present.kiem_bien_ban "%OUT%" --goc "%REPORT%"
if errorlevel 1 goto :loi
echo.
echo Kiểm hình: mở bản vừa dựng bằng Edge chạy ẩn, xem Clicky có che con số nào không...
%PY% -m present.kiem_hinh "%OUT%" --tri-nho "%CLICKY_TRI_NHO%"
if errorlevel 3 goto :mo_file
if errorlevel 1 goto :loi
:mo_file
echo.
echo Xong. Em mở bản trình bày để anh xem: "%OUT%"
echo Muốn sửa lời: sửa file _kich_ban_Clicky.yaml cạnh báo cáo, rồi kéo thả báo cáo vào đây lần nữa.
start "" "%OUT%"
pause
exit /b 0
:thieu_bao_cao
echo Hãy kéo thả file báo cáo .html vào biểu tượng này. Có thể kéo kèm file kịch bản .yaml anh đã sửa.
pause
exit /b 1
:loi
echo.
echo KHÔNG ĐẠT: chưa có bản trình bày nào để gửi. Anh xem dòng báo lỗi ở trên, sửa rồi chạy lại.
pause
exit /b 1
:tim_tri_nho
rem Thư mục Documents thật: máy có OneDrive sao lưu thì nó không nằm ở %USERPROFILE%\Documents.
if defined CLICKY_TRI_NHO exit /b 0
set "DOCS="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('MyDocuments')"`) do set "DOCS=%%D"
if not defined DOCS set "DOCS=%USERPROFILE%\Documents"
set "CLICKY_TRI_NHO=%DOCS%\Clicky tri nho"
exit /b 0
