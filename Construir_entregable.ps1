<#
.SYNOPSIS
    Construye el entregable de Twinx Express: dist\KioscoApp.

.DESCRIPTION
    Proceso reproducible: limpia SOLO los artefactos de build anteriores,
    ejecuta PyInstaller con KioscoApp.spec, copia los archivos de entrega
    (LEEME.txt y Restaurar_backup.bat) y valida activamente el resultado.

    Nunca toca data\, backups\ ni el codigo fuente. Solo borra:
      - build\KioscoApp
      - dist\KioscoApp
    Cualquier otra carpeta dentro de dist\ (por ejemplo builds historicos)
    se deja como esta y se avisa: no forma parte del entregable.

    Termina con codigo 0 si el build es valido y con codigo distinto de 0
    si algo falla o si dist\KioscoApp contiene datos prohibidos (bases de
    datos, ZIPs, data\, imagenes_productos\, backups\).

.PARAMETER SoloValidar
    No limpia, no compila y no copia nada: solo ejecuta la validacion
    sobre el dist\KioscoApp que ya exista. Sirve para comprobar que la
    defensa realmente rechaza un bundle contaminado.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Construir_entregable.ps1
#>
[CmdletBinding()]
param(
    [switch]$SoloValidar
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$raiz = $PSScriptRoot
$dirBuild = Join-Path $raiz 'build\KioscoApp'
$dirDist = Join-Path $raiz 'dist'
$dirEntregable = Join-Path $dirDist 'KioscoApp'
$archivosDeEntrega = @('LEEME.txt', 'Restaurar_backup.bat')

# Ruta relativa (a dist\KioscoApp) de los unicos ZIP legitimos del bundle.
$zipsPermitidos = @('_internal\base_library.zip')
$directoriosProhibidos = @('data', 'imagenes_productos', 'backups')
$patronesArchivoProhibido = @('*.db', '*.db-*', '*.sqlite', '*.sqlite3', '*.zip')

function Fallar([string]$mensaje) {
    Write-Host ''
    Write-Host "ERROR: $mensaje" -ForegroundColor Red
    exit 1
}

function Eliminar-ArtefactoDeBuild([string]$ruta) {
    # Guarda: solo se borra exactamente uno de los dos artefactos conocidos.
    $permitidas = @($dirBuild, $dirEntregable)
    if ($permitidas -notcontains $ruta) {
        Fallar "Se intento borrar una ruta no permitida: $ruta"
    }
    if (Test-Path -LiteralPath $ruta) {
        Write-Host "  Eliminando artefacto anterior: $ruta"
        Remove-Item -LiteralPath $ruta -Recurse -Force
    }
}

# ---------------------------------------------------------------------------
# 1. Validar que se esta ejecutando desde el repo correcto
# ---------------------------------------------------------------------------
Write-Host '[1/5] Validando el repositorio...'
Set-Location -LiteralPath $raiz

$requeridosEnRepo = @('KioscoApp.spec', 'lanzador.py', 'config.py', 'LEEME.txt', 'Restaurar_backup.bat', 'db\seed\catalogo_inicial.json')
foreach ($nombre in $requeridosEnRepo) {
    if (-not (Test-Path -LiteralPath (Join-Path $raiz $nombre) -PathType Leaf)) {
        Fallar "Falta '$nombre' en $raiz. Este script debe vivir en la raiz del repositorio."
    }
}
$dirMigracionesFuente = Join-Path $raiz 'db\migraciones'
if (-not (Test-Path -LiteralPath $dirMigracionesFuente -PathType Container)) {
    Fallar "Falta la carpeta db\migraciones en $raiz."
}
$migracionesFuente = @(Get-ChildItem -LiteralPath $dirMigracionesFuente -Filter '*.sql' -File | ForEach-Object { $_.Name } | Sort-Object)
if ($migracionesFuente.Count -eq 0) {
    Fallar 'db\migraciones no contiene ningun script .sql.'
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fallar "No se encontro 'python' en el PATH."
}
& python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fallar 'PyInstaller no esta instalado (pip install -r requirements-build.txt).'
}

if (-not $SoloValidar) {
    # ---------------------------------------------------------------------------
    # 2. Limpiar unicamente los artefactos de build anteriores
    # ---------------------------------------------------------------------------
    Write-Host '[2/5] Limpiando artefactos de build anteriores (build\KioscoApp y dist\KioscoApp)...'
    Eliminar-ArtefactoDeBuild $dirBuild
    Eliminar-ArtefactoDeBuild $dirEntregable

    # ---------------------------------------------------------------------------
    # 3. PyInstaller
    # ---------------------------------------------------------------------------
    Write-Host '[3/5] Ejecutando PyInstaller (KioscoApp.spec)...'
    # PyInstaller escribe su progreso por stderr: se relaja $ErrorActionPreference
    # solo durante la llamada nativa y se decide por el codigo de salida.
    $ErrorActionPreference = 'Continue'
    & python -m PyInstaller KioscoApp.spec --noconfirm --clean
    $codigoPyInstaller = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($codigoPyInstaller -ne 0) {
        Fallar "PyInstaller fallo (codigo $codigoPyInstaller)."
    }
    if (-not (Test-Path -LiteralPath $dirEntregable -PathType Container)) {
        Fallar "PyInstaller termino sin generar $dirEntregable."
    }

    # ---------------------------------------------------------------------------
    # 4. Copiar los archivos de entrega
    # ---------------------------------------------------------------------------
    Write-Host '[4/5] Copiando archivos de entrega...'
    foreach ($nombre in $archivosDeEntrega) {
        Copy-Item -LiteralPath (Join-Path $raiz $nombre) -Destination $dirEntregable -Force
        Write-Host "  + $nombre"
    }
}

