"""Lee, amplía y crea tablas del libro financiero editando el XML del .xlsx.

Por qué XML y no openpyxl: el libro lleva el complemento de Claude, comentarios
encadenados, validaciones y columnas calculadas. openpyxl carga y regenera el libro
entero y pierde parte de eso. Aquí sólo se tocan las partes necesarias (la hoja de
la tabla, la definición de la tabla, la cadena de cálculo y, al crear una hoja, el
índice del libro); el resto del .xlsx se copia byte a byte.

Las filas se manejan como dicts {nombre de columna de la tabla: valor}.
"""
from __future__ import annotations

import html
import os
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

EPOCA = date(1899, 12, 30)
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_HOJA = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
REL_TABLA = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/table"
CT_HOJA = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
CT_TABLA = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"


def serial_a_fecha(n: float) -> date:
    return EPOCA + timedelta(days=round(n))


def fecha_a_serial(d: date) -> int:
    return (d - EPOCA).days


def libro_abierto(ruta: str | Path) -> bool:
    """Excel deja un "~$nombre.xlsx" al lado mientras el libro está abierto."""
    ruta = Path(ruta)
    return (ruta.parent / ("~$" + ruta.name)).exists()


def _col_a_num(c: str) -> int:
    n = 0
    for ch in c:
        n = n * 26 + ord(ch) - 64
    return n


def _num_a_col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


@dataclass
class Columna:
    """Columna de una tabla. Al crear una tabla: tipo = fecha | moneda | porcentaje | entero | texto."""
    nombre: str
    tipo: str = "texto"
    formula: str | None = None   # fórmula de columna calculada, en texto normal (sin escapar)
    ancho: float | None = None
    letra: str = ""


# ---------------------------------------------------------------------------- lectura del paquete

class _Libro:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        with zipfile.ZipFile(ruta) as z:
            self.infos = z.infolist()
            self.partes = {i.filename: z.read(i.filename) for i in self.infos}
        self.quitar: set[str] = set()
        self.ss: list[str] = []
        if "xl/sharedStrings.xml" in self.partes:
            for si in re.finditer(r"<si>([\s\S]*?)</si>", self.txt("xl/sharedStrings.xml")):
                self.ss.append(html.unescape("".join(re.findall(r"<t[^>]*>([\s\S]*?)</t>", si.group(1)))))

    def txt(self, p: str) -> str:
        return self.partes[p].decode("utf-8")

    def poner(self, p: str, contenido: str):
        if p not in self.partes:
            self.infos.append(zipfile.ZipInfo(p, date_time=(2026, 1, 1, 0, 0, 0)))
        self.partes[p] = contenido.encode("utf-8")

    def tabla(self, nombre: str) -> "_Tabla | None":
        for p in self.partes:
            if re.fullmatch(r"xl/tables/[^/]+\.xml", p) and re.search(rf'\bname="{re.escape(nombre)}"', self.txt(p)):
                return _Tabla(self, p)
        return None

    def guardar(self, destino: Path):
        tmp = destino.with_name(destino.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for info in self.infos:
                if info.filename not in self.quitar:
                    z.writestr(info, self.partes[info.filename], compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, destino)

    def invalidar_calculo(self):
        """Borra la cadena de cálculo (Excel la rehace) y pide recalcular al abrir."""
        if "xl/calcChain.xml" in self.partes:
            self.quitar.add("xl/calcChain.xml")
            self.poner("[Content_Types].xml", re.sub(r'<Override PartName="/xl/calcChain\.xml"[^>]*/>', "",
                                                     self.txt("[Content_Types].xml")))
            self.poner("xl/_rels/workbook.xml.rels", re.sub(r'<Relationship [^>]*Target="calcChain\.xml"[^>]*/>', "",
                                                            self.txt("xl/_rels/workbook.xml.rels")))
        wb = self.txt("xl/workbook.xml")
        if "fullCalcOnLoad=" not in wb:
            self.poner("xl/workbook.xml", wb.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1))


