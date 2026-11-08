# Integración controlada de V1.17 y V1.18

Estado de ramas al cierre de la certificación (2026-11-07). Ninguna rama estable se mueve sin autorización de
Joaquín; este documento deja los comandos listos y dice qué verifica cada uno.

## Qué contiene cada rama

```
main 21d2268 ─ dev/v1.16 1dcfb4e (RC5 fiscal, congelada)
                └─ dev/v1.17 1ff7229 ─ seguridad y trazabilidad (contraseñas, auditoría, errores web)
                     └─ feature/v1.17-instancia-unica e6bce49
                          └─ feature/v1.17-analitica 14035b4  (certificada: suite completa 6020 tests)
                               ├─ fb3ef95 analítica del dueño ............................. V1.17
                               ├─ 2471501 design system compilado sin CDN .................. V1.17-B
                               ├─ 435bf7c evolución visual POS/dashboard/inventario ....... V1.17-B
                               ├─ 6395b59 instalador, ayuda integrada y soporte ........... V1.18-A/B/C
                               ├─ db38bbc, bef0668 evidencia y script de firma ............ V1.18-D
                               ├─ 6f1bae8, 56595bd, 14035b4 auditoría, fixes y certificación
                               └─ feature/v1.18-asistente-ia  (Twinx Asistente, esta rama)
```

`feature/v1.17-analitica` no es «solo analítica»: desde `6395b59` lleva también el instalador, la ayuda y el
soporte (V1.18-A/B/C/D) y `version.py` dice `1.17.0`. Los commits de corrección (`56595bd`) tocan ambas
partes. Separarlos a esta altura exigiría reescribir historia (cherry-picks y un nuevo árbol sin certificar),
así que la integración propuesta trata a `14035b4` como **el corte certificado de la versión 1.17.0 completa**
(analítica + design system + instalador + ayuda + soporte), y al asistente como la siguiente versión.

## Opción recomendada

1. **`dev/v1.17` avanza por fast-forward a `14035b4`** (es descendiente directo: `1ff7229` → `e6bce49` → ...).
   Nada se reescribe y la rama queda exactamente en el estado que pasó la suite completa.
2. **`feature/v1.18-asistente-ia` se mantiene separada** hasta terminar el piloto de Ollama; después se crea
   `dev/v1.18` desde su commit certificado (hoy `307815e`: suite completa 6262 tests sin fallos, E2E 58 + 32, build real válido).
3. `main`, `dev/v1.16` y los tags `v1.16.0-rc*` no se tocan: la 1.16 espera la homologación con ARCA.

### Comandos (a ejecutar por Joaquín, fuera de Claude)

```bash
# 0. Verificar que es un fast-forward limpio (debe imprimir 1ff7229, el HEAD actual de dev/v1.17)
git merge-base dev/v1.17 feature/v1.17-analitica

# 1. Avanzar dev/v1.17 sin reescribir nada (falla si no fuera fast-forward)
git switch dev/v1.17
git merge --ff-only feature/v1.17-analitica
git push origin dev/v1.17

# 2. Comprobar que quedó en el commit certificado
git rev-parse --short dev/v1.17            # 14035b4
git log --oneline 1ff7229..dev/v1.17 | wc -l  # 10 commits

# 3. Volver a la rama del asistente
git switch feature/v1.18-asistente-ia
```

`--ff-only` es la garantía de seguridad: si alguien hubiera movido `dev/v1.17`, el merge se niega en vez de
crear un commit de fusión. No usar `git branch -f` ni `reset --hard`.

## Opción alternativa (no recomendada)

Dejar `dev/v1.17` en `435bf7c` (último commit «puro» V1.17) y crear `dev/v1.18` en `14035b4`. Desventajas:
`435bf7c` no tiene las correcciones de analítica de `56595bd` ni pasó la suite completa como corte, y el
instalador (V1.18-A) es necesario para distribuir la 1.17.0 de todos modos.

## Qué queda pendiente para la primera release comercial

| Pendiente | Bloquea | Depende de |
|---|---|---|
| Homologación real con ARCA (WSAA, FEDummy, puntos de venta, FECAESolicitar) con certificado del emisor | Facturar desde Twinx | Trámite externo del comercio; no es código |
| Certificado de firma de código para `KioscoApp.exe` (`docs/FIRMA_EJECUTABLE.md`) | Instalación sin advertencia de SmartScreen | Compra/gestión externa |
| Piloto de Ollama (`docs/PILOTO_OLLAMA.md`) | Ofrecer la IA local como opción documentada; sin ella el asistente igual funciona | Autorización para instalar Ollama y un modelo |
| Decisión de alcance del tag `v1.17.0` | Release | Joaquín |

Con los dos primeros pendientes resueltos fuera del código, el corte `14035b4` ya es distribuible como 1.17.0
(build real validado, instalación, actualización y rollback probados con ejecutables).
