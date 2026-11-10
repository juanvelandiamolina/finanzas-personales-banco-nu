"""Proceso común a los dos tipos de extracto: leer → cuadrar → clasificar → CSV → (libro).

Cada tipo de extracto (cuenta, tarjeta) es un `Importador` que sabe:
  - leer su PDF y cuadrarlo con el resumen,
  - convertirlo en filas {columna de la tabla de Excel: valor},
  - qué hoja/tabla del libro le corresponde y cómo se crea si no existe,
  - cómo reconocer una fila que ya está en el libro (clave).
"""
from __future__ import annotations

import csv
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .clasificar import Reglas, historial_desde
from .libro_excel import Columna, anadir_filas, crear_tabla, leer_tabla, libro_abierto, tabla_existe

LIBRO_ABIERTO = 3  # código de salida cuando Excel tiene el libro abierto


def cop(n) -> str:
    """1234567.5 → 1.234.567,5"""
    s = f"{Decimal(str(n)).normalize():,f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


class Importador:
    nombre = ""            # "cuenta" | "tarjeta"
    titulo = ""            # para mostrar
    hoja = ""              # hoja del libro
    tabla = ""             # tabla de Excel dentro de la hoja
    archivo_reglas = ""    # dentro de config/

    def columnas(self) -> list[Columna]:
        """Columnas de la tabla, para crearla si el libro aún no la tiene."""
        raise NotImplementedError

    def leer(self, pdf: Path, clave: str | None):
        raise NotImplementedError

    def cuadrar(self, ext) -> list[str]:
        raise NotImplementedError

    def filas(self, ext, reglas: Reglas, historial: dict, opciones) -> list[dict]:
        """Filas para la tabla. Además de las columnas de Excel, cada fila lleva
        '_origen' (regla/historial/sin regla) y '_revisar' (bool)."""
        raise NotImplementedError

    def clave(self, fila: dict) -> str:
        raise NotImplementedError

    def imprimir(self, filas: list[dict]):
        raise NotImplementedError

    def totales(self, filas: list[dict]) -> str:
        raise NotImplementedError

    def csv_fila(self, fila: dict, ext, pdf: Path) -> dict:
        raise NotImplementedError

    def periodo(self, ext) -> tuple[date, date, str]:
        raise NotImplementedError


@dataclass
class Resultado:
    pendientes: dict[str, list[dict]] = field(default_factory=dict)  # por importador
    leidos: dict[str, list[Path]] = field(default_factory=dict)      # PDF leídos bien, por importador
    errores: bool = False

    @property
    def total_pendientes(self) -> int:
        return sum(len(v) for v in self.pendientes.values())


def _clave_texto(*partes) -> str:
    out = []
    for p in partes:
        if isinstance(p, date):
            out.append(p.isoformat())
        elif isinstance(p, (int, float, Decimal)):
            out.append(str(round(Decimal(str(p)) * 100)))
        else:
            out.append(str(p or "").strip().upper())
    return "|".join(out)


clave_texto = _clave_texto


def procesar(pdfs: list[Path], importadores: dict[str, Importador], detectar, opciones, raiz: Path) -> Resultado:
    """Vista previa + CSV de cada PDF; deja en Resultado lo que falta en el libro."""
    res = Resultado()
    cache_libro: dict[str, list[dict]] = {}
    contadores: dict[str, Counter] = {}

    def filas_libro(imp: Importador) -> list[dict]:
        if imp.nombre not in cache_libro:
            cache_libro[imp.nombre] = (leer_tabla(opciones.libro, imp.tabla)
                                       if opciones.libro and tabla_existe(opciones.libro, imp.tabla) else [])
            contadores[imp.nombre] = Counter(imp.clave(f) for f in cache_libro[imp.nombre])
            if opciones.libro:
                n = len(cache_libro[imp.nombre])
                print(f"Hoja '{imp.hoja}': {n} filas" if n or tabla_existe(opciones.libro, imp.tabla)
                      else f"Hoja '{imp.hoja}': todavía no existe; se creará al añadir.")
        return cache_libro[imp.nombre]

    for pdf in pdfs:
        try:
            tipo = detectar(pdf)
            if tipo not in importadores:
                raise ValueError("no parece un extracto de Cuenta Nu ni de Tarjeta de Crédito Nu.")
            imp = importadores[tipo]
            ext = imp.leer(pdf, opciones.clave)
        except Exception as e:
            print(f"\n✗ {pdf.name}: {e}", file=sys.stderr)
            res.errores = True
            continue

        res.leidos.setdefault(imp.nombre, []).append(pdf)
        libro_filas = filas_libro(imp)
        reglas =Reglas(raiz / "config" / imp.archivo_reglas)
        historial = {} if opciones.sin_historial else historial_desde(libro_filas)
        filas = imp.filas(ext, reglas, historial, opciones)
        avisos = imp.cuadrar(ext)
        desde, hasta, texto = imp.periodo(ext)

        print(f"\n■ {imp.titulo} · {pdf.name} · {texto} · {len(filas)} movimientos")
        print("\n".join("  ⚠ " + x for x in avisos) if avisos
              else "  ✓ Cuadra con el resumen del extracto")
        if not opciones.sin_tabla:
            imp.imprimir(filas)
        print("  " + imp.totales(filas))
        revisar = sum(1 for f in filas if f["_revisar"])
        if revisar:
            print(f"  {revisar} movimientos marcados ← REVISAR (no hay regla clara; corrígelos en el Excel si hace falta)")

        # CSV (para la carga a SQL / Power BI)
        carpeta = Path(opciones.salida) / imp.nombre
        carpeta.mkdir(parents=True, exist_ok=True)
        csv_path = carpeta / (re.sub(r"(_unlocked)?\.pdf$", "", pdf.name, flags=re.I) + ".csv")
        filas_csv = [imp.csv_fila(f, ext, pdf) for f in filas]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(filas_csv[0].keys()) if filas_csv else ["Fecha"])
            w.writeheader()
            w.writerows(filas_csv)
        print(f"  CSV: {csv_path}")

        # ¿qué falta en el libro? (por conteo: dos movimientos iguales el mismo día cuentan como dos)
        disponibles, nuevos = Counter(contadores[imp.nombre]), []
        for f in filas:
            k = imp.clave(f)
            if disponibles[k] > 0:
                disponibles[k] -= 1
            else:
                nuevos.append(f)
        if opciones.libro:
            print(f"  En el libro ya están {len(filas) - len(nuevos)}; nuevos: {len(nuevos)}")
        if opciones.comparar and opciones.libro:
            comparar(imp, filas, libro_filas, desde, hasta)
        if nuevos:
            if avisos and not opciones.forzar:
                print("  ✗ Este PDF no cuadra con su resumen: no se añadirá al libro (salvo --forzar).", file=sys.stderr)
                res.errores = True
            else:
                res.pendientes.setdefault(imp.nombre, []).extend(nuevos)
                contadores[imp.nombre].update(imp.clave(f) for f in nuevos)
    return res


