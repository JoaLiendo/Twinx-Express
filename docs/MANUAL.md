# Manual de Twinx Express

Este manual describe únicamente lo que la aplicación hace hoy. La misma ayuda está integrada en la aplicación
(menú **Ayuda**). Los apartados de compras, proveedores, reportes, analítica, backup y configuración son del
dueño (OWNER); el cajero (CASHIER) vende, cobra, abre y cierra la caja, consulta el stock y cuenta inventario.

## Guía rápida

1. **Abrir la caja** en *Caja* con el fondo inicial. Sin caja abierta no se puede vender.
2. **Vender** en *Ventas*: escaneá el código de barras (el cursor ya está en el lector) o buscá por nombre, elegí el
   medio de pago y tocá *Cobrar*. Con efectivo, cargá el monto recibido para ver el vuelto.
3. **Ingresar mercadería** en *Compras › Nueva*: proveedor, productos, cantidades, costo y, si corresponde,
   vencimientos. El stock y el costo se actualizan solos.
4. **Cerrar la caja** al final del día: contá el efectivo y confirmá. Se hace un backup automático.
5. **Revisar** el *Dashboard* (stock crítico, vencimientos, últimos 30 días) y, como dueño, *Analítica* para ver qué
   conviene revisar.

## Primeros pasos

**Instalación y primer arranque**

- La carpeta del programa se instala con `Instalar.bat` (o copiándola a `C:\KioscoApp`). Se abre con `KioscoApp.exe`
  o con el acceso directo: aparece una ventana negra (no la cierres mientras trabajás) y el navegador con la aplicación.
- Tus datos no viven en la carpeta del programa: están en `%LOCALAPPDATA%\KioscoApp` (base de datos, imágenes,
  backups, registro). Actualizar el programa no los toca.
- La primera vez se crea el usuario del **dueño**. Después, cada persona entra con su usuario y contraseña (mínimo de
  seguridad obligatorio).

**Usuarios y permisos**

- *Empleados* (dueño): alta de cajeros, cambio de rol, desactivación. Cambiar una contraseña cierra las sesiones de
  ese usuario en otros navegadores.
- El cajero no ve costos, márgenes, compras, proveedores, reportes, analítica, auditoría ni configuración.
- *Mi cuenta* (ícono con la inicial, arriba a la derecha): cambiar la propia contraseña.

**Catálogo**

- *Stock › Nuevo producto*: código de barras, nombre, costo, precio, stock inicial, mínimo, categoría (hasta tres
  niveles), unidad, etiquetas e imagen. También se puede importar un CSV/XLSX desde *Stock › Importar* y exportar el
  catálogo.
- *Stock › Categorías y etiquetas*: árbol de categorías y etiquetas planas.
- Un producto con precio 0 aparece como «Precio no configurado» y no se puede vender hasta cargarle precio.

## Ventas

- **Lector de código de barras**: el cursor vuelve solo al campo del lector después de cada acción. Escanear agrega
  una unidad; escanear de nuevo suma otra.
- **Búsqueda manual**: escribí parte del nombre o del código; ↑ ↓ resaltan un producto y Enter lo agrega. Si la
  búsqueda deja un único resultado, Enter también lo agrega. Esc quita el resaltado, luego limpia los filtros y por
  último vuelve al lector. Los filtros por categoría y por stock no afectan al lector.
- **Carrito**: + y − cambian la cantidad; el tacho quita la línea. No se puede superar el stock disponible ni vender
  sin stock.
- **Medios de pago**: efectivo (con monto recibido y vuelto), tarjeta, transferencia, otro, y *a cuenta* para clientes
  con crédito (ver Clientes). Una venta a cuenta puede ser mixta: parte en efectivo, el resto a cuenta.
- **Mercadería vencida**: si una venta solo se puede completar con unidades vencidas, el sistema pide autorización del
  dueño (su usuario y contraseña en el puesto del cajero) y lo registra en la auditoría.
- **Después de cobrar**: se puede imprimir el ticket interno y, si la facturación ARCA está configurada, la factura;
  *Nueva venta* deja todo listo para el siguiente cliente.
- **Historial** (*Ventas › Ver historial*): detalle de cada venta, ticket e impresión. El dueño puede **anular** una
  venta activa con un motivo: el stock vuelve y, si fue en efectivo, se registra el egreso de caja. Las ventas a cuenta
  no se anulan; las fiscalizadas se cancelan con nota de crédito desde el panel fiscal.
