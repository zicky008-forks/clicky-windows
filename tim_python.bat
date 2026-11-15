@echo off
rem Find a Python 3.10-3.13 that really runs, and put the command in PY. The "python" that Windows ships is
rem only a Microsoft Store shortcut, so it is skipped. If none is found, offer to install Python 3.12 for this
rem Windows account only (no administrator rights needed), from python.org.
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do if not defined PY py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
if not defined PY python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,13)" >nul 2>&1 && set "PY=python"
for %%V in (312 313 311 310) do for %%B in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%" "C:") do (
  if not defined PY if exist "%%~B\Python%%V\python.exe" set PY="%%~B\Python%%V\python.exe"
)
if defined PY goto :found
echo.
echo Máy này chưa có Python, nên Clicky chưa chạy được.
echo Em có thể cài Python 3.12 từ python.org cho riêng tài khoản Windows này: khoảng 25 MB, không cần quyền quản trị.
choice /c YN /m "Cài ngay (Y = có, N = không)"
if errorlevel 2 exit /b 1
set "PYSETUP=%TEMP%\python-3.12.10-amd64.exe"
echo Đang tải Python...
curl -fL -o "%PYSETUP%" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
if errorlevel 1 goto :failed
echo Đang cài Python (khoảng 1 phút^)...
"%PYSETUP%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0 Shortcuts=0
del "%PYSETUP%" >nul 2>&1
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY goto :failed
:found
%PY% -c "import sys" >nul 2>&1 || goto :failed
exit /b 0
:failed
echo Chưa cài được Python. Có thể máy công ty chặn cài phần mềm: anh nhờ IT cài Python 3.12 rồi chạy lại.
set "PY="
exit /b 1