class _Tabla:
    def __init__(self, libro: _Libro, path: str):
        self.libro, self.path = libro, path
        self.xml = libro.txt(path)
        base = posixpath.basename(path)
        rels = next((p for p in libro.partes if re.fullmatch(r"xl/worksheets/_rels/[^/]+\.rels", p)
                     and "../tables/" + base in libro.txt(p)), None)
        if not rels:
            raise ValueError(f"No encontré la hoja de la tabla {base}.")
        self.hoja_path = "xl/worksheets/" + posixpath.basename(rels)[: -len(".rels")]
        self.hoja_xml = libro.txt(self.hoja_path)
        self.columnas: list[Columna] = []
        for m in re.finditer(r"<tableColumn\b([^>]*?)(?:/>|>([\s\S]*?)</tableColumn>)", self.xml):
            nombre = html.unescape(re.search(r'\bname="([^"]*)"', m.group(1)).group(1))
            f = re.search(r"<calculatedColumnFormula>([\s\S]*?)</calculatedColumnFormula>", m.group(2) or "")
            self.columnas.append(Columna(nombre, formula=html.unescape(f.group(1)) if f else None))
        ref = re.search(r'<table\b[^>]*\bref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', self.xml)
        self.c1, self.r1, self.c2, self.r2 = ref.group(1), int(ref.group(2)), ref.group(3), int(ref.group(4))
        for i, c in enumerate(self.columnas):
            c.letra = _num_a_col(_col_a_num(self.c1) + i)

    def filas_hoja(self):
        return [(int(re.search(r'\br="(\d+)"', m.group(1)).group(1)), m.group(1), m.group(2) or "", m.group(0), m.start())
                for m in re.finditer(r"<row\b([^>]*?)(?:/>|>([\s\S]*?)</row>)", self.hoja_xml)]


def _celdas(cuerpo: str):
    for m in re.finditer(r"<c\b([^>]*?)(?:/>|>([\s\S]*?)</c>)", cuerpo):
        yield re.search(r'\br="([A-Z]+)\d+"', m.group(1)).group(1), m.group(1), m.group(2) or ""


def _valor(attrs: str, cuerpo: str, ss: list[str]):
    t = (re.search(r'\bt="(\w+)"', attrs) or [None, None])[1]
    if t == "inlineStr":
        return html.unescape("".join(re.findall(r"<t[^>]*>([\s\S]*?)</t>", cuerpo)))
    v = re.search(r"<v>([\s\S]*?)</v>", cuerpo)
    if not v:
        return None
    v = v.group(1)
    if t == "s":
        return ss[int(v)]
    if t in ("str", "e"):
        return html.unescape(v)
    if t == "b":
        return v == "1"
    return float(v)


# ---------------------------------------------------------------------------- API

def tabla_existe(ruta: str | Path, tabla: str) -> bool:
    return _Libro(Path(ruta)).tabla(tabla) is not None


def leer_tabla(ruta: str | Path, tabla: str) -> list[dict]:
    """Filas de la tabla como dicts {nombre de columna: valor}, más "_fila" (número de fila en la hoja).
    Las columnas cuyo nombre empieza por "Fecha" se devuelven como date."""
    L = _Libro(Path(ruta))
    T = L.tabla(tabla)
    if T is None:
        raise ValueError(f'No encontré la tabla "{tabla}" en {Path(ruta).name}.')
    por_letra = {c.letra: c.nombre for c in T.columnas}
    out = []
    for r, _, cuerpo, _, _ in T.filas_hoja():
        if not (T.r1 < r <= T.r2):
            continue
        o = {"_fila": r}
        for col, attrs, cc in _celdas(cuerpo):
            if col in por_letra:
                o[por_letra[col]] = _valor(attrs, cc, L.ss)
        for k, v in list(o.items()):
            if k.lower().startswith("fecha") and isinstance(v, float):
                o[k] = serial_a_fecha(v)
        if len(o) > 1:
            out.append(o)
    return out


def _valor_calculado(col: Columna, fila: dict):
    """Valor ya calculado de las columnas Año / Mes / AAAA-MM, para que se vean antes de recalcular."""
    if col.nombre in fila:
        return fila[col.nombre]
    f = fila.get("Fecha")
    if not isinstance(f, date):
        return None
    n = col.nombre.lower()
    if n in ("año", "ano", "anio"):
        return f.year
    if n == "mes":
        return f.month
    if n == "aaaa-mm":
        return f"{f.year}-{f.month:02d}"
    return None


