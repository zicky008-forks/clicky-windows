@echo off
rem Ghi câu hỏi người xem gửi qua Clicky vào trí nhớ trên máy này (CAU_TREO.md). Không gửi gì đi đâu.
rem Cách 1: mở email câu hỏi do Clicky gửi trong Outlook, bấm Ctrl+A rồi Ctrl+C, sau đó bấm đúp file này.
rem Cách 2: kéo thả file email đã lưu dạng .txt hoặc .eml vào file này.
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call "%~dp0tim_python.bat" || (pause & exit /b 1)
call :tim_tri_nho
echo Trí nhớ của Clicky: "%CLICKY_TRI_NHO%"
if "%~1"=="" (%PY% -m present.tri_nho --thu-muc "%CLICKY_TRI_NHO%" ghi) else (%PY% -m present.tri_nho --thu-muc "%CLICKY_TRI_NHO%" ghi "%~f1")
if errorlevel 1 goto :loi
echo.
echo Anh điền dòng "Trả lời:" dưới mỗi câu rồi lưu lại. Lần dựng báo cáo sau, Clicky đọc câu trả lời ngay sau lời chào.
start "" notepad "%CLICKY_TRI_NHO%\CAU_TREO.md"
pause
exit /b 0
:loi
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
