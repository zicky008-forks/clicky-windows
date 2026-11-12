@echo off
rem Ghi câu hỏi người xem gửi qua Clicky vào trí nhớ trên máy này (CAU_TREO.md). Không gửi gì đi đâu.
rem Cách 1: mở email câu hỏi do Clicky gửi trong Outlook, bấm Ctrl+A rồi Ctrl+C, sau đó bấm đúp file này.
rem Cách 2: kéo thả file email đã lưu dạng .txt hoặc .eml vào file này.
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
if not defined CLICKY_TRI_NHO set "CLICKY_TRI_NHO=%USERPROFILE%\Documents\Clicky tri nho"
cd /d "%~dp0"
call tim_python.bat || (pause & exit /b 1)
%PY% -m present.tri_nho --thu-muc "%CLICKY_TRI_NHO%" ghi %1
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Anh điền dòng "Trả lời:" dưới mỗi câu rồi lưu lại. Lần dựng báo cáo sau, Clicky đọc câu trả lời ở đầu bài.
start "" notepad "%CLICKY_TRI_NHO%\CAU_TREO.md"
pause
