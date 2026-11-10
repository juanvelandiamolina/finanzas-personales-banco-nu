"""Asigna Categoría / Subcategoría a cada movimiento según un archivo de reglas y el historial del libro.

Orden: reglas 'especificas' → historial (misma descripción y mismo sentido ya clasificada en el libro)
→ reglas 'genericas' → 'sinClasificar'. Cada regla puede pedir:
  patron  regex sobre la descripción (sin distinguir mayúsculas)
  signo   '+' entra dinero / '-' sale dinero
  tipo    sólo tarjeta: Compra, Pago, Devolución, Ajuste a favor, Beneficio, Avance
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# Categorías que significan "entra dinero" (para saber el sentido de una fila del historial)
CATEGORIAS_POSITIVAS = {"Ingreso", "Abono", "Pago Tarjeta"}


@dataclass
class Clasificacion:
    categoria: str
    subcategoria: str
    origen: str        # "regla" | "historial" | "sin regla"
    revisar: bool = False


@dataclass
class _Regla:
    re: re.Pattern
    signo: str | None
    tipo: str | None
    categoria: str
    subcategoria: str
    revisar: bool


class Reglas:
    def __init__(self, ruta: str | Path):
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        compilar = lambda lista: [_Regla(re.compile(x.get("patron", ""), re.I), x.get("signo"), x.get("tipo"),
                                         x["categoria"], x["subcategoria"], bool(x.get("revisar")))
                                  for x in lista or []]
        self.especificas = compilar(datos.get("especificas"))
        self.genericas = compilar(datos.get("genericas"))
        self.sin_clasificar = datos["sinClasificar"]


def signo(monto) -> str:
    return "-" if monto < 0 else "+"


def historial_desde(filas, col_desc="Descripción", col_cat="Categoría", col_sub="Subcategoría") -> dict:
    """descripción + sentido → (categoría, subcategoría) más usada en el libro."""
    cuentas: dict[str, Counter] = defaultdict(Counter)
    for f in filas:
        d, c = f.get(col_desc), f.get(col_cat)
        if not d or not c:
            continue
        k = ("+" if c in CATEGORIAS_POSITIVAS else "-") + "|" + str(d).strip().upper()
        cuentas[k][(c, f.get(col_sub) or "")] += 1
    return {k: c.most_common(1)[0][0] for k, c in cuentas.items()}


def clasificar(descripcion: str, monto, reglas: Reglas, historial: dict | None = None,
               tipo: str | None = None) -> Clasificacion:
    s = signo(monto)
    casa = lambda r: (r.re.search(descripcion) and (not r.signo or r.signo == s)
                      and (not r.tipo or r.tipo == tipo))

    r = next((r for r in reglas.especificas if casa(r)), None)
    if r:
        return Clasificacion(r.categoria, r.subcategoria, "regla", r.revisar)
    h = (historial or {}).get(s + "|" + descripcion.strip().upper())
    if h:
        return Clasificacion(h[0], h[1], "historial")
    r = next((r for r in reglas.genericas if casa(r)), None)
    if r:
        return Clasificacion(r.categoria, r.subcategoria, "regla", r.revisar)
    d = reglas.sin_clasificar[s]
    return Clasificacion(d["categoria"], d["subcategoria"], "sin regla", True)
