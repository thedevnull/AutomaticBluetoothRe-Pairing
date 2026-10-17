@echo off
rem ======================================================================
rem  Builds BluetoothDeviceControl.exe  (Qt GUI, single file, no console)
rem  Put this file next to the GUI script and the core script, then
rem  double-click it or run it from a terminal.
rem  Output: dist\BluetoothDeviceControl.exe
rem ======================================================================
setlocal EnableExtensions
cd /d "%~dp0"

rem ---- Settings (edit here if needed) ---------------------------------
set "APP_NAME=BluetoothDeviceControl"
set "GUI=bt_device_control_gui.py"
set "CORE=bluetooth_re_pairing.py"
rem Qt binding to bundle: PySide6 or PyQt6
set "QT_PACKAGE=PySide6"
rem 1 = build inside a private virtual environment (.venv-build)
set "USE_VENV=0"
rem 1 = build WITH a console window (use it to see errors), 0 = no console
set "CONSOLE=0"
rem 1 = start the finished exe once with --help to check that it launches
set "SMOKE_TEST=1"
rem Extra PyInstaller options, for example: --collect-submodules winrt
set "EXTRA_ARGS="

call :main
set "RC=%ERRORLEVEL%"
echo %cmdcmdline% | find /i "%~nx0" >nul && pause
exit /b %RC%


:main
set "OTHER_QT=PyQt6"
if /i "%QT_PACKAGE%"=="PyQt6" set "OTHER_QT=PySide6"

rem ---- Locate the scripts (auto-detect if the names above are wrong) ---
if not exist "%GUI%" (
    for /f "delims=" %%f in ('findstr /M /C:"QSystemTrayIcon" *.py 2^>nul') do set "GUI=%%f"
)
if not exist "%CORE%" (
    for /f "delims=" %%f in ('findstr /M /C:"class WindowsBackend" *.py 2^>nul') do set "CORE=%%f"
)
if not exist "%GUI%" (
    echo [x] GUI script not found. Edit GUI= at the top of this file.
    exit /b 1
)
if not exist "%CORE%" (
    echo [x] Core script not found. Edit CORE= at the top of this file.
    exit /b 1
)
echo [*] GUI  : %GUI%
echo [*] Core : %CORE%

rem ---- Find Python -----------------------------------------------------
set "PYEXE="
for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE (
    echo [x] Python not found. Install it from python.org and enable Add to PATH.
    exit /b 1
)
echo [*] Python: %PYEXE%

if "%USE_VENV%"=="1" (
    if not exist ".venv-build\Scripts\python.exe" (
        echo [*] Creating virtual environment .venv-build ...
        "%PYEXE%" -m venv .venv-build
        if errorlevel 1 (
            echo [x] Could not create the virtual environment.
            exit /b 1
        )
    )
    set "PYEXE=%CD%\.venv-build\Scripts\python.exe"
)

rem ---- Build dependencies ---------------------------------------------
echo [*] Installing build dependencies (the first run takes a while) ...
"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
"%PYEXE%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [x] Could not install PyInstaller.
    exit /b 1
)
"%PYEXE%" -m pip install %QT_PACKAGE% winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Bluetooth
if errorlevel 1 (
    echo [x] Could not install the Qt / WinRT packages.
    exit /b 1
)

rem ---- Optional: generate the exe icon ----------------------------------
set "ICON_ARG="
if exist "make_app_icon.py" (
    if exist "app_icon.ico" del "app_icon.ico"
    "%PYEXE%" make_app_icon.py >nul 2>&1
)
if exist "app_icon.ico" set "ICON_ARG=--icon app_icon.ico"

set "WINMODE=--windowed"
if "%CONSOLE%"=="1" set "WINMODE=--console"

rem ---- Build -----------------------------------------------------------
rem  --windowed            no console window (GUI program)
rem  --add-data CORE;.     ships the core script next to the GUI, so it also
rem                        works when it is loaded by file path
rem  --hidden-import ...   winrt modules are imported lazily by the core;
rem                        asyncio/shutil/datetime are used by the core only
rem  --exclude-module      PyInstaller aborts if two Qt bindings are installed
echo [*] Building %APP_NAME%.exe ...
"%PYEXE%" -m PyInstaller --noconfirm --clean --onefile %WINMODE% ^
  --name "%APP_NAME%" ^
  --distpath dist --workpath build ^
  --paths "%CD%" ^
  --add-data "%CORE%;." ^
  --hidden-import winrt.windows.devices.enumeration ^
  --hidden-import winrt.windows.devices.bluetooth ^
  --hidden-import winrt.windows.foundation ^
  --hidden-import winrt.windows.foundation.collections ^
  --hidden-import asyncio ^
  --hidden-import shutil ^
  --hidden-import datetime ^
  --exclude-module %OTHER_QT% ^
  --exclude-module %QT_PACKAGE%.QtWebEngineCore ^
  --exclude-module %QT_PACKAGE%.QtWebEngineWidgets ^
  --exclude-module %QT_PACKAGE%.QtQml ^
  --exclude-module %QT_PACKAGE%.QtQuick ^
  --exclude-module %QT_PACKAGE%.Qt3DCore ^
  --exclude-module %QT_PACKAGE%.QtMultimedia ^
  --exclude-module %QT_PACKAGE%.QtCharts ^
  --exclude-module %QT_PACKAGE%.QtPdf ^
  %ICON_ARG% %EXTRA_ARGS% ^
  "%GUI%"

if exist "%APP_NAME%.spec" del "%APP_NAME%.spec"
if not exist "dist\%APP_NAME%.exe" (
    echo [x] Build failed - the exe was not created. Read the messages above.
    echo     Tip: set CONSOLE=1 at the top and run the exe from a terminal.
    exit /b 1
)
echo [+] Built: %CD%\dist\%APP_NAME%.exe

rem ---- Smoke test ------------------------------------------------------
if "%SMOKE_TEST%"=="1" (
    echo [*] Smoke test: starting the exe with --help ...
    start /wait "" "dist\%APP_NAME%.exe" --help
    if errorlevel 1 (
        echo [!] The exe exited with an error. Set CONSOLE=1, rebuild, and run it from a terminal.
    ) else (
        echo [+] The exe starts correctly.
    )
)

echo.
echo Note: if you enabled Start at login, the exe path is stored in the registry.
echo If you move the exe, turn that option off and on again inside the program.
exit /b 0