def _fila_xml(r: int, columnas: list[Columna], fila: dict, estilo: dict[str, str | None], attrs_fila: str) -> str:
    celdas = []
    for col in columnas:
        ref = f"{col.letra}{r}"
        s = f' s="{estilo[col.letra]}"' if estilo.get(col.letra) else ""
        if col.formula:
            v = _valor_calculado(col, fila)
            if v is None:
                celdas.append(f'<c r="{ref}"{s}><f>{_esc(col.formula)}</f></c>')
            elif isinstance(v, (int, float, Decimal)):
                celdas.append(f'<c r="{ref}"{s}><f>{_esc(col.formula)}</f><v>{v}</v></c>')
            else:
                celdas.append(f'<c r="{ref}"{s} t="str"><f>{_esc(col.formula)}</f><v>{_esc(v)}</v></c>')
            continue
        v = fila.get(col.nombre)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            v = "Sí" if v else "No"
        if isinstance(v, date):
            celdas.append(f'<c r="{ref}"{s}><v>{fecha_a_serial(v)}</v></c>')
        elif isinstance(v, (int, float, Decimal)):
            celdas.append(f'<c r="{ref}"{s}><v>{v}</v></c>')
        else:
            celdas.append(f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{_esc(v)}</t></is></c>')
    return f'<row r="{r}"{(" " + attrs_fila) if attrs_fila else ""}>{"".join(celdas)}</row>'


def anadir_filas(ruta: str | Path, tabla: str, filas: list[dict], destino: str | Path | None = None):
    """Añade filas al final de una tabla existente. Devuelve (primera, última) fila añadida."""
    if not filas:
        return None
    ruta = Path(ruta)
    L = _Libro(ruta)
    T = L.tabla(tabla)
    if T is None:
        raise ValueError(f'No encontré la tabla "{tabla}" en {ruta.name}.')
    filas_hoja = T.filas_hoja()
    ultima = next((f for f in filas_hoja if f[0] == T.r2), None)
    if ultima is None:
        raise ValueError(f"La tabla {tabla} termina en la fila {T.r2} pero esa fila no existe en la hoja.")
    debajo = [f for f in filas_hoja if f[0] > T.r2 and re.search(r"<v>|<is>|<f>", f[2])]
    if debajo:
        raise ValueError(f"Hay datos debajo de la tabla {tabla} (fila {debajo[0][0]}); no añado para no pisarlos.")

    # estilo de cada columna = el de la última fila de datos
    estilo = {col: (re.search(r'\bs="(\d+)"', a) or [None, None])[1] for col, a, _ in _celdas(ultima[2])}
    attrs_fila = re.sub(r'\s*\br="\d+"', "", ultima[1]).strip()
    nuevas = [_fila_xml(T.r2 + 1 + i, T.columnas, f, estilo, attrs_fila) for i, f in enumerate(filas)]
    nueva_ultima = T.r2 + len(filas)

    # hoja: quitar filas vacías que estorben debajo, insertar las nuevas, ampliar rangos
    hoja = T.hoja_xml
    for f in sorted((f for f in filas_hoja if f[0] > T.r2), key=lambda f: f[4], reverse=True):
        hoja = hoja[: f[4]] + hoja[f[4] + len(f[3]):]
    hoja = hoja.replace(ultima[3], ultima[3] + "".join(nuevas), 1)
    hoja = re.sub(r'<dimension ref="([A-Z]+\d+):([A-Z]+)(\d+)"\s*/>',
                  lambda m: f'<dimension ref="{m.group(1)}:{m.group(2)}{max(int(m.group(3)), nueva_ultima)}"/>', hoja)

    def ampliar(m):  # validaciones / formatos condicionales que acababan en la última fila de la tabla
        partes = []
        for rg in m.group(1).split(" "):
            p = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rg)
            partes.append(f"{p.group(1)}{p.group(2)}:{p.group(3)}{nueva_ultima}"
                          if p and int(p.group(4)) == T.r2 and int(p.group(2)) >= T.r1 else rg)
        return f'sqref="{" ".join(partes)}"'
    L.poner(T.hoja_path, re.sub(r'sqref="([^"]+)"', ampliar, hoja))

    ref_vieja, ref_nueva = f"{T.c1}{T.r1}:{T.c2}{T.r2}", f"{T.c1}{T.r1}:{T.c2}{nueva_ultima}"
    L.poner(T.path, T.xml.replace(f'ref="{ref_vieja}"', f'ref="{ref_nueva}"'))
    L.invalidar_calculo()
    L.guardar(Path(destino) if destino else ruta)
    return T.r2 + 1, nueva_ultima


