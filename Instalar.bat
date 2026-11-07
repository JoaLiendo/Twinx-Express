@echo off
setlocal

rem Instala o actualiza Twinx Express desde esta carpeta (ver Instalar.ps1, que es quien hace todo).
rem Debe estar siempre en la misma carpeta que KioscoApp.exe.

if not exist "%~dp0Instalar.ps1" (
    echo No se encontro Instalar.ps1 en esta carpeta.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"
echo.
if not "%CODIGO%"=="0" (
    echo La instalacion NO se completo. Lee el mensaje de arriba.
) else (
    echo Podes cerrar esta ventana.
)
pause
exit /b %CODIGO%
