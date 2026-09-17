@ECHO OFF
REM ============================================================================
REM  build-youtube.bat - Compila Mixxx con la integracion de YouTube Music.
REM
REM  Requisitos (una sola vez):
REM    1) Visual Studio 2022 Build Tools (C++ workload + Windows SDK):
REM         winget install --id Microsoft.VisualStudio.2022.BuildTools -e
REM    2) CMake:  winget install Kitware.CMake -e
REM    3) Ninja:  winget install Ninja-build.Ninja -e
REM
REM  Uso:  tools\build-youtube.bat
REM  Resultado:  build\mixxx.exe
REM ============================================================================
SETLOCAL ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

SET "ROOT=%~dp0.."
SET "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
SET "VSINSTALL="

REM 1) Buscar Visual Studio (MSVC)
IF EXIST "%VSWHERE%" (
    FOR /F "usebackq tokens=*" %%i IN (`"%VSWHERE%" -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) DO SET "VSINSTALL=%%i"
)
IF NOT DEFINED VSINSTALL (
    ECHO [ERROR] No se encontro Visual Studio con herramientas de C++.
    ECHO          Instalalo como administrador:
    ECHO            winget install --id Microsoft.VisualStudio.2022.BuildTools -e
    EXIT /B 1
)

SET "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
IF NOT EXIST "%VCVARS%" (
    ECHO [ERROR] No se encontro vcvars64.bat en %VSINSTALL%
    EXIT /B 1
)
CALL "%VCVARS%" || EXIT /B 1
ECHO [OK] MSVC: %VSINSTALL%

REM 2) Buscar CMake y Ninja (instalados con winget)
SET "WINGET=%LOCALAPPDATA%\Microsoft\WinGet\Packages"
SET "CMAKEDIR="
FOR /F "delims=" %%f IN ('where /r "%WINGET%" cmake.exe 2^>nul') DO (
    IF NOT DEFINED CMAKEDIR SET "CMAKEDIR=%%~dpf"
)
SET "NINJADIR="
FOR /F "delims=" %%f IN ('where /r "%WINGET%" ninja.exe 2^>nul') DO (
    IF NOT DEFINED NINJADIR SET "NINJADIR=%%~dpf"
)
WHERE cmake >NUL 2>NUL
IF ERRORLEVEL 1 (
    IF NOT DEFINED CMAKEDIR (
        ECHO [ERROR] CMake no encontrado. Instala: winget install Kitware.CMake -e
        EXIT /B 1
    )
    SET "PATH=!CMAKEDIR!;%PATH%"
)
WHERE ninja >NUL 2>NUL
IF ERRORLEVEL 1 (
    IF NOT DEFINED NINJADIR (
        ECHO [ERROR] Ninja no encontrado. Instala: winget install Ninja-build.Ninja -e
        EXIT /B 1
    )
    SET "PATH=!NINJADIR!;%PATH%"
)
ECHO [OK] CMake y Ninja presentes.

REM 3) Configurar el entorno de dependencias precompiladas (~2.5 GB, una sola vez)
CALL "%ROOT%\tools\windows_buildenv.bat" setup
IF ERRORLEVEL 1 (
    ECHO [ERROR] Fallo al configurar el build environment.
    EXIT /B 1
)
ECHO [OK] Build environment: %MIXXX_VCPKG_ROOT%

REM 4) Configurar (descarga el buildenv en la primera ejecucion)
SET "BUILD_DIR=%ROOT%\build"
IF NOT EXIST "%BUILD_DIR%" MD "%BUILD_DIR%"

cmake -S "%ROOT%" -B "%BUILD_DIR%" -G Ninja ^
      -DCMAKE_BUILD_TYPE=Release ^
      -DCMAKE_TOOLCHAIN_FILE="%MIXXX_VCPKG_ROOT%\scripts\buildsystems\vcpkg.cmake"
IF ERRORLEVEL 1 (
    ECHO [ERROR] La configuracion de CMake fallo.
    EXIT /B 1
)

REM 5) Compilar
cmake --build "%BUILD_DIR%" --parallel
IF ERRORLEVEL 1 (
    ECHO [ERROR] La compilacion fallo.
    EXIT /B 1
)

ECHO.
ECHO [OK] Compilacion completa. Ejecuta: "%BUILD_DIR%\mixxx.exe"
ENDLOCAL