def crear_tabla(ruta: str | Path, hoja: str, tabla: str, columnas: list[Columna], filas: list[dict],
                destino: str | Path | None = None, estilo_de: str = "tblMovimientos", color: str = "FF820AD1"):
    """Crea una hoja nueva con una tabla de Excel y sus primeras filas (una tabla necesita al menos una).
    Los formatos (fecha, moneda, cabecera) se copian de la tabla `estilo_de` para que se vean igual."""
    if not filas:
        raise ValueError("Para crear la tabla hace falta al menos una fila.")
    ruta = Path(ruta)
    L = _Libro(ruta)
    if L.tabla(tabla):
        raise ValueError(f'Ya existe la tabla "{tabla}".')
    wb = L.txt("xl/workbook.xml")
    if re.search(rf'<sheet\b[^>]*\bname="{re.escape(_esc(hoja))}"', wb):
        raise ValueError(f'Ya existe una hoja llamada "{hoja}".')

    # ---- estilos de referencia
    est = {"fecha": None, "moneda": None, "cabecera": None, "porcentaje": None, "entero": None, "texto": None}
    ref = L.tabla(estilo_de)
    if ref:
        filas_ref = ref.filas_hoja()
        por_nombre = {c.letra: c.nombre.lower() for c in ref.columnas}
        ult = next((f for f in filas_ref if f[0] == ref.r2), None)
        cab = next((f for f in filas_ref if f[0] == ref.r1), None)
        for col, a, _ in _celdas(ult[2] if ult else ""):
            s = (re.search(r'\bs="(\d+)"', a) or [None, None])[1]
            if por_nombre.get(col, "").startswith("fecha"):
                est["fecha"] = s
            elif por_nombre.get(col, "").startswith("monto"):
                est["moneda"] = s
        for _, a, _ in _celdas(cab[2] if cab else ""):
            est["cabecera"] = (re.search(r'\bs="(\d+)"', a) or [None, None])[1]
            break
    estilos = L.txt("xl/styles.xml")
    formatos = {m.group(1): html.unescape(m.group(2))
                for m in re.finditer(r'<numFmt numFmtId="(\d+)" formatCode="([^"]*)"', estilos)}
    xfs = re.findall(r"<xf\b[^>]*?>", re.search(r"<cellXfs[^>]*>([\s\S]*?)</cellXfs>", estilos).group(1))
    for i, xf in enumerate(xfs):
        nf = (re.search(r'numFmtId="(\d+)"', xf) or [None, "0"])[1]
        if est["porcentaje"] is None and nf == "10" and 'fillId="0"' in xf:   # 0.00%
            est["porcentaje"] = str(i)
        if est["entero"] is None and nf == "1" and 'fillId="0"' in xf:
            est["entero"] = str(i)
    if est["porcentaje"] is None:  # añadir el estilo 0.00 % (formato 10 de Excel) al final de cellXfs
        est["porcentaje"] = str(len(xfs))
        estilos = re.sub(r'<cellXfs count="(\d+)">([\s\S]*?)</cellXfs>',
                         lambda m: f'<cellXfs count="{int(m.group(1)) + 1}">{m.group(2)}'
                                   '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                                   '</cellXfs>', estilos, count=1)
        L.poner("xl/styles.xml", estilos)

    # ---- números nuevos de partes e identificadores
    n_hoja = 1 + max(int(m.group(1)) for p in L.partes if (m := re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", p)))
    n_tabla = 1 + max([int(m.group(1)) for p in L.partes if (m := re.fullmatch(r"xl/tables/table(\d+)\.xml", p))] or [0])
    id_tabla = 1 + max([int(re.search(r'<table\b[^>]*\bid="(\d+)"', L.txt(p)).group(1))
                        for p in L.partes if re.fullmatch(r"xl/tables/[^/]+\.xml", p)] or [0])
    rels = L.txt("xl/_rels/workbook.xml.rels")
    rid = "rId" + str(1 + max(int(x) for x in re.findall(r'Id="rId(\d+)"', rels)))
    sheet_id = 1 + max(int(x) for x in re.findall(r'<sheet\b[^>]*\bsheetId="(\d+)"', wb))
    hoja_path, tabla_path = f"xl/worksheets/sheet{n_hoja}.xml", f"xl/tables/table{n_tabla}.xml"

    for i, c in enumerate(columnas):
        c.letra = _num_a_col(i + 1)
    ultima_col, ultima_fila = columnas[-1].letra, 1 + len(filas)
    rango = f"A1:{ultima_col}{ultima_fila}"
    estilo_col = {c.letra: est.get(c.tipo) for c in columnas}

    cab_s = f' s="{est["cabecera"]}"' if est["cabecera"] else ""
    cabecera = "".join(f'<c r="{c.letra}1"{cab_s} t="inlineStr"><is><t>{_esc(c.nombre)}</t></is></c>' for c in columnas)
    spans = f'spans="1:{len(columnas)}"'
    filas_xml = [f'<row r="1" {spans}>{cabecera}</row>'] + [
        _fila_xml(2 + i, columnas, f, estilo_col, spans) for i, f in enumerate(filas)]
    cols = "".join(f'<col min="{i}" max="{i}" width="{c.ancho}" customWidth="1"/>'
                   for i, c in enumerate(columnas, start=1) if c.ancho)
    L.poner(hoja_path,
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_R}">'
            f'<sheetPr><tabColor rgb="{color}"/></sheetPr><dimension ref="{rango}"/>'
            '<sheetViews><sheetView showGridLines="0" workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/>'
            + (f"<cols>{cols}</cols>" if cols else "")
            + f'<sheetData>{"".join(filas_xml)}</sheetData>'
            '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
            '<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>')
    L.poner(f"xl/worksheets/_rels/sheet{n_hoja}.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL_TABLA}" Target="../tables/table{n_tabla}.xml"/></Relationships>')
    tcols = "".join(
        f'<tableColumn id="{i}" name="{_esc(c.nombre)}"'
        + (f"><calculatedColumnFormula>{_esc(c.formula)}</calculatedColumnFormula></tableColumn>" if c.formula else "/>")
        for i, c in enumerate(columnas, start=1))
    L.poner(tabla_path,
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<table xmlns="{NS_MAIN}" id="{id_tabla}" name="{tabla}" displayName="{tabla}" ref="{rango}" totalsRowShown="0">'
            f'<autoFilter ref="{rango}"/><tableColumns count="{len(columnas)}">{tcols}</tableColumns>'
            '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" '
            'showColumnStripes="0"/></table>')

    # ---- índice del libro: hoja, relación y tipos de contenido
    L.poner("xl/workbook.xml", wb.replace("</sheets>", f'<sheet name="{_esc(hoja)}" sheetId="{sheet_id}" r:id="{rid}"/></sheets>', 1))
    L.poner("xl/_rels/workbook.xml.rels", rels.replace(
        "</Relationships>", f'<Relationship Id="{rid}" Type="{REL_HOJA}" Target="worksheets/sheet{n_hoja}.xml"/></Relationships>', 1))
    ct = L.txt("[Content_Types].xml").replace(
        "</Types>", f'<Override PartName="/{hoja_path}" ContentType="{CT_HOJA}"/>'
                    f'<Override PartName="/{tabla_path}" ContentType="{CT_TABLA}"/></Types>', 1)
    L.poner("[Content_Types].xml", ct)
    if "docProps/app.xml" in L.partes:  # lista de hojas que muestra el Explorador de Windows
        app = L.txt("docProps/app.xml")
        app = re.sub(r"(<vt:lpstr>Hojas de c\S*?</vt:lpstr></vt:variant><vt:variant><vt:i4>)(\d+)",
                     lambda m: m.group(1) + str(int(m.group(2)) + 1), app)
        m = re.search(r'<TitlesOfParts><vt:vector size="(\d+)"', app)
        if m:
            app = app.replace(m.group(0), f'<TitlesOfParts><vt:vector size="{int(m.group(1)) + 1}"', 1)
            app = re.sub(r"(<TitlesOfParts>[\s\S]*?)(</vt:vector>)", lambda x: x.group(1) + f"<vt:lpstr>{_esc(hoja)}</vt:lpstr>" + x.group(2), app, 1)
        L.poner("docProps/app.xml", app)
    L.invalidar_calculo()
    L.guardar(Path(destino) if destino else ruta)
    return 2, ultima_fila
