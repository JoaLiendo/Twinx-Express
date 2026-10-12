# ROADMAP.md — Twinx Express V1.11–V1.18

> Roadmap vivo, no retrospectivo (el detalle de lo ya implementado por versión está en `README.md`,
> `LEEME.txt` y `PROGRESS.md`). Se actualiza a medida que cada versión se define o se cierra.
> V1.14 en adelante es **provisional**: orden y alcance pueden cambiar según lo que surja en V1.11–V1.13.

## V1.11 — Trazabilidad de ajustes de stock

- **V1.11-A (en curso)**: nuevos motivos manuales de pérdida (`VENCIMIENTO`, `ROTURA`, `ROBO`,
  `FALLA_FABRICA`, todos solo-resta); `MERMA`/`PERDIDA`/`OTRO` pasan a legacy (solo lectura de
  historial); `RECUENTO` sigue siendo el código interno del ajuste de inventario físico (se
  muestra como "Ajuste de inventario" en UI); snapshot nullable de `costo_unitario_centavos` por
  ajuste (migración 027).
- **V1.11-B (a definir)**: reversas explícitas de un ajuste manual erróneo (sin reinterpretar un
  `RECUENTO` como corrección). Diseño preparado en V1.11-A pero no implementado.

## V1.12 — a definir

## V1.13 — a definir

---

### V1.14–V1.18 (provisional)

Sin alcance definido todavía. Candidatas ya mencionadas en `PROGRESS.md` como pendientes/fuera de
alcance (a priorizar cuando corresponda, no comprometidas a ninguna versión puntual):

- Lotes y vencimientos de productos (no de compras), promociones, proveedores con rating.
- Órdenes de compra persistentes, imputación de pagos a compras concretas, notificaciones de
  vencimientos, saldos a favor y devoluciones a proveedor.
- Backup automático/programado.
- Sincronizar `interfaces/cli/main.py` con los flujos de la web, o retirarlo.
