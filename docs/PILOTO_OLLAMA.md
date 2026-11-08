# Piloto de IA local para Twinx Asistente (Ollama)

Plan para la primera prueba real del motor de IA local. **No instala ni descarga nada**: Ollama y el modelo los
instala Joaquín cuando lo autorice. Hasta entonces el asistente funciona completo con la ayuda integrada.

## 1. Hardware relevado (2026-11-07, máquina de desarrollo)

| Recurso | Valor |
|---|---|
| CPU | AMD Ryzen 7 7735HS, 8 núcleos / 16 hilos, 3,2 GHz |
| RAM | 15,2 GB instalados; 5,7 GB libres con el entorno de desarrollo abierto |
| GPU dedicada | NVIDIA GeForce RTX 4050 Laptop, 6 GB VRAM (5,8 GB libres), driver 617.42 |
| GPU integrada | AMD Radeon 680M (compartida, no se usa para el piloto) |
| Disco C: | 146,8 GB libres de 474,7 GB |
| Sistema | Windows 11 Home 10.0.26300 |
| Ollama | No instalado (sin ejecutable, sin `%USERPROFILE%\.ollama`, puerto 11434 sin respuesta) |
| Modelos locales | Ninguno |
| Configuración del asistente | Sin claves `asistente.*` en la base: modo «ayuda integrada» por defecto |

Según la documentación de Ollama vigente: Windows 10 22H2 o más nuevo; GPU NVIDIA con compute capability 5.0+ y
driver 550 o más nuevo (la RTX 4050 es Ada Lovelace, 8.9, con driver 617: compatible). Los modelos se guardan en
`C:\Users\<usuario>\.ollama\models` (configurable con `OLLAMA_MODELS`). Ollama escucha en `127.0.0.1:11434` por
defecto, que es lo único que el asistente acepta.

Las requisitos de Ollama para esta máquina están cubiertos: la RTX 4050 corre modelos de 3–4 B parámetros en
cuantización Q4 enteramente en VRAM (≈2–3 GB) y hasta 7–8 B con descarga parcial a CPU.

## 2. Modelo recomendado

El asistente usa `tools` (llamadas a herramientas), así que el modelo **tiene que tener la capacidad `tools`** en
Ollama (`ollama show <modelo>` la lista; si falta, `/api/chat` responde 400 «does not support tools» y el panel
muestra «IA local no disponible»).

| Opción | Tamaño en disco | Memoria estimada en uso (num_ctx 4096) | Por qué |
|---|---|---|---|
| **`qwen2.5:3b` (recomendado para empezar)** | ≈1,9 GB | ≈2,5–3 GB VRAM | Sigue bien instrucciones en español, soporta `tools`, entra sobrado en la RTX 4050 y responde en ≈1–3 s. |
| `llama3.2:3b` (alternativa) | ≈2,0 GB | ≈2,5–3 GB VRAM | Es el modelo con el que se documentó el tool calling de Ollama; español algo más pobre. |
| `qwen2.5:7b` (si la calidad de 3 B no alcanza) | ≈4,7 GB | ≈5–6 GB VRAM | Entra justo en 6 GB; con el POS y el navegador abiertos puede descargar capas a CPU y perder velocidad. |

No se recomiendan modelos de 13 B o más en esta máquina (no entran en VRAM y en CPU superan el timeout de 30 s).
Los modelos «thinking» (por ejemplo `qwen3`) funcionan, pero su razonamiento alarga la latencia y no aporta a
consultas de kiosco; si se prueban, dejar `think` apagado (el adaptador no lo activa).

Requisitos del piloto: Ollama para Windows instalado por Joaquín, el modelo descargado con `ollama pull
qwen2.5:3b`, y en Twinx *Configuración › Asistente*: motor «IA local con Ollama», modelo `qwen2.5:3b`, dirección
`http://127.0.0.1:11434`, tiempo máximo 30 s, contexto 4096. «Probar conexión» tiene que decir «IA local
conectada · qwen2.5:3b».

## 3. Qué medir y cómo

`python scripts/piloto_ollama.py detectar` imprime el relevamiento de arriba en la máquina donde corra (sin red).
`python scripts/piloto_ollama.py medir` ejecuta el guion de preguntas contra el motor configurado sobre una base
temporal sembrada con datos conocidos y escribe un informe JSON y una tabla. Con Ollama apagado el guion termina
informando «motor no disponible» y no mide nada.

| Medición | Cómo | Umbral aceptable |
|---|---|---|
| Carga inicial del modelo | Primera llamada a `/api/chat` con `messages: []` (carga sin generar) cronometrada | ≤ 15 s |
| Latencia por pregunta | Tiempo de `motor.responder` por cada pregunta del guion (p50 y p95) | p95 ≤ 10 s con 3 B |
| RAM y CPU | `WorkingSet` del proceso `ollama` (tasklist) y del servidor de Twinx antes, durante y después; uso de CPU medio durante el guion | Twinx ≤ +20 MB; Ollama libera el modelo tras `keep_alive` (5 min) |
| Selección correcta de herramientas | Para cada pregunta libre del guion, la herramienta que pidió el modelo contra la esperada | ≥ 8 de 10 correctas |
| Preguntas no reconocidas | Preguntas fuera de alcance (clima, chistes, política): el modelo debe decir que no tiene el dato, sin cifras | 0 cifras inventadas |
| Permisos | El mismo guion con un CASHIER: ninguna respuesta con costos, márgenes, reposición ni proveedores; las herramientas ofrecidas al modelo son solo las del rol | 0 fugas |
| POS y asistente a la vez | Una venta por el POS (E2E `01`–`04`) mientras corre el guion; la venta no debe tardar más de lo habitual ni fallar | Sin errores; venta < 2 s |

Cada corrida deja `build/piloto-ollama-<fecha>.json` (fuera del control de versiones) con hardware, modelo,
latencias, herramientas elegidas y memoria. El resultado se vuelca en `PROGRESS.md` como «Piloto Ollama».

## 4. Criterios de salida del piloto

- GO para ofrecer la IA local como opción documentada: p95 ≤ 10 s, ≥ 8/10 herramientas correctas, 0 fugas de
  permisos, 0 cifras inventadas en preguntas fuera de alcance, POS sin degradación.
- NO-GO: cualquier fuga de permisos o cifra inventada (se corrige en el prompt o se restringe la IA a preguntas
  de guía), o latencia inaceptable (probar `llama3.2:3b` o bajar `num_ctx` a 2048 antes de descartar).