- **Borrador de factura** (dueño): desde el detalle de una venta, *Ver borrador de la factura* muestra lo que se
  emitiría, marcado «BORRADOR — SIN VALIDEZ FISCAL», sin número, CAE ni QR. No emite ni consulta a ARCA.

## Pedidos

- Un **pedido de cliente** es lo que alguien encarga para retirar o recibir después: cliente (o un nombre de contacto),
  productos y cantidades, fecha de entrega y notas. Lo toman y lo venden ambos roles.
- Tomar un pedido **no reserva ni descuenta stock, no cobra ni factura**. Los precios se guardan solo como referencia:
  rigen los del momento de la venta.
- *Vender en el POS* abre el punto de venta con el carrito ya cargado (hasta donde el stock de hoy lo permite) y avisa
  si un producto cambió de precio, no tiene stock suficiente o fue dado de baja. Al cobrar, el pedido queda
  **convertido** en esa venta y no se puede vender dos veces; un reintento devuelve la misma venta.
- Un pedido pendiente se **cancela** con motivo (queda en el historial y en la auditoría). Nada se borra.
- No es una compra a proveedor ni una sugerencia de reposición: eso sigue en *Compras* y *Reposición*.

## Caja

- **Abrir** con el fondo inicial. Mientras está abierta se pueden registrar *ingresos* y *egresos* manuales con
  descripción.
- **Arqueo**: en cualquier momento muestra cuánto efectivo debería haber (fondo + ventas en efectivo + cobros +
  ingresos − egresos − pagos a proveedores desde caja).
- **Cerrar**: contá el efectivo real, el sistema calcula la diferencia y la guarda. Al cerrar se hace un backup
  automático; si fallara, la caja queda cerrada igual y aparece una alerta hasta que un backup salga bien.
- Cada sesión de caja queda en *Reportes › Caja por sesión*.

## Inventario

**Stock**

- *Stock* lista los productos con filtros por texto, categoría (incluye subcategorías), estado de stock, vencimiento y
  etiqueta. Desde cada fila: editar, movimientos (kardex), vencimientos y eliminar/dar de baja (un producto con ventas
  no se borra: se desactiva y se puede reactivar desde «Dados de baja»).
- **Stock mínimo**: por debajo del mínimo el producto aparece como crítico en el dashboard y entra en *Reposición*.
- **Ajustes de stock** (dueño, desde *Movimientos*): baja por vencimiento, rotura, robo o falla de fábrica, siempre
  con motivo; un ajuste erróneo se revierte (una sola vez) y queda trazado. El costo de la pérdida es el de la partida
  de la que salió.

**Partidas y vencimientos**

- Cada ingreso de mercadería crea una partida con su costo y, si se cargó, su vencimiento. Las ventas descuentan
  primero lo que vence antes (FEFO).
- El dashboard avisa de productos *próximos a vencer* (días configurables en *Configuración*) y *vencidos*. Desde
  *Vencimientos* de un producto se retira la mercadería vencida.
- *Precios › Liquidación* (dueño) sugiere un precio para lo próximo a vencer; nunca cambia un precio sin confirmación.

**Inventario físico**

- *Inventario › Nuevo*: conteo a ciegas (no se ve el stock del sistema mientras se cuenta). El dueño revisa las
  diferencias y confirma: se generan ajustes con motivo «Ajuste de inventario». Si un producto se movió después de
  contarlo, hay que volver a contarlo antes de confirmar.

**Reposición**

- *Reposición* agrupa por proveedor principal lo que está por debajo del mínimo. Cada fila explica el cálculo («¿Cómo se
  calculó?»): faltante hasta el mínimo, rotación de los últimos 30 días, cobertura, vencimientos y calificación del
  proveedor. Podés cambiar cantidades, pasar la lista al formulario de compra o imprimirla. Nunca registra una compra
  sola.

## Compras

- *Compras › Nueva*: proveedor, líneas con cantidad y costo unitario, observaciones, condición **contado** (con origen
  del pago: caja o fuera de caja) o **crédito** (con vencimiento opcional). Cada línea admite vencimiento de la
  mercadería (una fecha o varias con `cantidad@fecha`).
- Al confirmar, el stock sube, el costo vigente del producto pasa a ser el de la compra y queda en el historial de
  precios. Un reenvío del mismo formulario no duplica la compra.
- El listado filtra por proveedor, fechas, estado y producto; el detalle muestra líneas, vencimientos y partidas. Una
  compra se puede **anular** con motivo si el stock todavía lo permite; a crédito, revierte el cargo al proveedor si no
  hubo pagos posteriores.
