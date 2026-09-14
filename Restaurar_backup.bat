@echo off
setlocal

rem Este script no hace ningun restore por si mismo: solo llama a
rem KioscoApp.exe con --restore, que es quien valida y restaura el
rem backup. Debe estar siempre en la misma carpeta que KioscoApp.exe.

set "KIOSCO_EXE=%~dp0KioscoApp.exe"

if not exist "%KIOSCO_EXE%" (
    echo No se encontro KioscoApp.exe en esta carpeta.
    echo Este archivo tiene que estar junto a KioscoApp.exe.
    echo.
    pause
    exit /b 1
)

set "ZIP=%~1"

if "%ZIP%"=="" (
    echo Arrastra el archivo de backup ^(termina en .zip^) sobre este
    echo archivo, o escribi su ubicacion completa abajo y presiona Enter.
    echo.
    set /p ZIP=Ubicacion del backup:
)

if "%ZIP%"=="" (
    echo No se indico ningun archivo de backup. Cancelado.
    echo.
    pause
    exit /b 1
)

echo.
echo Se va a restaurar KioscoApp desde:
echo   %ZIP%
echo.
echo IMPORTANTE: si KioscoApp esta abierto, cerralo antes de continuar.
echo Esto va a reemplazar los datos actuales por los del backup elegido.
echo.
pause

"%KIOSCO_EXE%" --restore "%ZIP%"

endlocal
