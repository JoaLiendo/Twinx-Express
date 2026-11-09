@echo off

rem Desinstala el programa Twinx Express de ESTA carpeta (la instalada, por ejemplo C:\KioscoApp) SIN tocar
rem los datos del comercio (ver Desinstalar.ps1). Los datos de %LOCALAPPDATA%\KioscoApp nunca se borran.
rem Sin setlocal a proposito: al terminar, cmd volveria a la carpeta guardada, que ya no existe. Por la misma
rem razon, "(goto) 2>nul" cierra el lote sin volver a leer este archivo ya borrado (si no, cmd muestra errores).

if not exist "%~dp0Desinstalar.ps1" (
    echo No se encontro Desinstalar.ps1 en esta carpeta.
    echo.
    pause
    exit /b 1
)

set "TWINX_DESTINO=%~dp0."

rem Todo en una sola linea: cmd la lee completa antes de ejecutarla, asi que despues no necesita volver a
rem leer este archivo (que se borra junto con la carpeta del programa) ni buscarlo desde otra carpeta.
rem Windows no deja borrar la carpeta en la que esta parada esta ventana: se trabaja desde %TEMP%.
cd /d "%TEMP%" & powershell -NoProfile -ExecutionPolicy Bypass -File "%TWINX_DESTINO%\Desinstalar.ps1" -Destino "%TWINX_DESTINO%" %* && (echo. & pause & (goto) 2>nul & exit /b 0) || (echo. & echo La desinstalacion NO se completo. Lee el mensaje de arriba. Los datos no se tocaron. & pause & (goto) 2>nul & exit /b 1)
