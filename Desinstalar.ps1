<#
.SYNOPSIS
    Desinstala el programa Twinx Express (cierre operativo, F3) SIN tocar los datos comerciales.

.DESCRIPTION
    Quita la carpeta del programa (por defecto C:\KioscoApp) y los accesos directos "Twinx Express" del
    Escritorio y del menu Inicio. NUNCA borra la carpeta de datos del comercio (%LOCALAPPDATA%\KioscoApp:
    base de datos, imagenes, backups, versiones archivadas y material fiscal): queda intacta y el script
    informa donde esta. Si mas adelante se reinstala el programa, vuelve a abrir esos mismos datos.

    No requiere permisos de administrador. Se niega a desinstalar si la aplicacion esta abierta, si la carpeta
    indicada no contiene KioscoApp.exe o si apunta a los datos. No ejecuta ninguna eliminacion automatica de
    datos: borrar los datos es una decision manual y deliberada del dueno, fuera de este script.

.PARAMETER Destino
    Carpeta donde esta instalado el programa. Por defecto C:\KioscoApp.

.PARAMETER CarpetaAccesosDirectos
    Carpeta de accesos directos a limpiar en lugar del Escritorio y el menu Inicio (pruebas).

.PARAMETER Si
    No pedir confirmacion (uso en pruebas o desatendido).

.EXAMPLE
    .\Desinstalar.ps1
#>

param(
    [string]$Destino = 'C:\KioscoApp',
    [string]$CarpetaAccesosDirectos = '',
    [switch]$Si
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
    try {
        $cliente = New-Object System.Net.Sockets.TcpClient
        $cliente.Connect('127.0.0.1', $puertoAplicacion)
        $cliente.Close()
        return $true
    } catch {
        return $false
    }
}

Write-Host '=== Twinx Express - Desinstalacion del programa ==='
Write-Host ''

$destinoCompleto = [System.IO.Path]::GetFullPath($Destino)
if ($destinoCompleto.Length -gt 3) { $destinoCompleto = $destinoCompleto.TrimEnd('\') }
$exe = Join-Path $destinoCompleto 'KioscoApp.exe'
$datos = Join-Path $env:LOCALAPPDATA 'KioscoApp'

# Guardias: solo se desinstala una carpeta que es, de verdad, el programa (y nunca la de datos).
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    Fallar "En $destinoCompleto no hay un KioscoApp.exe: no es una instalacion de Twinx Express. No se toco nada."
}
if ((Normalizar $destinoCompleto) -eq (Normalizar $datos) -or (Normalizar $destinoCompleto).StartsWith((Normalizar $datos) + '\')) {
    Fallar "La carpeta indicada es la de DATOS del comercio ($datos). Este script nunca la borra. No se toco nada."
}
if (((Normalizar $datos)).StartsWith((Normalizar $destinoCompleto) + '\')) {
    Fallar "La carpeta de datos ($datos) esta dentro del programa: no se puede desinstalar sin arriesgar los datos. No se toco nada."
}
if (Aplicacion-Abierta) {
    Fallar 'Twinx Express esta abierto (puerto 8000 en uso). Cerralo y volve a ejecutar la desinstalacion. No se toco nada.'
}

if (-not $Si) {
    Write-Host "Se va a quitar el programa de: $destinoCompleto"
    Write-Host "Se van a quitar los accesos directos '$nombreAcceso' del Escritorio y del menu Inicio."
    Write-Host "NO se tocan los datos del comercio: $datos"
    Write-Host ''
    $respuesta = Read-Host 'Escribi SI para continuar'
    if ($respuesta -ne 'SI') { Write-Host 'Cancelado. No se toco nada.'; exit 0 }
}

# 1. Accesos directos (solo los nuestros, por nombre).
$carpetas = @()
if ($CarpetaAccesosDirectos) {
    $carpetas += $CarpetaAccesosDirectos
} else {
    $carpetas += [Environment]::GetFolderPath('Desktop')
    $carpetas += (Join-Path ([Environment]::GetFolderPath('Programs')) $nombreAcceso)
}
foreach ($carpeta in $carpetas) {
    $acceso = Join-Path $carpeta "$nombreAcceso.lnk"
    if (Test-Path -LiteralPath $acceso -PathType Leaf) {
        Remove-Item -LiteralPath $acceso -Force
        Write-Host "  - $acceso"
    }
    if ((Split-Path -Leaf $carpeta) -eq $nombreAcceso -and (Test-Path -LiteralPath $carpeta -PathType Container)) {
        if (@(Get-ChildItem -LiteralPath $carpeta -Force).Count -eq 0) { Remove-Item -LiteralPath $carpeta -Force }
    }
}

# 2. Carpeta del programa (y nada mas).
Write-Host "[1/1] Quitando el programa de $destinoCompleto..."
Remove-Item -LiteralPath $destinoCompleto -Recurse -Force
if (Test-Path -LiteralPath $destinoCompleto) {
    Fallar "No se pudo quitar completamente $destinoCompleto (archivos en uso?). Los datos siguen intactos en $datos."
}

Write-Host ''
Write-Host '=== LISTO: programa desinstalado ==='
Write-Host "  Datos conservados : $datos"
Write-Host '                      (base de datos, imagenes, backups, versiones archivadas y material fiscal)'
Write-Host '  Para volver a usarlos: instala Twinx Express de nuevo con Instalar.bat; abre esos mismos datos.'
Write-Host '  Para borrar los datos definitivamente: hacelo a mano y a conciencia (este script nunca lo hace).'
exit 0
