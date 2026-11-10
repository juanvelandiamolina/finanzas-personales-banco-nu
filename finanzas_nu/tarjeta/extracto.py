"""Lee el extracto mensual de la Tarjeta de Crédito Nu.

Página 1: fecha límite de pago, fecha de corte, periodo facturado y el resumen
(deuda del mes, intereses, cargos por conversión, pago mínimo...).

Páginas siguientes: una tabla con columnas a x fija

    Fecha(30) Descripción(80) Valor(150) Cuotas(210) Valor del mes(258)
    Interés del mes(320: "2.10%   $596,45") Total a pagar(422) Restante(504)

Cada movimiento ocupa varias líneas: la principal (con fecha y montos), debajo
el año "2026" y el resto de la descripción, y a veces sub-líneas "↪ ...":
  ↪ Comisión por cambio de moneda ........ $70,37   (compras en moneda extranjera)
  ↪ A capital / A intereses / A cargos y comisiones   (desglose de un pago)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..comun.pdf import Linea, leer_lineas, mes, monto_cop

# límites de columna (x de inicio de cada una, con margen)
COLUMNAS = [("fecha", 25), ("descripcion", 75), ("valor", 145), ("cuotas", 205), ("valor_mes", 250),
            ("interes", 315), ("total", 415), ("restante", 500)]
RE_FECHA = re.compile(r"(\d{1,2})\s+([A-Z]{3})(?:\s+(\d{4}))?", re.I)


@dataclass
class MovimientoTarjeta:
    fecha: date
    descripcion: str
    tipo: str                         # Compra | Pago | Devolución | Ajuste a favor | Beneficio | Avance
    valor: Decimal                    # valor original de la operación (positivo)
    cuota: int | None = None          # 3 en "3 de 24"
    cuotas: int | None = None         # 24 en "3 de 24"
    valor_mes: Decimal = Decimal(0)   # capital que se cobra en este extracto
    tasa: Decimal | None = None       # tasa M.V. como fracción (0.021 = 2,10 %)
    intereses: Decimal = Decimal(0)
    comisiones: Decimal = Decimal(0)  # comisión por cambio de moneda
    total: Decimal = Decimal(0)       # "Total a pagar este mes" (con su signo, puede ser negativo)
    restante: Decimal = Decimal(0)    # "Restante por pagar"
    a_capital: Decimal | None = None  # desglose de pagos y beneficios
    a_intereses: Decimal | None = None
    a_cargos: Decimal | None = None
    pagina: int = 0


@dataclass
class ExtractoTarjeta:
    archivo: Path
    tarjeta: str | None               # últimos 4 dígitos
    corte: date
    limite_pago: date
    desde: date
    hasta: date
    mes_extracto: str                 # "Agosto" en "Llegó tu extracto de Agosto"
    resumen: dict[str, Decimal] = field(default_factory=dict)
    movimientos: list[MovimientoTarjeta] = field(default_factory=list)

    @property
    def periodo_texto(self) -> str:
        return f"{self.desde:%d/%m/%Y} - {self.hasta:%d/%m/%Y} (corte {self.corte:%d/%m/%Y})"


def _fecha_larga(txt: str) -> date:
    """'29 AGO 2026' → date"""
    m = re.fullmatch(r"\s*(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})\s*", txt)
    if not m:
        raise ValueError(f'Fecha no reconocida: "{txt}"')
    return date(int(m.group(3)), mes(m.group(2)), int(m.group(1)))


def _columnas(l: Linea) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    for x, s in l.trozos:
        nombre = [n for n, x0 in COLUMNAS if x >= x0][-1] if x >= COLUMNAS[0][1] else "fecha"
        out.setdefault(nombre, []).append(s)
    return {k: " ".join(v) for k, v in out.items()}


def _tipo(desc: str, valor: Decimal) -> str:
    d = desc.lower()
    if d.startswith("gracias por tu pago"):
        return "Pago"
    if d.startswith("ajuste a tu favor"):
        return "Ajuste a favor"
    if "beneficio" in d:
        return "Beneficio"
    if valor < 0 or "devoluci" in d:
        return "Devolución"
    if "avance" in d:
        return "Avance"
    return "Compra"


def leer_extracto_tarjeta(ruta: str | Path, clave: str | None = None) -> ExtractoTarjeta:
    ruta = Path(ruta)
    paginas = leer_lineas(ruta, clave)
    p1 = paginas[0]

    # ---- Cabecera
    ext = None
    for i, l in enumerate(p1):
        if re.search(r"Fecha de corte", l.texto, re.I) and i + 1 < len(p1):
            v = p1[i + 1]
            # tres valores en la línea de abajo, en el mismo orden que sus títulos
            limite = _fecha_larga(v.en(0, 270))
            corte = _fecha_larga(v.en(270, 410))
            per = re.fullmatch(r"\s*(\d{1,2})\s+([A-Za-z]{3})\s*-\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s*", v.en(410, 999))
            if not per:
                raise ValueError(f'Periodo facturado no reconocido: "{v.en(410, 999)}"')
            anio_h = int(per.group(5))
            hasta = date(anio_h, mes(per.group(4)), int(per.group(3)))
            m_desde = mes(per.group(2))
            desde = date(anio_h - (1 if m_desde > hasta.month else 0), m_desde, int(per.group(1)))
            mes_ext = next((re.search(r"extracto de (\w+)", x.texto, re.I).group(1)
                            for x in p1 if re.search(r"extracto de \w+", x.texto, re.I)), "")
            tarjeta = next((m.group(1) for x in p1
                            if (m := re.search(r"•\s*•\s*•\s*•\s*(\d{4})", x.texto))), None)
            ext = ExtractoTarjeta(ruta, tarjeta, corte, limite, desde, hasta, mes_ext)
            break
    if ext is None:
        raise ValueError(f'"{ruta.name}" no parece un extracto de Tarjeta de Crédito Nu (no encontré la fecha de corte).')

    # ---- Resumen (columna derecha de la página 1)
    etiquetas = {
        "deuda_mes": r"^Deuda a pagar este mes", "intereses": r"^Intereses", "comision_avances": r"^Comisiones de avances",
        "comision_servicio": r"^Comisiones por servicio", "conversion": r"^Cargos por conversi",
        "abonos": r"^Abonos", "devoluciones": r"^Devoluciones o ajustes", "pago_minimo": r"^PAGO MÍNIMO",
        "deuda_restante": r"^Deuda restante", "pago_total": r"^PAGO HASTA",
    }
    for l in p1:
        izquierda = l.en(285, 495).strip()
        for k, patron in etiquetas.items():
            if k in ext.resumen or not re.search(patron, izquierda, re.I):
                continue
            v = monto_cop(l.en(495, 999))
            if v is not None:
                ext.resumen[k] = v
    # "Cupo" a la izquierda: $8.000.000,00 bajo "Tu cupo definido"
    for i, l in enumerate(p1):
        if re.search(r"Tu cupo definido", l.texto, re.I) and i + 1 < len(p1):
            v = monto_cop(p1[i + 1].en(0, 200))
            if v is not None:
                ext.resumen["cupo"] = v

    # ---- Movimientos
    actual: MovimientoTarjeta | None = None
    anio_pendiente = False
    for lineas_pag in paginas[1:]:
        if not any(re.search(r"Fecha\s+Descripci", l.texto) for l in lineas_pag):
            continue
        dentro = False
        for l in lineas_pag:
            t = l.texto
            if re.search(r"Fecha\s+Descripci", t):
                dentro = True
                continue
            if not dentro or re.search(r"Porcentaje y valor", t):
                continue
            if re.search(r"^Pago mínimo|Tienes preguntas|Nu Financiera", t.strip()) or l.en(415, 500).startswith("Pago mínimo"):
                dentro = False
                actual = None
                continue
            c = _columnas(l)
            fm = RE_FECHA.fullmatch(c.get("fecha", ""))
            valor = monto_cop(c.get("valor", ""))
            if fm and valor is not None:
                # línea principal de un movimiento
                anio = int(fm.group(3)) if fm.group(3) else ext.hasta.year
                anio_pendiente = not fm.group(3)
                desc = c.get("descripcion", "")
                actual = MovimientoTarjeta(
                    fecha=date(anio, mes(fm.group(2)), int(fm.group(1))), descripcion=desc,
                    tipo="", valor=valor, pagina=l.pagina,
                    valor_mes=monto_cop(c.get("valor_mes", "")) or Decimal(0),
                    total=monto_cop(c.get("total", "")) or Decimal(0),
                    restante=monto_cop(c.get("restante", "")) or Decimal(0))
                cu = re.fullmatch(r"(\d+)\s+de\s+(\d+)", c.get("cuotas", ""))
                if cu:
                    actual.cuota, actual.cuotas = int(cu.group(1)), int(cu.group(2))
                it = re.fullmatch(r"([\d.,]+)%\s*(.*)", c.get("interes", ""))
                if it:
                    actual.tasa = (Decimal(it.group(1).replace(",", ".")) / 100).quantize(Decimal("0.0001"))
                    actual.intereses = monto_cop(it.group(2)) or Decimal(0)
                ext.movimientos.append(actual)
                continue
            if actual is None:
                continue
            # líneas de continuación del movimiento actual
            if anio_pendiente and re.fullmatch(r"\d{4}", c.get("fecha", "")):
                actual.fecha = actual.fecha.replace(year=int(c["fecha"]))
                anio_pendiente = False
            if c.get("descripcion"):
                actual.descripcion += " " + c["descripcion"]
            sub = c.get("valor", "")
            if sub.startswith("↪"):
                s = sub.lstrip("↪ ").strip()
                if re.match(r"Comisi[oó]n", s, re.I):
                    actual.comisiones += monto_cop(c.get("total", "")) or Decimal(0)
                else:
                    m = re.match(r"A (capital|intereses|cargos y comisiones)\s+(.*)", s, re.I)
                    if m:
                        attr = {"capital": "a_capital", "intereses": "a_intereses"}.get(m.group(1).lower(), "a_cargos")
                        setattr(actual, attr, monto_cop(m.group(2)))

    for m in ext.movimientos:
        m.descripcion = re.sub(r"\s+", " ", m.descripcion).strip()
        m.tipo = _tipo(m.descripcion, m.valor)
        m.valor = abs(m.valor)
    return ext


def cuadrar(ext: ExtractoTarjeta) -> list[str]:
    """Comprueba los movimientos leídos contra el resumen de la página 1. Lista vacía = cuadra."""
    r, avisos = ext.resumen, []
    S = lambda xs: sum(xs, Decimal(0))
    movs = ext.movimientos
    compras = [m for m in movs if m.tipo in ("Compra", "Avance")]
    pruebas = [  # (clave del resumen, nombre, suma leída, obligatoria)
        ("deuda_mes", "Deuda a pagar este mes", S(m.valor_mes for m in compras), True),
        ("intereses", "Intereses", S(m.intereses for m in movs), True),
        ("conversion", "Cargos por conversión", S(m.comisiones for m in movs), True),
        ("abonos", "Abonos", S(m.total for m in movs if m.tipo in ("Pago", "Beneficio")), False),
        ("devoluciones", "Devoluciones o ajustes a favor",
         S(m.total for m in movs if m.tipo in ("Devolución", "Ajuste a favor")), False),
        ("pago_minimo", "PAGO MÍNIMO", S(m.total + m.comisiones for m in movs), True),
    ]
    for k, nombre, leido, obligatoria in pruebas:
        if k not in r:
            if obligatoria:
                avisos.append(f'No encontré "{nombre}" en el resumen.')
        elif leido != r[k]:
            avisos.append(f'"{nombre}" del resumen es {r[k]} y los movimientos suman {leido} (diferencia {r[k] - leido}).')
    return avisos
