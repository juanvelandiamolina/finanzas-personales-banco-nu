"""Utilidades comunes para leer los PDF de Nu: abrir (con o sin contraseña), líneas con coordenadas y montos."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
NOMBRE_MES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def monto_cop(txt: str) -> Decimal | None:
    """"$1.234.567,00" / "-$6.000,00" / "+$0,20" / "+ $0,20" / "- $386.070,00" → Decimal con signo."""
    m = re.fullmatch(r"([+-]?)\$([\d.]+),(\d{2})", re.sub(r"\s", "", txt or ""))
    if not m:
        return None
    n = Decimal(m.group(2).replace(".", "") + "." + m.group(3))
    return -n if m.group(1) == "-" else n


def mes(txt: str) -> int:
    """'oct' / 'OCT' / 'octubre' → 10"""
    try:
        return MESES.index(txt.strip().lower()[:3]) + 1
    except ValueError:
        raise ValueError(f'Mes no reconocido: "{txt}"') from None


@dataclass
class Linea:
    top: float
    trozos: list[tuple[float, str]]  # (x, texto), de izquierda a derecha
    pagina: int = 0

    @property
    def texto(self) -> str:
        return " ".join(s for _, s in self.trozos)

    def en(self, x_min: float, x_max: float) -> str:
        """Texto de los trozos cuya x está en [x_min, x_max)."""
        return " ".join(s for x, s in self.trozos if x_min <= x < x_max)


def lineas(page, numero: int = 0) -> list[Linea]:
    """Agrupa las palabras de la página en líneas (misma altura ±2 pt), de arriba abajo."""
    palabras = [w for w in page.extract_words(keep_blank_chars=True, x_tolerance=3)
                if w.get("upright", True) and w["text"].strip()]
    out: list[Linea] = []
    for w in sorted(palabras, key=lambda w: (w["top"], w["x0"])):
        l = next((l for l in out if abs(l.top - w["top"]) <= 2), None)
        if l is None:
            l = Linea(w["top"], [], numero)
            out.append(l)
        l.trozos.append((w["x0"], w["text"].strip()))
    for l in out:
        l.trozos.sort()
    return sorted(out, key=lambda l: l.top)


def leer_lineas(ruta: str | Path, clave: str | None = None) -> list[list[Linea]]:
    """Todas las páginas del PDF como listas de líneas."""
    with abrir(Path(ruta), clave) as pdf:
        return [lineas(p, n) for n, p in enumerate(pdf.pages, start=1)]


def abrir(ruta: Path, clave: str | None):
    try:
        pdf = pdfplumber.open(ruta)
        pdf.pages[0].extract_words()  # fuerza el descifrado
        return pdf
    except Exception as e:  # pdfplumber envuelve el error de pdfminer
        if not _es_de_clave(e):
            raise
    if not clave:
        raise ValueError(f'"{ruta.name}" tiene contraseña. Ponla en NU_PDF_CLAVE (archivo .env) '
                         "o pasa el PDF ya desbloqueado.")
    try:
        pdf = pdfplumber.open(ruta, password=clave)
        pdf.pages[0].extract_words()
        return pdf
    except Exception as e:
        if _es_de_clave(e):
            raise ValueError(f'La contraseña de NU_PDF_CLAVE no abre "{ruta.name}".') from None
        raise


def problema_de_clave(ruta: str | Path, clave: str | None) -> str | None:
    """None si el PDF se puede abrir con esa clave (o no la necesita); si no, el motivo."""
    try:
        abrir(Path(ruta), clave).close()
        return None
    except ValueError as e:
        if "contraseña" in str(e):
            return str(e)
        raise


def _es_de_clave(e: BaseException | None) -> bool:
    while e is not None:
        if isinstance(e, PDFPasswordIncorrect) or any(isinstance(a, PDFPasswordIncorrect) for a in e.args):
            return True
        e = e.__cause__ or e.__context__
    return False


def tipo_de_extracto(ruta: str | Path, clave: str | None = None) -> str | None:
    """'cuenta' | 'tarjeta' | None, mirando el texto de la primera página."""
    with abrir(Path(ruta), clave) as pdf:
        texto = pdf.pages[0].extract_text() or ""
    if re.search(r"Fecha de corte", texto, re.I) and re.search(r"Periodo facturado", texto, re.I):
        return "tarjeta"
    if re.search(r"Per[ií]odo", texto, re.I) and re.search(r"Resumen de tus movimientos|Dinero Disponible", texto, re.I):
        return "cuenta"
    return None
