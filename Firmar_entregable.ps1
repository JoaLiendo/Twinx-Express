<#
.SYNOPSIS
    Firma digitalmente KioscoApp.exe del entregable (Authenticode) y verifica la firma. Sin certificado,
    informa que el ejecutable NO esta firmado y termina con error.

.DESCRIPTION
    Procedimiento completo en docs\FIRMA_EJECUTABLE.md. Requiere un certificado de firma de codigo
    autentico (OV o EV, emitido por una autoridad certificadora reconocida por Windows) y signtool.exe
    (Windows SDK). Este script no genera certificados ni "firma de prueba": una firma autofirmada no sirve
    para distribuir (SmartScreen y el antivirus la tratan igual o peor que un ejecutable sin firmar).

.PARAMETER Entregable
    Carpeta del entregable (por defecto dist\KioscoApp junto a este script).

.PARAMETER Huella
    Thumbprint (SHA-1) del certificado instalado en el almacen del usuario actual (Cert:\CurrentUser\My).
    Recomendado con certificados EV en token USB: la clave privada nunca sale del dispositivo.

.PARAMETER Pfx
    Ruta a un archivo .pfx con el certificado y su clave (alternativa a -Huella). La contrasena se pide
    en pantalla; nunca va por linea de comandos ni queda en ningun archivo.

.PARAMETER ServidorTimestamp
    Servidor RFC 3161 para el sello de tiempo: la firma sigue valida cuando el certificado vence.

.PARAMETER SoloVerificar
    No firma: solo muestra el estado de la firma actual (Valid / NotSigned / ...).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Firmar_entregable.ps1 -Huella 0123ABCD...
    powershell -ExecutionPolicy Bypass -File .\Firmar_entregable.ps1 -SoloVerificar
#>
[CmdletBinding()]
param(
    [string]$Entregable = (Join-Path $PSScriptRoot 'dist\KioscoApp'),
    [string]$Huella = '',
    [string]$Pfx = '',
    [string]$ServidorTimestamp = 'http://timestamp.digicert.com',
    [switch]$SoloVerificar
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fallar([string]$mensaje) {
    Write-Host ''
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

$exe = Join-Path $Entregable 'KioscoApp.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    Fallar "No existe $exe. Construi el entregable primero (Construir_entregable.ps1)."
}

function Estado-Firma([string]$ruta) {
    $firma = Get-AuthenticodeSignature -LiteralPath $ruta
    $firmante = if ($firma.SignerCertificate) { $firma.SignerCertificate.Subject } else { '-' }
    return @{ Estado = $firma.Status.ToString(); Firmante = $firmante; Mensaje = $firma.StatusMessage }
}

function Buscar-Signtool {
    $comando = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $raices = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "$env:ProgramFiles\Windows Kits\10\bin")
    foreach ($raiz in $raices) {
        if (Test-Path -LiteralPath $raiz) {
            $candidatos = Get-ChildItem -LiteralPath $raiz -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -like '*\x64\signtool.exe' } | Sort-Object FullName -Descending
            if ($candidatos) { return $candidatos[0].FullName }
        }
    }
    return $null
}

if (-not $SoloVerificar) {
    if (-not $Huella -and -not $Pfx) {
        $actual = Estado-Firma $exe
        Write-Host "Estado actual de la firma: $($actual.Estado)" -ForegroundColor Yellow
        Fallar 'No se indico ningun certificado (-Huella o -Pfx). El ejecutable NO esta firmado: ver docs\FIRMA_EJECUTABLE.md para obtener un certificado de firma de codigo.'
    }
    $signtool = Buscar-Signtool
    if (-not $signtool) {
        Fallar 'No se encontro signtool.exe. Instala el Windows SDK (componente "Windows SDK Signing Tools for Desktop Apps").'
    }
    $argumentos = @('sign', '/fd', 'SHA256', '/td', 'SHA256', '/tr', $ServidorTimestamp, '/d', 'Twinx Express')
    if ($Huella) {
        $argumentos += @('/sha1', $Huella)
    } else {
        if (-not (Test-Path -LiteralPath $Pfx -PathType Leaf)) { Fallar "No existe el archivo $Pfx." }
        $clave = Read-Host -AsSecureString 'Contrasena del .pfx'
        $texto = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($clave))
        $argumentos += @('/f', $Pfx, '/p', $texto)
    }
    $argumentos += $exe
    Write-Host "Firmando $exe..."
    $ErrorActionPreference = 'Continue'
    & $signtool @argumentos
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($codigo -ne 0) { Fallar "signtool termino con codigo $codigo." }
}

$resultado = Estado-Firma $exe
Write-Host ''
Write-Host "Archivo  : $exe"
Write-Host "Firma    : $($resultado.Estado)"
Write-Host "Firmante : $($resultado.Firmante)"
if ($resultado.Estado -ne 'Valid') {
    Write-Host "Detalle  : $($resultado.Mensaje)"
    Write-Host 'EL EJECUTABLE NO TIENE UNA FIRMA VALIDA.' -ForegroundColor Red
    exit 1
}
Write-Host 'FIRMA VALIDA' -ForegroundColor Green
exit 0
