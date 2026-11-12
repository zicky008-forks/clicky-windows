@echo off
rem Kéo thả một báo cáo .html vào đây: máy tạo bản <tên>_CHE.html giữ nguyên bố cục, bảng, biểu đồ và nhãn,
rem nhưng mọi con số đổi sang số giả cùng dạng. Chạy trên máy này, không gửi gì đi. File gốc không bị sửa.
rem Muốn che cả tên (công ty, người): ghi vào TEN_CAN_CHE.txt cạnh file này, mỗi dòng "Tên thật = Tên thay".
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
call tim_python.bat || (pause & exit /b 1)
if "%~1"=="" (
  echo Hãy kéo thả file báo cáo .html vào biểu tượng này.
  pause
  exit /b 1
)
%PY% tools\che_so_lieu.py "%~1" --tu-file TEN_CAN_CHE.txt
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "%~dpn1_CHE.html"
pause
