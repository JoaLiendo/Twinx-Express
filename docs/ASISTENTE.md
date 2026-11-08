# Twinx Asistente (V1.18)

Asistente conversacional de **solo lectura** integrado en la interfaz web de Twinx Express. Vive en un panel
lateral disponible en todas las pantallas, responde preguntas frecuentes del comerciante con datos reales del
sistema y, si el dueño lo configura, puede delegar preguntas libres en un modelo de lenguaje **local** (Ollama).
Nunca ejecuta acciones ni envía datos fuera de la máquina.

## Qué hace y qué no

| Hace | No hace |
|---|---|
| Responde cuánto se vendió, qué se vendió más, qué vence, qué reponer, qué productos tienen margen negativo, qué proveedores tienen reclamos, cuánto deben los clientes y cuánto se debe a proveedores. | Registrar ventas, compras, pagos, movimientos de caja, anulaciones, cambios de precio o stock, operaciones fiscales, restore ni rollback. |
| Explica cómo hacer algo (registrar una compra, restaurar un backup, cerrar la caja...) con el texto del manual. | Inventar cifras: toda cifra sale de una herramienta de consulta; si falta el dato, lo dice. |
| Lleva a la pantalla relacionada con enlaces definidos por el servidor. | Fabricar URLs, ejecutar SQL, leer archivos ni usar herramientas generales. |
| Funciona completo sin ningún motor de IA (ayuda integrada). | Depender de servicios remotos, suscripciones o telemetría. |

## Arquitectura

```
navegador  ──POST /api/asistente/preguntar──▶  interfaces/web/rutas/asistente.py
   ▲                                                 │
   │ JSON (texto, origen, acciones)                  ▼
   │                                      services/asistente/motor.py
   │                         ┌───────────────────────┼─────────────────────────┐
   │                         ▼                       ▼                         ▼
   │          domain/asistente.py          services/asistente/        services/asistente/
   │          (intenciones, períodos,      herramientas.py            proveedores.py
   │           permisos por rol)           (consultas validadas)      (Ollama local / falso)
   │                                               │
   │                                               ▼
   │                       servicios existentes: reportes, analítica, stock, partidas,
   │                       caja, clientes, proveedores, deuda a proveedores
   └─────────────────────────────────────────────────────────────────────────────────
```

- **`domain/asistente.py`**: reglas puras. La tabla `PERMISOS` dice qué rol usa cada herramienta
  (deny-by-default). `detectar_intencion` reconoce las preguntas frecuentes sin ningún modelo.
  `resolver_periodo` traduce "hoy", "esta semana", "este mes"... a fechas inclusivas.
- **`services/asistente/herramientas.py`**: diez herramientas de consulta. Cada una reutiliza un servicio
  existente, valida sus argumentos contra un esquema cerrado (tipos y `enum`), acota las filas
  (`LIMITE_FILAS = 10`) y sanea los textos del comercio antes de devolverlos. `ejecutar()` verifica el
  permiso **siempre**, venga el pedido de la ayuda integrada o del modelo.
- **`services/asistente/guia.py`**: respuestas fijas de "¿cómo hago...?" tomadas de `docs/MANUAL.md`, con
  enlace a la sección de la Ayuda. Un tema del dueño se lo dice al cajero en vez de explicárselo.
- **`services/asistente/proveedores.py`**: interfaz `ProveedorIA` y dos implementaciones: `ProveedorOllama`
  (API REST local, `urllib`, sin dependencias nuevas) y `ProveedorFalso` (guion determinista para tests).
- **`services/asistente/configuracion.py`**: motor, URL, modelo, timeout y `num_ctx` en la tabla
  `configuracion`, auditados. La URL solo puede ser loopback.
- **`services/asistente/motor.py`**: orquestación (ver abajo), cache de 30 s del estado del motor y el
  bucle de herramientas con el modelo (máximo 3 rondas).
- **`interfaces/web/rutas/asistente.py`**: `GET /api/asistente/estado` y `POST /api/asistente/preguntar`
  (ambos roles). La configuración vive en `/configuracion/asistente` (solo OWNER).
- **`interfaces/web/static/js/asistente.js`** y el panel en `base.html`: chat con historial corto en
  `sessionStorage` de la pestaña (atado al usuario, con botón Borrar), preguntas sugeridas según la pantalla,
  estado del motor, errores de sesión/conexión, accesos a reportes y cierre con botón o Esc. Todo se inserta
  con `textContent`; los enlaces se aceptan solo si son rutas internas.

## Orden de resolución de una pregunta

1. **Ayuda integrada** (`origen = integrado`): si la pregunta se reconoce, se ejecuta la herramienta y se
   responde con texto redactado por el sistema. Determinista, sin modelo, sin cifras inventadas.
2. **IA local** (`origen = ia_local`): si no se reconoce y hay un motor configurado y disponible, el modelo
   recibe la pregunta, el historial corto y **solo las herramientas del rol**. Cada herramienta que pide se
   valida y ejecuta en el servidor. Si el modelo pide una herramienta prohibida, recibe un error y el usuario
   ve una advertencia.
