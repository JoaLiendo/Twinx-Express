"""Twinx Asistente (V1.18): asistente conversacional de solo lectura sobre los servicios existentes.

- `herramientas`: consultas con argumentos validados, filas acotadas y permisos por rol.
- `guia`: respuestas fijas de "¿cómo hago...?" tomadas del manual.
- `proveedores`: adaptador de Ollama local y proveedor falso para tests.
- `configuracion`: motor, modelo y límites elegidos por el dueño.
- `motor`: la orquestación (ayuda integrada primero; IA local si está configurada y disponible).
"""