- El precio de venta no cambia solo al comprar: se ajusta en *Precios* (actualización masiva por categoría, con vista
  previa) o editando el producto.
- **Comprobante del proveedor** (opcional): tipo (factura, remito, ticket, otro), número tal como figura en el papel y
  fecha. No es el número interno de la compra (ese es «#…»). Un mismo proveedor no puede tener dos compras activas con
  el mismo comprobante; se busca por ese número en el historial y se corrige desde el detalle (queda auditado).

## Gastos y resultado

- *Gastos* (dueño): alquiler, electricidad, agua, Internet, servicios, salarios, mantenimiento, transporte, comisiones,
  impuestos y tasas, u «otros» con descripción obligatoria. Cada gasto lleva fecha, importe, medio de pago, referencia,
  responsable y observaciones.
- **No cargues mercadería como gasto**: su costo se reconoce al vender (margen bruto); contarla también como gasto la
  duplicaría.
- Si se paga **con efectivo de la caja abierta**, se registra el egreso en la caja en la misma operación. Un reenvío del
  formulario no duplica nada.
- Lo económico (fecha, importe, medio de pago) no se edita: un gasto equivocado se **anula** con motivo (si salió de la
  caja, decidís si el efectivo vuelve) y se carga otro. Lo descriptivo (categoría, descripción, referencia,
  responsable, observaciones) se corrige y queda auditado.
- *Analítica › Resultado*: ventas, anulaciones, margen bruto, gastos por categoría y **resultado operativo estimado**
  (margen bruto − gastos) por día, semana o mes, con CSV. Es una estimación: no descuenta impuestos, pérdidas de stock
  ni unidades sin costo conocido. El dashboard del dueño muestra los gastos y el resultado de los últimos 30 días.

## Proveedores

- Ficha con datos de contacto, datos fiscales opcionales (CUIT único), categorías que provee, productos vinculados (y
  cuál es el principal, con su último costo), compras, saldo y cuenta a pagar.
- **Pagos**: en efectivo desde la caja o por transferencia; nunca más que el saldo. La deuda se reparte por antigüedad
  como estimación (vencida, próxima, vigente, sin vencimiento).
- **Evaluaciones** de 1 a 5 estrellas con comentario obligatorio (no se editan) y **reclamos** con estados (pendiente,
  reconocido, resuelto, rechazado) vinculados a una compra o a un ajuste por falla de fábrica.
- Un proveedor dado de baja conserva su historia y se puede reactivar; no se le pueden cargar compras nuevas.

## Clientes

- Persona o empresa (con razón social, CUIT y hasta dos compradores autorizados). Ambos roles pueden crear clientes y
  cobrar; editar y desactivar es del dueño.
- **Cuenta corriente**: una venta a cuenta carga el saldo; los *cobros* en efectivo entran a la caja y se imputan a las
  ventas más viejas (el dueño puede imputar a mano).
- **Crédito**: nadie tiene crédito hasta que el dueño define una política en *Configuración* (general, por tipo de
  cliente o individual). El estado se calcula por antigüedad de la deuda: normal, regular (más de 30 días), demorado
  (más de 45: requiere autorización temporal del dueño para vender a cuenta) y crítica (60 o más: crédito cerrado hasta
  que el dueño lo reabra). Tres cierres seguidos sin regularizar vetan el crédito.
- La ficha muestra saldo, estado, movimientos, obligaciones pendientes y el historial de crédito.
- **DNI** (opcional, solo personas): 7 u 8 dígitos; sirve para buscar al cliente y evitar fichas duplicadas. A los
  clientes históricos no se les inventa ninguno.

## Reportes y analítica

**Reportes (dueño)**

- *Ventas* por período (facturación, ticket promedio, medios de pago, por día, por vendedor, rentabilidad, anulaciones)
  y valorización del inventario a costo.
- *Caja por sesión*, *Compras por proveedor*, *Cuenta corriente* (deuda y cobranzas), *Ventas por cliente* (ventas,
  ticket promedio, cada cuántos días compra, deuda y cobros de cada cliente identificado), *Deuda a proveedores*,
  *Rotación de stock* y *Pérdidas de stock* (por motivo, producto, categoría y mes). Casi todos exportan a CSV.
- *Auditoría*: quién hizo qué y cuándo, paginada y exportable.

**Analítica (dueño)**

- *Resumen*: últimos 30 días contra los 30 anteriores, stock que necesita atención y **decisiones a revisar hoy**
  (márgenes negativos, costos que subieron sin ajustar el precio, clientes críticos o demorados, proveedores con deuda
  vencida o con seguimiento, reposición pendiente).
- *Ventas* por día, semana o mes con comparaciones; *Margen bruto* por producto, categoría o período distinguiendo costo
  de referencia, costo real de partidas y unidades sin costo conocido; *Precios y costos* desde el historial real;
  *Deuda* de clientes por antigüedad y a proveedores por vencimiento; *Proveedores* (compras, calificación, reclamos,
  costos, dependencia).
- *Resultado*: margen bruto menos gastos operativos por período (ver *Gastos y resultado*).
- Todo se calcula con datos registrados. Nada es un pronóstico; el margen bruto no es ganancia neta.

## Facturación electrónica

- **Comprobante interno**: toda venta imprime un ticket que dice «COMPROBANTE INTERNO · NO VÁLIDO COMO FACTURA». No
  es una factura y no reemplaza la obligación de emitir comprobantes por un medio autorizado.
- **Estado de preparación** (*Fiscal*, dueño): lista qué falta para emitir facturas electrónicas y quién lo resuelve:
  perfil del emisor, certificado del ambiente (ausente, vencido, por vencer o vigente), punto de venta cargado y
  **validado por ARCA**, tablas de ARCA, información fiscal de los productos, conectividad (se prueba con un botón,
  nunca sola), homologación y habilitación de producción (trámite externo y del técnico), comprobantes inciertos.
  Que un dato esté cargado no lo da por válido.
- **Homologación**: mientras el ambiente sea *homologación*, lo que se emite lleva la marca «SIN VALIDEZ FISCAL». La
  homologación real con ARCA y la habilitación de producción están pendientes y son externas a Twinx.
- **Comprobantes autorizados** (*Fiscal › Comprobantes autorizados*): listado informativo por período con totales y
  CSV. **No es el Libro IVA Ventas**: no valida tratamientos tributarios ni incluye comprobantes emitidos fuera de
  Twinx; las notas de crédito se informan aparte.
- **Borrador**: vista previa sin validez fiscal desde el detalle de la venta (ver *Ventas*).

## Backup

- Se hace un backup **al cerrar la caja**, **al abrir la aplicación** si el último tiene más de 24 horas y **antes de
  cada actualización**. El dueño puede crear uno cuando quiera en *Configuración › Backup*.
- **Backup diario a una hora fija** (opcional): en *Backup* se elige la hora; se hace solo si Twinx está abierto a esa
  hora (no se instala ningún servicio ni tarea de Windows: con la computadora apagada no hay backup) y no se repite si
  ya hubo uno después de esa hora.
- Cada backup es un ZIP con la base de datos y las imágenes, verificado antes de darse por bueno. Se guardan en
  `%LOCALAPPDATA%\KioscoApp\backups`: 14 diarios, 8 semanales, 12 mensuales, los últimos 10 manuales y los 3 previos a
  actualizaciones.
- **Segundo destino** (muy recomendado): una carpeta en otro disco, pendrive o carpeta sincronizada con la nube. Cada
  backup se copia y se verifica ahí; si el disco no está, el backup principal se hace igual y la página avisa.
- El material fiscal (certificados y claves) no viaja en los backups: se vuelve a importar en una instalación nueva.

## Restauración, actualización, vuelta atrás y desinstalación

- **Restaurar** (con la aplicación cerrada): doble clic en `Restaurar_backup.bat` y arrastrar el ZIP. Antes de
  reemplazar nada se guarda una copia de seguridad de los datos actuales; un backup dañado o de una versión más nueva
  no se restaura. Si hay comprobantes fiscales que el backup no conoce, el restore se bloquea: lo concilia el soporte.
- **Custodia de la historia fiscal**: Twinx guarda, fuera de la carpeta de datos, qué comprobantes ya informó a ARCA.
  Si la base se reemplaza por una copia anterior por fuera del procedimiento (versión vieja del programa, copia
  manual), la aplicación **no inicia** y explica cómo recuperar la base; el técnico puede aceptar la pérdida con
  `KioscoApp.exe --aceptar-historia-fiscal` (queda auditado).
- **Desinstalar**: doble clic en `Desinstalar.bat` **de la carpeta instalada** (por ejemplo `C:\KioscoApp`; pide
  escribir SI y muestra qué carpeta va a quitar) quita esa carpeta del programa y los accesos
  directos, y **nunca borra los datos**: la base, las imágenes, los backups, las versiones archivadas y el material
  fiscal quedan en `%LOCALAPPDATA%\KioscoApp` y el script lo informa. Reinstalar vuelve a abrir esos mismos datos.
  Borrar los datos es una decisión manual y deliberada, fuera de Twinx.
- **Actualizar** (soporte): `Instalar.bat` desde la carpeta de la versión nueva detecta la instalación y ejecuta la
  actualización con backup previo verificado, copia de la versión anterior y migración de la base. Si la migración
  falla, vuelve sola a la versión anterior.
- **Volver a la versión anterior** (soporte): `KioscoApp.exe --rollback <versión>` desde la copia conservada en
  `%LOCALAPPDATA%\KioscoApp\versiones`; restaura el programa y los datos de antes de actualizar.
  `KioscoApp.exe --versiones` lista lo conservado.

## Solución de problemas

- **«Twinx Express ya está abierto»**: hay otra ventana del programa usando la misma base. Usá esa ventana o entrá a
  `http://127.0.0.1:8000`. Solo puede correr una instancia por base de datos.
- **El navegador no abre**: con la ventana negra abierta, entrá a `http://127.0.0.1:8000`. Si el puerto está ocupado
  por otro programa, cerralo o reiniciá la computadora.
- **«La caja está cerrada»** al vender: abrila en *Caja*.
- **El lector no agrega productos**: hacé clic en el campo del lector (o Esc desde la búsqueda) y volvé a escanear;
  verificá que el producto exista con ese código.
- **Alerta de backup fallido**: revisá espacio en disco y permisos, creá un backup manual desde *Backup*; si vuelve a
  fallar, pedí soporte.
- **La aplicación no arranca o muestra «base de datos dañada»**: no borres nada; restaurá el backup más reciente con
  `Restaurar_backup.bat` o contactá al soporte con el registro (`%LOCALAPPDATA%\KioscoApp\logs\twinx_express.log`).
- **Facturación ARCA**: una venta queda «incierta» cuando ARCA no respondió; se concilia sola al reiniciar o desde el
  panel fiscal. Mientras una esté incierta no se emite la siguiente. No reinstales ni restaures datos sin consultar al
  soporte.
- **«La base de datos actual NO contiene comprobantes fiscales…»** al abrir: la base fue reemplazada por una copia
  anterior fuera del procedimiento seguro. No sigas: restaurá el backup de tipo SEGURIDAD más reciente (o la carpeta
  `data_reemplazada_*`) con `Restaurar_backup.bat`, o pedí al soporte que evalúe `--aceptar-historia-fiscal`.
- **Pedir ayuda**: *Configuración › Soporte* muestra la versión, dónde están los datos y permite descargar un
  diagnóstico (sin datos de negocio ni contraseñas) para enviar al soporte técnico.

## Asistente

**Twinx Asistente** es el panel de chat que se abre con el botón *Asistente* (arriba a la derecha, en cualquier
pantalla). Es de **solo lectura**: consulta datos ya registrados y explica cómo usar el programa; nunca registra
ventas, compras, pagos ni cambios de stock o precios.

- **Qué podés preguntar**: cuánto vendiste hoy (o ayer, esta semana, este mes), qué productos se vendieron más,
  cuáles están por vencer, qué tenés que reponer, qué productos tienen margen negativo, qué proveedores tienen
  reclamos abiertos, cuánto te deben los clientes, cuánto debés a proveedores y cómo se hace algo (registrar una
  compra, restaurar un backup, cerrar la caja...). Cada respuesta trae accesos a la pantalla relacionada.
- **Permisos**: el cajero obtiene solo lo que ve en sus pantallas (ventas de su caja, vencimientos, deuda total de
  clientes y guías); costos, márgenes, reposición, proveedores y reportes son del dueño, también en el chat.
- **Sin IA, con datos reales**: las preguntas frecuentes se responden con la *ayuda integrada* (texto del sistema,
  sin inteligencia artificial). Cada respuesta indica su origen.
- **IA local opcional** (dueño): si en la computadora hay un motor Ollama (https://ollama.com) con un modelo
  descargado, en *Configuración › Asistente* se lo puede activar para preguntas libres. La conexión es solo local
  (127.0.0.1): nada sale de la máquina, no hay suscripciones ni cuentas. Twinx no instala Ollama ni modelos. El
  panel muestra «IA local conectada», «IA local no disponible» o «Ayuda integrada»; si el motor está apagado, la
  ayuda integrada sigue funcionando.
- **Historial**: la conversación se guarda solo en la pestaña del navegador y se borra con *Borrar* o al cerrarla.