def escribir(res: Resultado, importadores: dict[str, Importador], libro: str | Path, raiz: Path) -> int:
    if not res.total_pendientes:
        print("\nNada que añadir al libro.")
        return 0
    if libro_abierto(libro):
        print("\n✗ El libro está abierto en Excel. Ciérralo y vuelve a intentarlo.", file=sys.stderr)
        return LIBRO_ABIERTO
    libro = Path(libro)
    respaldo = raiz / "respaldos" / f"{libro.stem}_{datetime.now():%Y-%m-%d_%H-%M-%S}{libro.suffix}"
    respaldo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(libro, respaldo)
    print(f"\nRespaldo del libro: {respaldo}")
    for nombre, filas in res.pendientes.items():
        imp = importadores[nombre]
        filas = sorted(filas, key=lambda f: f.get("_orden", 0))
        if tabla_existe(libro, imp.tabla):
            desde, hasta = anadir_filas(libro, imp.tabla, filas)
            print(f"✓ Hoja '{imp.hoja}': añadidas {len(filas)} filas (filas {desde}–{hasta}).")
        else:
            desde, hasta = crear_tabla(libro, imp.hoja, imp.tabla, imp.columnas(), filas)
            print(f"✓ Hoja '{imp.hoja}' creada con la tabla {imp.tabla}: {len(filas)} filas (filas {desde}–{hasta}).")
    archivar(res, raiz)
    return 0


def archivar(res: Resultado, raiz: Path):
    """Copia los PDF ya importados a extractos/<cuenta|tarjeta>/ (los originales no se tocan)."""
    copiados = 0
    for nombre, pdfs in res.leidos.items():
        destino = raiz / "extractos" / nombre
        destino.mkdir(parents=True, exist_ok=True)
        for p in pdfs:
            d = destino / p.name
            if p.resolve() != d.resolve() and not d.exists():
                shutil.copy2(p, d)
                copiados += 1
    if copiados:
        print(f"Copiados {copiados} PDF a la carpeta extractos\\ del proyecto.")


def comparar(imp: Importador, filas: list[dict], libro_filas: list[dict], desde: date, hasta: date):
    del_periodo = [f for f in libro_filas if isinstance(f.get("Fecha"), date) and desde <= f["Fecha"] <= hasta]
    por_clave: dict[str, list[dict]] = {}
    for f in del_periodo:
        por_clave.setdefault(imp.clave(f), []).append(f)
    faltan = dif = 0
    for f in filas:
        lista = por_clave.get(imp.clave(f))
        if not lista:
            faltan += 1
            print(f"    falta en libro: {f['Fecha']} {f['Descripción']}")
            continue
        g = lista.pop(0)
        if (g.get("Categoría"), g.get("Subcategoría")) != (f["Categoría"], f["Subcategoría"]):
            dif += 1
            print(f"    categoría distinta fila {g['_fila']}: {f['Descripción']} → libro "
                  f"\"{g.get('Categoría')} / {g.get('Subcategoría')}\", script \"{f['Categoría']} / {f['Subcategoría']}\"")
    sobran = [g for lista in por_clave.values() for g in lista]
    for g in sobran:
        print(f"    está en libro y no en el PDF: fila {g['_fila']} {g.get('Fecha')} {g.get('Descripción')}")
    print(f"  Comparación con el libro: {len(filas) - faltan} coinciden · {faltan} faltan · "
          f"{len(sobran)} sobran · {dif} con otra categoría")
