"""Entidad Categoría de producto (Fase 3C) y su árbol (V1.11-E).

Agrupa productos para organizar/filtrar el catálogo. Es puramente
organizativa: no tiene ninguna relación con precios, stock ni ventas.

Desde V1.11-E una categoría puede estar dentro de otra (Alimentos ›
Panificados › Criollos). El modelo admite cualquier profundidad; la
interfaz ofrece hasta `NIVELES_MAXIMOS` niveles. Un producto tiene una
sola categoría, de cualquier nivel: filtrar por una categoría incluye a
sus subcategorías (`ArbolCategorias.ids_con_descendientes`).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from excepciones import DatosInvalidosError

NIVELES_MAXIMOS = 3
SEPARADOR_RUTA = " › "


@dataclass
class Categoria:
    """Una categoría de productos, validada al construirse."""

    nombre: str
    id: int | None = None
    activa: bool = True
    fecha_creacion: str | None = None
    categoria_padre_id: int | None = None

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre de la categoría no puede estar vacío.")


@dataclass(frozen=True)
class CategoriaEnArbol:
    """Una categoría con su posición en el árbol, para listados y selects."""

    categoria: Categoria
    nivel: int
    ruta: str


class ArbolCategorias:
    """Vista de solo lectura del árbol de categorías, construida a partir de todas las
    categorías (activas e inactivas). El catálogo de un kiosco es chico: se arma en memoria
    en vez de con consultas recursivas por cada pregunta."""

    def __init__(self, categorias: Iterable[Categoria]) -> None:
        self._por_id = {categoria.id: categoria for categoria in categorias}
        self._hijos: dict[int | None, list[Categoria]] = {}
        for categoria in sorted(self._por_id.values(), key=lambda c: c.nombre.casefold()):
            padre = categoria.categoria_padre_id if categoria.categoria_padre_id in self._por_id else None
            self._hijos.setdefault(padre, []).append(categoria)

    def obtener(self, categoria_id: int) -> Categoria | None:
        return self._por_id.get(categoria_id)

    def ancestros(self, categoria_id: int) -> list[Categoria]:
        """De la categoría principal hasta la indicada, incluida."""
        cadena: list[Categoria] = []
        actual = self._por_id.get(categoria_id)
        while actual is not None and actual not in cadena:
            cadena.append(actual)
            actual = self._por_id.get(actual.categoria_padre_id) if actual.categoria_padre_id else None
        return list(reversed(cadena))

    def ruta(self, categoria_id: int) -> str:
        """Breadcrumb legible: "Alimentos › Panificados › Criollos"."""
        return SEPARADOR_RUTA.join(categoria.nombre for categoria in self.ancestros(categoria_id))

    def nivel(self, categoria_id: int) -> int:
        """1 para una categoría principal, 2 para una subcategoría, etc."""
        return len(self.ancestros(categoria_id))

    def ids_con_descendientes(self, categoria_id: int) -> set[int]:
        """La categoría y todas sus subcategorías, a cualquier profundidad."""
        resultado: set[int] = set()
        pendientes = [categoria_id] if categoria_id in self._por_id else []
        while pendientes:
            actual = pendientes.pop()
            if actual in resultado:
                continue
            resultado.add(actual)
            pendientes.extend(hijo.id for hijo in self._hijos.get(actual, []))
        return resultado

    def subcategorias(self, categoria_id: int) -> list[Categoria]:
        """Subcategorías directas, por nombre."""
        return list(self._hijos.get(categoria_id, []))

    def altura(self, categoria_id: int) -> int:
        """Niveles que ocupa la categoría con sus subcategorías (1 si no tiene)."""
        hijos = self._hijos.get(categoria_id, [])
        return 1 + max((self.altura(hijo.id) for hijo in hijos), default=0)

    def en_orden(self, solo_activas: bool = False) -> list[CategoriaEnArbol]:
        """Recorrido en profundidad (cada categoría seguida de sus subcategorías, por nombre).
        Con `solo_activas`, una categoría inactiva oculta también a sus subcategorías."""
        resultado: list[CategoriaEnArbol] = []

        def visitar(padre_id: int | None, nivel: int) -> None:
            for categoria in self._hijos.get(padre_id, []):
                if solo_activas and not categoria.activa:
                    continue
                resultado.append(CategoriaEnArbol(categoria, nivel, self.ruta(categoria.id)))
                visitar(categoria.id, nivel + 1)

        visitar(None, 1)
        return resultado

    def validar_ubicacion(self, categoria_id: int | None, categoria_padre_id: int | None) -> None:
        """Valida que `categoria_id` (o una categoría nueva, si es `None`) pueda quedar dentro de
        `categoria_padre_id` (`None` = categoría principal): el padre existe y está activo, no es
        la propia categoría ni una de sus subcategorías, y el resultado no supera `NIVELES_MAXIMOS`.

        Raises:
            DatosInvalidosError: si la ubicación no es válida.
        """
        if categoria_padre_id is None:
            altura = self.altura(categoria_id) if categoria_id is not None else 1
            if altura > NIVELES_MAXIMOS:
                raise DatosInvalidosError(f"Las categorías admiten hasta {NIVELES_MAXIMOS} niveles.")
            return
        padre = self._por_id.get(categoria_padre_id)
        if padre is None:
            raise DatosInvalidosError("La categoría superior elegida no existe.")
        if not padre.activa:
            raise DatosInvalidosError(f"La categoría '{padre.nombre}' está dada de baja.")
        if categoria_id is not None and categoria_padre_id in self.ids_con_descendientes(categoria_id):
            raise DatosInvalidosError("Una categoría no puede quedar dentro de sí misma ni de una de sus subcategorías.")
        altura = self.altura(categoria_id) if categoria_id is not None else 1
        if self.nivel(categoria_padre_id) + altura > NIVELES_MAXIMOS:
            raise DatosInvalidosError(f"Las categorías admiten hasta {NIVELES_MAXIMOS} niveles.")
