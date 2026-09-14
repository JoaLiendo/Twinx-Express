# CLAUDE.md — Directrices del proyecto "Kiosco Stock App"

Este archivo define las reglas de trabajo para cualquier sesión de Claude Code en este repositorio. Tiene prioridad sobre el comportamiento por defecto.

## 1. Contexto del proyecto

Sistema de control de stock para un kiosco.

- **Fase actual:** Beta local (single-user, sin red).
- **Persistencia:** SQLite (archivo local, sin servidor de base de datos).
- **Entrada de datos:** lector de código de barras (USB, emula teclado) + búsqueda manual por nombre/código.
- **Alcance funcional inicial:** alta/baja/modificación de productos, control de stock, registro de ventas, control básico de caja (apertura, cierre, movimientos).
- **Futuro:** migración a interfaz web local (misma base de datos / misma capa de lógica, cambia solo la capa de presentación). Por eso la lógica de negocio **no debe depender de la interfaz** (ni de consola ni de framework web).

## 2. Idioma y documentación

- Todo el código (docstrings, comentarios, mensajes de commit, nombres de excepciones personalizadas) se documenta **en español**.
- Los identificadores (nombres de variables, funciones, clases, tablas y columnas) se escriben **en inglés** siguiendo convenciones estándar de Python (PEP 8), salvo términos de dominio sin traducción natural (ej. `kiosco` puede quedar como está si así lo prefiere el usuario). Ante la duda, priorizar consistencia con el resto del código ya escrito.
- Docstrings breves y útiles: explican **qué hace** una función pública y **por qué** existe una decisión no obvia (no explican lo obvio).
- No generar comentarios que narren el código línea por línea.

## 3. Arquitectura y modularidad

- Separación estricta en capas:
  - `db/`: acceso a datos (conexión, esquema, migraciones, queries). Es la única capa que importa `sqlite3` directamente.
  - `models/` o `domain/`: entidades y reglas de negocio puras (sin SQL, sin I/O).
  - `services/`: casos de uso que orquestan `db/` + `domain/` (ej. `registrar_venta`, `actualizar_stock`).
  - `interfaces/` (o `cli/` en esta fase): capa de presentación. Hoy es consola/CLI; en el futuro será web. **No debe contener lógica de negocio ni SQL.**
- Ninguna función de `services/` o `interfaces/` ejecuta SQL directo: siempre pasa por `db/`.
- Funciones cortas, con una responsabilidad clara. Evitar módulos "god object".
- No introducir abstracciones, capas o flags de configuración para necesidades hipotéticas futuras. Resolver el problema actual.

## 4. Patrones seguros para SQLite

- **Una única función/módulo centraliza la creación de conexiones** (ej. `db/conexion.py`), para poder ajustar configuración (`PRAGMA foreign_keys = ON`, `journal_mode`, etc.) en un solo lugar.
- **Usar siempre context managers (`with`)** para conexiones y cursores, de forma que la conexión se cierre y el commit/rollback ocurra automáticamente:
  ```python
  with sqlite3.connect(RUTA_DB) as conexion:
      conexion.execute("PRAGMA foreign_keys = ON")
      cursor = conexion.cursor()
      cursor.execute(consulta, parametros)
      # el commit es automático al salir del bloque `with` si no hubo excepción
  ```
- **Transacciones explícitas para operaciones multi-paso** (ej. registrar una venta que descuenta stock de varios productos y crea un movimiento de caja): todo debe ocurrir dentro de una sola transacción, con `rollback()` explícito ante cualquier error, para no dejar la base de datos en estado inconsistente.
- **Nunca concatenar strings para construir SQL.** Usar siempre parámetros (`?` placeholders) para prevenir SQL injection, incluso siendo una app local.
- **Claves foráneas activadas** (`PRAGMA foreign_keys = ON`) en cada conexión, ya que SQLite las tiene desactivadas por defecto.
- Definir el esquema mediante scripts de migración versionados (`db/migraciones/`), no mediante `CREATE TABLE IF NOT EXISTS` disperso por el código.
- Las funciones de `db/` reciben y devuelven tipos simples o dataclasses, nunca cursores ni conexiones abiertas hacia capas superiores.

## 5. Manejo de excepciones (estándar profesional)

- Excepciones específicas y con significado de dominio (ej. `StockInsuficienteError`, `ProductoNoEncontradoError`), definidas en un módulo propio (`excepciones.py`), heredando de una excepción base del proyecto.
- **Nunca capturar `Exception` genérica de forma silenciosa.** Capturar la excepción más específica posible; si se re-lanza, preservar la traza original (`raise NuevaExcepcion(...) from error`).
- Los errores de `sqlite3` (`sqlite3.IntegrityError`, `sqlite3.OperationalError`, etc.) se capturan en la capa `db/` y se traducen a excepciones de dominio antes de propagarse hacia arriba. La capa de servicios/interfaz no debe conocer detalles de SQLite.
- Ninguna operación de caja o stock puede fallar en silencio: todo error se registra (logging) y se comunica al usuario de forma clara.
- Usar el módulo `logging` estándar de Python (no `print`) para trazabilidad, con niveles apropiados (`INFO` para operaciones normales, `WARNING`/`ERROR` para problemas).

## 6. Estilo de código

- Tipado estático con `typing` en todas las funciones públicas (parámetros y retorno).
- PEP 8. Preferir `pathlib` sobre manejo de rutas con strings.
- Sin código muerto, sin funciones sin usar, sin parches de compatibilidad hacia atrás innecesarios (proyecto nuevo, sin usuarios en producción todavía).
- Tests con `pytest` para la capa `db/` y `services/` como mínimo (usar una base de datos SQLite en memoria o un archivo temporal para tests, nunca la base de datos real).

## 7. Qué NO hacer

- No mezclar lógica de presentación (inputs de consola, prints de formato) dentro de `services/` o `db/`.
- No usar variables globales mutables para el estado de la caja o del stock; pasar el estado explícitamente o encapsularlo en una clase de servicio.
- No hardcodear rutas absolutas de la base de datos: usar una configuración centralizada (ej. `config.py` con la ruta relativa al proyecto).
