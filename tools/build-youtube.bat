@ECHO OFF
REM ============================================================================
REM  build-youtube.bat - Compila Mixxx con la integración de YouTube Music.
REM
REM  Requisitos previos (una sola vez, necesita permisos de administrador):
REM    1) Visual Studio 2022 Build Tools (C++ workload):
REM         winget install --id Microsoft.VisualStudio.2022.BuildTools -e ^
REM           --override "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ^
REM                        --add Microsoft.VisualStudio.Component.Windows11SDK.22000"
REM    2) CMake y Ninja:
REM         winget install Kitware.CMake -e
REM         winget install Ninja-build.Ninja -e
REM
REM  Uso:  tools\build-youtube.bat
REM  Resultado:  build\mixxx.exe
REM ============================================================================
SETLOCAL ENABLEEXTENSIONS

SET "ROOT=%~dp0.."
SET "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
SET "MSVC_FOUND="

REM 1) Buscar MSVC
IF EXIST "%VSWHERE%" (
    FOR /F "usebackq tokens=*" %%i IN (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) DO SET "MSVC_FOUND=%%i"
)
IF NOT DEFINED MSVC_FOUND (
    ECHO [ERROR] Visual Studio Build Tools ^(C++^) no encontrado.
    ECHO          Instalalo como administrador:
    ECHO            winget install --id Microsoft.VisualStudio.2022.BuildTools -e
    EXIT /B 1
)
ECHO [OK] MSVC: %MSVC_FOUND%

REM 2) Verificar CMake y Ninja
WHERE cmake >NUL 2>NUL
IF ERRORLEVEL 1 (
    ECHO [ERROR] CMake no encontrado. Instala: winget install Kitware.CMake -e
    EXIT /B 1
)
WHERE ninja >NUL 2>NUL
IF ERRORLEVEL 1 (
    ECHO [ERROR] Ninja no encontrado. Instala: winget install Ninja-build.Ninja -e
    EXIT /B 1
)
ECHO [OK] CMake y Ninja presentes.

REM 3) Configurar el entorno de dependencias precompiladas (~2.5 GB, una sola vez).
REM    Esto define MIXXX_VCPKG_ROOT, CMAKE_GENERATOR=Ninja, etc.
CALL "%~dp0windows_buildenv.bat" setup
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
