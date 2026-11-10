"""Pruebas que no necesitan PDF ni libro reales:  python -m unittest discover tests"""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finanzas_nu.comun.clasificar import Reglas, clasificar, historial_desde
from finanzas_nu.comun.libro_excel import fecha_a_serial, serial_a_fecha
from finanzas_nu.comun.pdf import monto_cop
from finanzas_nu.cuenta.extracto import ExtractoCuenta, Movimiento, cuadrar as cuadrar_cuenta
from finanzas_nu.tarjeta.extracto import ExtractoTarjeta, MovimientoTarjeta, cuadrar as cuadrar_tarjeta
from finanzas_nu.tarjeta.importar import _es_nueva

CONFIG = Path(__file__).resolve().parent.parent / "config"
REGLAS_CUENTA = Reglas(CONFIG / "reglas_cuenta.json")
REGLAS_TARJETA = Reglas(CONFIG / "reglas_tarjeta.json")
D = Decimal


class Montos(unittest.TestCase):
    def test_formatos_de_nu(self):
        self.assertEqual(monto_cop("-$6.000,00"), D("-6000.00"))
        self.assertEqual(monto_cop("+$1.234.567,00"), D("1234567.00"))
        self.assertEqual(monto_cop("$0,20"), D("0.20"))
        self.assertEqual(monto_cop("- $123.456,00"), D("-123456.00"))
        self.assertIsNone(monto_cop("Pago a Claro"))


class ClasificacionCuenta(unittest.TestCase):
    def caso(self, desc, monto, cat, sub):
        c = clasificar(desc, D(monto), REGLAS_CUENTA)
        self.assertEqual((c.categoria, c.subcategoria), (cat, sub), desc)

    def test_reglas(self):
        self.caso("Enviaste a FULANO DE TAL", -1000, "Gasto Variable", "Transferencias")
        self.caso("Recibiste de Bancolombia", 1000, "Ingreso", "Transferencia Recibida")
        self.caso("Pago a Claro", -1000, "Gasto Fijo", "Servicios")
        self.caso("Enviaste a INMOBILIARIA EJEMPLO SAS", -1000, "Gasto Fijo", "Arriendo")
        self.caso("Compra en OXXO CENTRO 3", -1000, "Gasto Variable", "Alimentación")
        self.caso("Compra en DLO*GOOGLE YouTubePrem", -1000, "Gasto Fijo", "Suscripciones")
        self.caso("Impuesto del 4x1000", -4, "Gasto Fijo", "Impuestos")
        self.caso("Impuesto del 4x1000", 4, "Ingreso", "Reembolsos")
        self.caso("Pagaste tu tarjeta", -1000, "Gasto Variable", "Tarjeta de Crédito")
        self.caso("Rendimientos Nu octubre 2026", 1000, "Ingreso", "Rendimientos")

    def test_desconocido_se_marca_para_revisar(self):
        self.assertTrue(clasificar("Algo nuevo de Nu", D(-5), REGLAS_CUENTA).revisar)

    def test_historial_gana_a_la_generica(self):
        hist = historial_desde([{"Descripción": "Enviaste a MENGANO DE TAL", "Categoría": "Gasto Fijo", "Subcategoría": "Arriendo"}])
        c = clasificar("Enviaste a MENGANO DE TAL", D(-900000), REGLAS_CUENTA, hist)
        self.assertEqual((c.categoria, c.subcategoria, c.origen), ("Gasto Fijo", "Arriendo", "historial"))


class ClasificacionTarjeta(unittest.TestCase):
    def caso(self, desc, tipo, cat, sub):
        monto = D(-1000) if tipo in ("Compra", "Avance") else D(1000)
        c = clasificar(desc, monto, REGLAS_TARJETA, tipo=tipo)
        self.assertEqual((c.categoria, c.subcategoria), (cat, sub), desc)

    def test_reglas(self):
        self.caso("Gracias por tu pago", "Pago", "Pago Tarjeta", "Pago")
        self.caso("St-Tienda Devolución", "Devolución", "Abono", "Devolución")
        self.caso("Anthropic* Claude Sub", "Compra", "Gasto Fijo", "Suscripciones")
        self.caso("Cruz Verde Calle 80", "Compra", "Gasto Variable", "Salud")
        self.caso("Oxxo Calle 26", "Compra", "Gasto Variable", "Alimentación")
        self.caso("Movistar Pagosepayco", "Compra", "Gasto Fijo", "Servicios")
        self.caso("Procinal Imax", "Compra", "Gasto Variable", "Ocio")
        self.caso("Amazon.Com", "Compra", "Gasto Variable", "Compras")

    def test_comercio_desconocido_se_marca(self):
        c = clasificar("Comercio Raro", D(-1), REGLAS_TARJETA, tipo="Compra")
        self.assertEqual((c.subcategoria, c.revisar), ("Compras", True))

    def test_compra_nueva(self):
        m = lambda **k: MovimientoTarjeta(date(2026, 8, 1), "x", k.pop("tipo", "Compra"), D(100), **k)
        self.assertTrue(_es_nueva(m(cuota=1, cuotas=1, valor_mes=D(100))))
        self.assertFalse(_es_nueva(m(cuota=2, cuotas=24, valor_mes=D(5))))      # cuota de una compra vieja
        self.assertFalse(_es_nueva(m(cuota=1, cuotas=1, valor_mes=D(0))))       # sólo intereses
        self.assertTrue(_es_nueva(m(tipo="Pago")))


class Cuadre(unittest.TestCase):
    def test_cuenta(self):
        ext = ExtractoCuenta(Path("x.pdf"), "ABC", date(2026, 10, 1), date(2026, 10, 31), "01 - 31 OCT 2026",
                             resumen={"inicio": D("100"), "fin": D("150"), "entro": D("80")},
                             movimientos=[Movimiento(date(2026, 10, 2), "Recibiste de X", D("80")),
                                          Movimiento(date(2026, 10, 3), "Enviaste a Y", D("-40")),
                                          Movimiento(date(2026, 10, 31), "Rendimientos Nu", D("10"), es_rendimiento=True)])
        self.assertEqual(cuadrar_cuenta(ext), [])
        ext.movimientos.pop(1)
        self.assertTrue(cuadrar_cuenta(ext))

    def test_tarjeta(self):
        compra = MovimientoTarjeta(date(2026, 8, 2), "Oxxo", "Compra", D(100), 1, 1, valor_mes=D(100),
                                   intereses=D(3), comisiones=D(1), total=D(103))
        pago = MovimientoTarjeta(date(2026, 8, 3), "Gracias por tu pago", "Pago", D(50), total=D(-50))
        ext = ExtractoTarjeta(Path("x.pdf"), "1234", date(2026, 8, 29), date(2026, 9, 18), date(2026, 7, 29),
                              date(2026, 8, 28), "Agosto", movimientos=[compra, pago],
                              resumen={"deuda_mes": D(100), "intereses": D(3), "conversion": D(1),
                                       "abonos": D(-50), "pago_minimo": D(54)})
        self.assertEqual(cuadrar_tarjeta(ext), [])
        compra.intereses = D(0)
        self.assertTrue(cuadrar_tarjeta(ext))


class Fechas(unittest.TestCase):
    def test_serial_excel(self):
        self.assertEqual(fecha_a_serial(date(2026, 10, 31)), 46326)
        self.assertEqual(serial_a_fecha(46326), date(2026, 10, 31))


if __name__ == "__main__":
    unittest.main()