3. **Sin respuesta**: se listan las preguntas que sí puede responder y se enlaza la Ayuda.

El panel distingue los tres estados: *IA local conectada · modelo*, *IA local no disponible · ayuda
integrada* y *Ayuda integrada*. Cada respuesta indica su origen; una respuesta fija nunca se presenta como IA.

## Permisos

| Herramienta | OWNER | CASHIER |
|---|---|---|
| ventas_del_periodo (total, ticket promedio, medios de pago) | ✔ | ✖ |
| ventas_de_la_caja (sesión de caja, sin costos) | ✔ | ✔ |
| productos_mas_vendidos | ✔ | ✖ |
| productos_por_vencer | ✔ | ✔ |
| reposicion_sugerida (incluye costos) | ✔ | ✖ |
| margen_negativo | ✔ | ✖ |
| proveedores_con_reclamos | ✔ | ✖ |
| deuda_de_clientes (total para ambos; antigüedad y deudores solo OWNER) | ✔ | ✔ |
| deuda_a_proveedores | ✔ | ✖ |
| guia_de_uso | ✔ | ✔ |

El modelo nunca ve las herramientas que el rol no puede usar, y aunque las nombrara, `ejecutar()` las rechaza.

## Motor de IA local (opcional)

- Adaptador para la API de Ollama (`/api/tags` para detectar, `/api/chat` con `tools` y `stream: false`).
- Conexión **exclusivamente local**: la URL debe apuntar a `127.0.0.1`, `localhost` o `::1`.
- Límites: `num_ctx` (1024–32768, 4096 por defecto), `num_predict = 400`, `temperature = 0.1`,
  `keep_alive = 5m` (libera la RAM del modelo al dejar de usarlo), timeout configurable (5–120 s, 30 por
  defecto) y 2 s para la detección.
- Sin claves, sin telemetría, sin descargas automáticas: Twinx no instala Ollama ni modelos. El dueño elige
  el modelo instalado en su máquina.
- El ejecutable no incrusta ningún modelo: PyInstaller no cambia.

## Seguridad

- Solo local, de verdad: el adaptador usa un *opener* propio sin proxy (el de urllib por defecto manda incluso
  `127.0.0.1` por `HTTP_PROXY` o el proxy de Windows) y sin seguir redirecciones; la URL se valida con puerto
  1–65535 y sin caracteres raros. Antes de dar el motor por disponible consulta `/api/show`: un modelo sin la
  capacidad `tools` no sirve y se informa.
- Una conversación con la IA por vez (semáforo): una pregunta libre puede tardar hasta 4 × timeout y ocupar un
  hilo del servidor; la segunda en paralelo recibe «la IA está ocupada» y la ayuda integrada. A lo sumo tres
  llamadas a herramientas por turno del modelo; argumentos que no son un objeto se rechazan, no se vacían.
- Un solo sondeo del motor a la vez: mientras uno comprueba, los demás reciben el último estado conocido.
- Sesión y rol: las rutas exigen sesión; el rol se lee de la cookie en el servidor.
- Prompt: los nombres de productos, clientes y proveedores se sanean (sin saltos de línea ni caracteres de
  control, 80 caracteres) y el sistema le indica al modelo que son datos, no instrucciones.
- Salida: el navegador nunca interpreta HTML de la respuesta; las acciones son enlaces internos validados.
- Prompt y textos: se quitan caracteres de control y de formato invisible (bidi, ancho cero), no solo los
  saltos de línea.
- Historial: solo en `sessionStorage` de la pestaña, atado al id de usuario; al iniciar el panel con otro
  usuario se borra el del anterior y la página de login borra todos; nunca se persiste en el servidor ni se
  registra en el log (el log guarda códigos HTTP y rutas del motor, nunca cuerpos ni conversaciones).
- Riesgos aceptados y documentados: (1) la app no tiene tokens CSRF y confía en `SameSite=Lax`; otro origen
  del mismo host (`http://127.0.0.1:<otro puerto>`) podría enviar el formulario de configuración del asistente,
  igual que cualquier otro formulario del dueño; (2) un cajero puede crear un cliente con un nombre que intente
  dar instrucciones al modelo: llega saneado y acotado, no hay herramientas de escritura ni enlaces fabricados
  por el modelo, así que solo puede afectar la redacción de una respuesta de IA, nunca la ayuda integrada.
- Secretos: la configuración del asistente no guarda claves; el material fiscal y las contraseñas quedan
  fuera de toda herramienta.

## Limitaciones de esta versión

- Las respuestas de la IA local dependen del modelo instalado; con modelos chicos puede no usar la
  herramienta correcta. La ayuda integrada cubre las preguntas objetivo sin IA.
- Sin historial persistente entre sesiones ni entre dispositivos (decisión deliberada).
- No hay acciones de escritura. Cualquier capacidad futura de ese tipo requerirá una propuesta visible y
  confirmación explícita fuera del modelo.
