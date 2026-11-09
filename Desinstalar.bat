@echo off
setlocal

rem Desinstala el programa Twinx Express SIN tocar los datos del comercio (ver Desinstalar.ps1).
rem Debe estar en la misma carpeta que KioscoApp.exe (la carpeta instalada o el entregable).

if not exist "%~dp0Desinstalar.ps1" (
    echo No se encontro Desinstalar.ps1 en esta carpeta.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Desinstalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"
echo.
if not "%CODIGO%"=="0" (
    echo La desinstalacion NO se completo. Lee el mensaje de arriba. Los datos no se tocaron.
) else (
    echo Podes cerrar esta ventana.
)
pause
exit /b %CODIGO%