# ---------------------------------------------------------------------------
# 5. Validar el contenido final de dist\KioscoApp
# ---------------------------------------------------------------------------
Write-Host '[5/5] Validando dist\KioscoApp...'
if (-not (Test-Path -LiteralPath $dirEntregable -PathType Container)) {
    Fallar "No existe $dirEntregable."
}
$problemas = New-Object System.Collections.Generic.List[string]

# 5a. Lo que tiene que estar.
$requeridosEnEntregable = @(
    @{ Ruta = 'KioscoApp.exe'; Tipo = 'Leaf' },
    @{ Ruta = '_internal'; Tipo = 'Container' },
    @{ Ruta = 'LEEME.txt'; Tipo = 'Leaf' },
    @{ Ruta = 'Restaurar_backup.bat'; Tipo = 'Leaf' },
    @{ Ruta = '_internal\interfaces\web\templates'; Tipo = 'Container' },
    @{ Ruta = '_internal\interfaces\web\static'; Tipo = 'Container' },
    @{ Ruta = '_internal\db\migraciones'; Tipo = 'Container' },
    @{ Ruta = '_internal\db\seed\catalogo_inicial.json'; Tipo = 'Leaf' }
)
foreach ($requerido in $requeridosEnEntregable) {
    $ruta = Join-Path $dirEntregable $requerido.Ruta
    if (-not (Test-Path -LiteralPath $ruta -PathType $requerido.Tipo)) {
        $problemas.Add("Falta '$($requerido.Ruta)' en dist\KioscoApp.")
    }
}

# 5b. Migraciones completas: mismos nombres que db\migraciones del repo.
$dirMigracionesBuild = Join-Path $dirEntregable '_internal\db\migraciones'
if (Test-Path -LiteralPath $dirMigracionesBuild -PathType Container) {
    $migracionesBuild = @(Get-ChildItem -LiteralPath $dirMigracionesBuild -Filter '*.sql' -File | ForEach-Object { $_.Name } | Sort-Object)
    $diferencias = @(Compare-Object -ReferenceObject $migracionesFuente -DifferenceObject $migracionesBuild)
    if ($diferencias.Count -gt 0) {
        $problemas.Add("Las migraciones del build no coinciden con db\migraciones (repo: $($migracionesFuente.Count), build: $($migracionesBuild.Count)).")
    }
}

# 5b-bis. Catalogo inicial: el archivo del bundle debe ser identico al del repo.
$catalogoRepo = Join-Path $raiz 'db\seed\catalogo_inicial.json'
$catalogoBuild = Join-Path $dirEntregable '_internal\db\seed\catalogo_inicial.json'
if (Test-Path -LiteralPath $catalogoBuild -PathType Leaf) {
    $hashRepo = (Get-FileHash -LiteralPath $catalogoRepo -Algorithm SHA256).Hash
    $hashBuild = (Get-FileHash -LiteralPath $catalogoBuild -Algorithm SHA256).Hash
    if ($hashRepo -ne $hashBuild) {
        $problemas.Add('El catalogo inicial del build (_internal\db\seed\catalogo_inicial.json) no coincide con db\seed\catalogo_inicial.json del repo.')
    }
}

# 5c. Lo que NO tiene que estar (busqueda recursiva, sin confiar en el .spec).
$prefijo = $dirEntregable.TrimEnd('\') + '\'
$elementos = @(Get-ChildItem -LiteralPath $dirEntregable -Recurse -Force)
foreach ($elemento in $elementos) {
    $relativa = $elemento.FullName.Substring($prefijo.Length)
    if ($elemento.PSIsContainer) {
        if ($directoriosProhibidos -contains $elemento.Name) {
            $problemas.Add("Directorio prohibido: $relativa")
        }
        continue
    }
    foreach ($patron in $patronesArchivoProhibido) {
        if ($elemento.Name -like $patron) {
            if ($zipsPermitidos -notcontains $relativa) {
                $problemas.Add("Archivo prohibido: $relativa")
            }
            break
        }
    }
}

if ($problemas.Count -gt 0) {
    Write-Host ''
    Write-Host 'BUILD INVALIDO: dist\KioscoApp NO debe entregarse.' -ForegroundColor Red
    foreach ($problema in $problemas) {
        Write-Host "  - $problema" -ForegroundColor Red
    }
    exit 1
}

# Otras carpetas dentro de dist\ no forman parte del entregable: solo se avisa.
$otrasEnDist = @(Get-ChildItem -LiteralPath $dirDist -Force | Where-Object { $_.Name -ne 'KioscoApp' })
if ($otrasEnDist.Count -gt 0) {
    Write-Host ''
    Write-Host 'AVISO: dist\ contiene elementos que NO forman parte del entregable (no se tocaron):' -ForegroundColor Yellow
    foreach ($otra in $otrasEnDist) {
        Write-Host "  - dist\$($otra.Name)" -ForegroundColor Yellow
    }
    Write-Host 'Entregar unicamente dist\KioscoApp.' -ForegroundColor Yellow
}

$archivos = @($elementos | Where-Object { -not $_.PSIsContainer })
$tamanoMB = [math]::Round((($archivos | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Host ''
Write-Host 'BUILD VALIDO' -ForegroundColor Green
Write-Host "  Entregable : $dirEntregable"
Write-Host "  Archivos   : $($archivos.Count) ($tamanoMB MB)"
Write-Host "  Migraciones: $($migracionesFuente.Count) (coinciden con db\migraciones)"
Write-Host '  Sin bases de datos, ZIPs, data\, imagenes_productos\ ni backups\.'
exit 0
