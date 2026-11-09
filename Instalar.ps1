<#
.SYNOPSIS
    Instala o actualiza Twinx Express (KioscoApp) desde la carpeta en la que esta este script.

.DESCRIPTION
    Dos casos, decididos solos:

      - Instalacion nueva: copia esta carpeta completa a la carpeta de destino (C:\KioscoApp por defecto)
        y crea los accesos directos "Twinx Express" en el Escritorio y en el menu Inicio.
      - Actualizacion: si en el destino ya hay un KioscoApp.exe, NO copia nada a mano: ejecuta
        KioscoApp.exe --actualizar <destino> de la version nueva, que hace el backup previo verificado,
        conserva una copia de la version instalada (para --rollback), reemplaza el programa y migra la
        base de datos (y vuelve atras sola si la migracion falla). Despues refresca los accesos directos.

    Nunca toca %LOCALAPPDATA%\KioscoApp (datos, backups, registro, versiones): los datos del cliente no
    forman parte del programa. Se niega a instalar si esta carpeta trae una base de datos, un data\ o
    secretos fiscales, y a actualizar si la aplicacion esta abierta.

    No requiere permisos de administrador (C:\KioscoApp y los accesos directos son del usuario actual).

.PARAMETER Destino
    Carpeta de instalacion. Por defecto C:\KioscoApp.

.PARAMETER SinAccesosDirectos
    No crear ni refrescar accesos directos.

.PARAMETER CarpetaAccesosDirectos
    Carpeta donde dejar los accesos directos en lugar del Escritorio y el menu Inicio (pruebas).

.PARAMETER EjecutableActualizador
    Programa a invocar para actualizar en lugar de <origen>\KioscoApp.exe (solo pruebas automatizadas).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Instalar.ps1
    (o doble clic en Instalar.bat)
