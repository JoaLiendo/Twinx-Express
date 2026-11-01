"""Política de retención de backups (V1.15): qué backups se conservan.

Función pura y determinista (sin disco ni reloj): recibe la lista de backups
ya clasificados y devuelve los que hay que conservar. Todo lo demás de esa
lista se puede borrar; lo que no está en la lista (archivos ajenos, backups
dañados que no se pudieron clasificar) nunca se considera.

Semántica (cada regla elige backups por su cuenta; se conserva la unión):

- **Regulares** (`MANUAL`, `AUTOMATICO`, `CIERRE_CAJA`), esquema abuelo-padre-hijo:
  - diarios: el más reciente de cada uno de los últimos `diarios` días *con
    backups* (no días de calendario: si el kiosco cierra un mes, no se pierde
    nada por no haber operado);
  - semanales: el más reciente de cada una de las últimas `semanales` semanas
    ISO con backups (la semana ISO resuelve los cruces de año);
  - mensuales: el más reciente de cada uno de los últimos `mensuales` meses
    con backups.
- **Manuales**: además, los `manuales` más recientes siempre se conservan,
  para que un backup que el dueño acaba de pedir no desaparezca por una regla
  de calendario.
- **Pre-update**: los `pre_update` más recientes, en su propia familia (nunca
  compiten con los diarios), más los `protegidos` (los que respaldan una
  versión instalada a la que se puede volver con rollback).
- **Seguridad** (copia previa a un restore/rollback): los `seguridad` más recientes.
"""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime

from domain.backup import TIPO_AUTOMATICO, TIPO_CIERRE_CAJA, TIPO_MANUAL, TIPO_PRE_UPDATE, TIPO_SEGURIDAD

TIPOS_REGULARES = frozenset({TIPO_MANUAL, TIPO_AUTOMATICO, TIPO_CIERRE_CAJA})


@dataclass(frozen=True)
class BackupClasificado:
    nombre: str
    fecha: datetime
    tipo: str


@dataclass(frozen=True)
class PoliticaRetencion:
    diarios: int = 14
    semanales: int = 8
    mensuales: int = 12
    pre_update: int = 3
    manuales: int = 10
    seguridad: int = 3


def _mas_reciente_por_periodo(
    ordenados: list[BackupClasificado], periodo: Callable[[datetime], Hashable], cantidad: int
) -> set[str]:
    elegidos: dict[Hashable, str] = {}
    for backup in ordenados:
        clave = periodo(backup.fecha)
        if clave not in elegidos:
            if len(elegidos) >= cantidad:
                break
            elegidos[clave] = backup.nombre
    return set(elegidos.values())


def _mas_recientes(ordenados: list[BackupClasificado], tipo: str, cantidad: int) -> set[str]:
    return {b.nombre for b in [b for b in ordenados if b.tipo == tipo][: max(cantidad, 0)]}


def backups_a_conservar(
    backups: Iterable[BackupClasificado],
    politica: PoliticaRetencion = PoliticaRetencion(),
    protegidos: frozenset[str] = frozenset(),
) -> set[str]:
    """Nombres de los backups que se conservan según `politica`."""
    # Orden total y determinista: fecha y, a igual fecha, nombre.
    ordenados = sorted(backups, key=lambda b: (b.fecha, b.nombre), reverse=True)
    regulares = [b for b in ordenados if b.tipo in TIPOS_REGULARES]

    conservar = set()
    conservar |= _mas_reciente_por_periodo(regulares, lambda f: f.date(), politica.diarios)
    conservar |= _mas_reciente_por_periodo(regulares, lambda f: tuple(f.isocalendar())[:2], politica.semanales)
    conservar |= _mas_reciente_por_periodo(regulares, lambda f: (f.year, f.month), politica.mensuales)
    conservar |= _mas_recientes(ordenados, TIPO_MANUAL, politica.manuales)
    conservar |= _mas_recientes(ordenados, TIPO_PRE_UPDATE, politica.pre_update)
    conservar |= _mas_recientes(ordenados, TIPO_SEGURIDAD, politica.seguridad)
    conservar |= {b.nombre for b in ordenados if b.nombre in protegidos}
    return conservar
