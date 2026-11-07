# Firma del ejecutable (V1.18-D)

Estado: **BLOQUEADO por un requisito externo**. `KioscoApp.exe` **no está firmado**. Firmarlo requiere un
certificado de firma de código auténtico emitido a nombre de la persona o empresa que distribuye Twinx Express;
no se puede generar localmente ni reemplazar por un certificado autofirmado (Windows SmartScreen y los antivirus
tratan una firma autofirmada igual o peor que la ausencia de firma). Nada en el repositorio afirma ni simula que el
ejecutable esté firmado.

## Qué hace falta (una sola vez)

1. **Certificado de firma de código** (Code Signing), de una autoridad reconocida por Windows (DigiCert, Sectigo,
   GlobalSign, SSL.com, Certum, etc.). Dos tipos:
   - **OV** (validación de organización o individual): más barato; SmartScreen muestra la advertencia «editor
     desconocido» hasta que el certificado acumula reputación con descargas.
   - **EV** (validación extendida): reputación inmediata en SmartScreen; la clave vive en un token USB o en un HSM
     en la nube y no se puede copiar.
   Desde 2023 todas las CA exigen que la clave privada esté en hardware (token/HSM) también para OV.
2. **Validación de identidad** ante la CA: documento de identidad o constancia de la empresa (CUIT/razón social),
   verificación telefónica o por correo. Tarda de días a semanas.
3. **signtool.exe**: viene con el Windows SDK (componente «Windows SDK Signing Tools for Desktop Apps»).

Esto es una compra o suscripción anual y una gestión de identidad: queda fuera de lo que se puede hacer desde el
código. Hasta tenerlo, el entregable se distribuye sin firma y hay que documentarle al cliente la advertencia de
SmartScreen («Más información → Ejecutar de todas formas»).

## Cómo firmar cuando se tenga el certificado

Con el entregable construido (`Construir_entregable.ps1`) y el certificado instalado en el almacén del usuario
(o el token conectado):

```powershell
# Ver la huella (thumbprint) del certificado instalado:
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Format-List Subject, Thumbprint, NotAfter

# Firmar y verificar (sello de tiempo incluido: la firma sigue válida cuando el certificado venza):
powershell -ExecutionPolicy Bypass -File .\Firmar_entregable.ps1 -Huella <THUMBPRINT>

# Alternativa con archivo .pfx (la contraseña se pide en pantalla, nunca va en la línea de comandos):
powershell -ExecutionPolicy Bypass -File .\Firmar_entregable.ps1 -Pfx C:\ruta\certificado.pfx
```

`Firmar_entregable.ps1` ejecuta `signtool sign /fd SHA256 /td SHA256 /tr <timestamp>` sobre `dist\KioscoApp\KioscoApp.exe`
y después verifica con `Get-AuthenticodeSignature`: termina con código 0 solo si el estado es `Valid`. Sin
certificado, informa `NotSigned` y termina con código 1: **no existe un modo que “simule” la firma**.

Se firma únicamente `KioscoApp.exe` (el ejecutable propio). Las DLL de Python y de las dependencias dentro de
`_internal\` ya vienen firmadas por sus autores o no se modifican.

## Cómo verificar (cliente o soporte)

```powershell
Get-AuthenticodeSignature "C:\KioscoApp\KioscoApp.exe" | Format-List Status, StatusMessage, SignerCertificate
# o
powershell -ExecutionPolicy Bypass -File .\Firmar_entregable.ps1 -SoloVerificar -Entregable C:\KioscoApp
```

`Status = Valid` y el firmante esperado en `SignerCertificate.Subject`. También en Propiedades del archivo →
pestaña «Firmas digitales».

## Qué NO hacer

- No usar `New-SelfSignedCertificate` ni certificados de prueba para distribuir: no mejoran nada para el cliente y
  pueden empeorar la detección del antivirus.
- No versionar ni copiar `.pfx`, claves ni contraseñas en el repositorio ni en el entregable
  (`Construir_entregable.ps1` rechaza `.pfx`, `.p12` y `.key` dentro de `dist\KioscoApp`).
- No afirmar en LEEME, manual ni changelog que el ejecutable está firmado hasta que `-SoloVerificar` devuelva
  `Valid` sobre el entregable real.