#>
[CmdletBinding()]
param(
    [string]$Destino = 'C:\KioscoApp',
    [switch]$SinAccesosDirectos,
    [string]$CarpetaAccesosDirectos = '',
    [string]$EjecutableActualizador = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$origen = $PSScriptRoot
$nombreAcceso = 'Twinx Express'
$puertoAplicacion = 8000

function Fallar([string]$mensaje) {
    Write-Host ''
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

function Normalizar([string]$ruta) {
    return [System.IO.Path]::GetFullPath($ruta).TrimEnd('\').ToLowerInvariant()
}

function Aplicacion-Abierta {
    # El puerto de la aplicacion escuchando es la senal mas simple de "esta abierta"; KioscoApp.exe
    # --actualizar vuelve a comprobarlo (y el lock de instancia) antes de tocar nada.
    try {
        $cliente = New-Object System.Net.Sockets.TcpClient
        $cliente.Connect('127.0.0.1', $puertoAplicacion)
        $cliente.Close()
        return $true
    } catch {
        return $false
    }
}

function Crear-AccesoDirecto([string]$carpeta, [string]$exe) {
    if (-not (Test-Path -LiteralPath $carpeta -PathType Container)) {
        New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
    }
    $ruta = Join-Path $carpeta "$nombreAcceso.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $acceso = $shell.CreateShortcut($ruta)
    $acceso.TargetPath = $exe
    $acceso.WorkingDirectory = Split-Path -Parent $exe
    $acceso.IconLocation = "$exe,0"
    $acceso.Description = 'Twinx Express - control de stock, ventas y caja'
    $acceso.Save()
    return $ruta
}

# ---------------------------------------------------------------------------
# 1. Validar la carpeta de origen (lo que se va a instalar)
# ---------------------------------------------------------------------------
Write-Host "[1/4] Validando el programa en $origen..."
foreach ($requerido in @('KioscoApp.exe', '_internal', 'LEEME.txt', 'Restaurar_backup.bat')) {
    if (-not (Test-Path -LiteralPath (Join-Path $origen $requerido))) {
        Fallar "Falta '$requerido' junto a Instalar.ps1. Ejecutalo desde la carpeta completa del programa."
    }
}
$prohibidos = @(Get-ChildItem -LiteralPath $origen -Recurse -Force | Where-Object {
    ($_.PSIsContainer -and $_.Name -in @('data', 'backups', 'fiscal_privado', 'imagenes_productos')) -or
    (-not $_.PSIsContainer -and ($_.Name -like '*.db' -or $_.Name -like '*.db-*' -or $_.Name -like '*.sqlite*' -or $_.Name -like '*.dpapi' -or $_.Name -like '*.pfx' -or $_.Name -like '*.p12' -or $_.Name -like '*.key')) -or
    (-not $_.PSIsContainer -and $_.Name -like '*.zip' -and $_.FullName -ne (Join-Path $origen '_internal\base_library.zip'))
})
if ($prohibidos.Count -gt 0) {
    Fallar "Esta carpeta contiene datos o secretos que no pertenecen al programa ($($prohibidos[0].Name)): no se instala. Pedi un entregable limpio."
}
$destinoCompleto = [System.IO.Path]::GetFullPath($Destino)
# Sin barra final: "C:\Kiosco\" entre comillas hace que la barra escape la comilla al pasar por cmd.
if ($destinoCompleto.Length -gt 3) { $destinoCompleto = $destinoCompleto.TrimEnd('\') }
# La raiz de una unidad ("D:\") conserva su barra: duplicarla la hace sobrevivir a las comillas de cmd.
$destinoParaCmd = $destinoCompleto
if ($destinoParaCmd.EndsWith('\')) { $destinoParaCmd = $destinoParaCmd + '\' }
if ((Normalizar $origen) -eq (Normalizar $destinoCompleto)) {
    Fallar 'El origen y el destino son la misma carpeta: ejecuta el instalador desde la carpeta descargada o el pendrive, no desde la instalacion.'
}
if ((Normalizar $destinoCompleto).StartsWith((Normalizar $origen) + '\')) {
    Fallar 'El destino no puede estar dentro de la carpeta de origen.'
}

$exeDestino = Join-Path $destinoCompleto 'KioscoApp.exe'
$exeOrigen = Join-Path $origen 'KioscoApp.exe'
$hayInstalacion = Test-Path -LiteralPath $exeDestino -PathType Leaf

# ---------------------------------------------------------------------------
# 2. Instalar o actualizar
# ---------------------------------------------------------------------------
if ($hayInstalacion) {
    Write-Host "[2/4] Hay una instalacion en ${destinoCompleto}: se ACTUALIZA (los datos no se tocan)."
    if (Aplicacion-Abierta) {
        Fallar "Twinx Express esta abierto (puerto $puertoAplicacion en uso). Cerra la ventana negra y volve a ejecutar el instalador."
    }
    $actualizador = if ($EjecutableActualizador) { $EjecutableActualizador } else { $exeOrigen }
    # El programa termina con "Presiona Enter": se le entrega un Enter por la entrada estandar.
    $ErrorActionPreference = 'Continue'
    cmd /c "echo.| `"$actualizador`" --actualizar `"$destinoParaCmd`""
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($codigo -ne 0) {
        Fallar "La actualizacion no se completo (codigo $codigo). La instalacion anterior sigue como estaba; revisa el mensaje de arriba."
    }
} else {
    Write-Host "[2/4] Instalacion nueva en $destinoCompleto..."
    if (-not (Test-Path -LiteralPath $destinoCompleto -PathType Container)) {
        New-Item -ItemType Directory -Path $destinoCompleto -Force | Out-Null
    } elseif (@(Get-ChildItem -LiteralPath $destinoCompleto -Force).Count -gt 0) {
        Fallar "La carpeta $destinoCompleto existe y no esta vacia, pero no tiene KioscoApp.exe. Elegi otra carpeta o vaciala."
    }
    Get-ChildItem -LiteralPath $origen -Force | Where-Object { $_.Name -ne 'Instalar.ps1' -and $_.Name -ne 'Instalar.bat' } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $destinoCompleto -Recurse -Force
    }
    # El instalador tambien viaja con el programa: documenta como se instalo y sirve para instalar
    # una copia en otra carpeta o PC (desde la instalacion no se reinstala sobre si misma).
    Copy-Item -LiteralPath (Join-Path $origen 'Instalar.ps1') -Destination $destinoCompleto -Force
    Copy-Item -LiteralPath (Join-Path $origen 'Instalar.bat') -Destination $destinoCompleto -Force
    # Cierre operativo (F3): el desinstalador viaja con el programa (quita el programa, nunca los datos).
    foreach ($desinstalador in @('Desinstalar.ps1', 'Desinstalar.bat')) {
        if (Test-Path -LiteralPath (Join-Path $origen $desinstalador) -PathType Leaf) {
            Copy-Item -LiteralPath (Join-Path $origen $desinstalador) -Destination $destinoCompleto -Force
        }
    }
    if (-not (Test-Path -LiteralPath $exeDestino -PathType Leaf)) {
        Fallar 'La copia termino sin KioscoApp.exe en el destino.'
    }
}

# ---------------------------------------------------------------------------
# 3. Accesos directos
# ---------------------------------------------------------------------------
$accesos = @()
if ($SinAccesosDirectos) {
    Write-Host '[3/4] Accesos directos: omitidos.'
} else {
    Write-Host '[3/4] Accesos directos...'
    if ($CarpetaAccesosDirectos) {
        $accesos += Crear-AccesoDirecto $CarpetaAccesosDirectos $exeDestino
    } else {
        $accesos += Crear-AccesoDirecto ([Environment]::GetFolderPath('Desktop')) $exeDestino
        $accesos += Crear-AccesoDirecto (Join-Path ([Environment]::GetFolderPath('Programs')) $nombreAcceso) $exeDestino
    }
    foreach ($acceso in $accesos) { Write-Host "  + $acceso" }
}

# ---------------------------------------------------------------------------
# 4. Resumen
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '[4/4] LISTO' -ForegroundColor Green
Write-Host "  Programa : $destinoCompleto"
Write-Host "  Datos    : $env:LOCALAPPDATA\KioscoApp  (no se tocaron; se crean en la primera ejecucion)"
if ($hayInstalacion) {
    Write-Host '  Se actualizo una instalacion existente. Para volver atras: KioscoApp.exe --versiones / --rollback (ver LEEME.txt).'
} else {
    Write-Host '  Primera ejecucion: abri "Twinx Express" (acceso directo) o KioscoApp.exe; se te pedira crear el usuario del dueno.'
}
Write-Host '  Manual: menu Ayuda dentro de la aplicacion, o MANUAL.md en la carpeta del programa.'
exit 0
