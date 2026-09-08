from decimal import Decimal
from io import StringIO
from pathlib import Path
from uuid import uuid4

from django.core.management import call_command
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from openpyxl import Workbook

from documentos.models import Empresa, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.catalogos_sqlite import (
    CatalogoNoResuelto,
    catalogos_para_documento,
    resolve_categoria_cuenta,
    resolve_producto_iva,
    resolve_rubro_final,
)
from documentos.services.consulta_documentos import read_consulta_documento
from documentos.services.reclasificacion import build_reclassification


class CatalogosSQLiteTests(TestCase):
    def artifact_directory(self) -> Path:
        directory = settings.BASE_DIR / ".test_artifacts" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create_scope(self):
        empresa = Empresa.objects.create(nombre="MC-INTERVALLES S.C.C.", codigo="mc-intervalles-scc")
        return empresa, Proyecto.objects.create(empresa=empresa, codigo="IZARI", nombre="IZARI")

    def rubros_workbook(self, directory: Path, account: str = "IZEEHE5") -> Path:
        path = directory / "rubros.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Hoja2"
        sheet.append(["CODIGO DE PROYECTO", "CODIGO DEL RUBRO PADRE", "CODIGO DE RUBRO", "DESCRIPCION DEL RUBRO", "TIPO", "CUENTAS"])
        sheet.append(["3", "0", "1", "IZARI", "P", "1010309"])
        sheet.append(["3", "1", "1.2.3.2.3.05", "Hormigon cubierta", "V", account])
        workbook.save(path)
        return path

    def products_workbook(self, directory: Path, invalid: bool = False) -> Path:
        path = directory / "productos.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["codprod01", "desprod01", "porciva01"])
        sheet.append(["IZ-101135", "Hormigon", "9999.99" if invalid else "15.00"])
        sheet.append(["IZ-301004", "Alquiler", "15.00"])
        workbook.save(path)
        return path

    def run_command(self, command: str, path: Path, *extra: str) -> tuple[str, str]:
        output = StringIO()
        errors = StringIO()
        call_command(command, str(path), *extra, stdout=output, stderr=errors)
        return output.getvalue(), errors.getvalue()

    def test_unique_constraints(self) -> None:
        empresa, proyecto = self.create_scope()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Empresa.objects.create(nombre="Otra", codigo="MC-INTERVALLES-SCC")
        MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1.1", descripcion="", tipo="V", cuenta_contable="A")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1.1", descripcion="", tipo="V", cuenta_contable="B")
        MapeoProductoIVA.objects.create(proyecto=proyecto, codigo_producto="P-1", porcentaje_iva=Decimal("15"))
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                MapeoProductoIVA.objects.create(proyecto=proyecto, codigo_producto="P-1", porcentaje_iva=Decimal("15"))
        MapeoCategoriaCuenta.objects.create(proyecto=proyecto, categoria="MATERIALES", cuenta_general_original="101")
        MapeoCategoriaCuenta.objects.create(proyecto=proyecto, categoria="MATERIALES", cuenta_general_original="102")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                MapeoCategoriaCuenta.objects.create(proyecto=proyecto, categoria="MATERIALES", cuenta_general_original="101")

    def test_initial_import_and_idempotent_reimport(self) -> None:
        directory = self.artifact_directory()
        rubros = self.rubros_workbook(directory)
        products = self.products_workbook(directory)
        output, _ = self.run_command("importar_rubros", rubros)
        self.assertIn("creados=2", output)
        output, _ = self.run_command("importar_productos_iva", products)
        self.assertIn("creados=2", output)
        self.assertEqual(MapeoRubroCuenta.objects.count(), 2)
        self.assertEqual(MapeoProductoIVA.objects.count(), 2)
        output, _ = self.run_command("importar_rubros", rubros)
        self.assertIn("omitidos=2", output)
        output, _ = self.run_command("importar_productos_iva", products)
        self.assertIn("omitidos=2", output)
        self.assertEqual(MapeoRubroCuenta.objects.count(), 2)
        self.assertEqual(MapeoProductoIVA.objects.count(), 2)

    def test_import_updates_an_existing_record(self) -> None:
        directory = self.artifact_directory()
        self.run_command("importar_rubros", self.rubros_workbook(directory, "IZEEHE5"))
        output, _ = self.run_command("importar_rubros", self.rubros_workbook(directory, "ACTUALIZADA"))
        self.assertIn("actualizados=1", output)
        self.assertEqual(MapeoRubroCuenta.objects.get(codigo_rubro="1.2.3.2.3.05").cuenta_contable, "ACTUALIZADA")

    def test_dry_run_does_not_write_to_database(self) -> None:
        output, _ = self.run_command("importar_productos_iva", self.products_workbook(self.artifact_directory()), "--dry-run")
        self.assertIn("creados=2", output)
        self.assertEqual(Empresa.objects.count(), 0)
        self.assertEqual(MapeoProductoIVA.objects.count(), 0)

    def test_rejects_invalid_vat_rate(self) -> None:
        output, errors = self.run_command("importar_productos_iva", self.products_workbook(self.artifact_directory(), invalid=True))
        self.assertIn("creados=1", output)
        self.assertIn("errores=1", output)
        self.assertIn("fuera de rango", errors)
        self.assertFalse(MapeoProductoIVA.objects.filter(codigo_producto="IZ-101135").exists())

    def test_resolves_only_final_v_rubros(self) -> None:
        _, proyecto = self.create_scope()
        MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1", descripcion="Padre", tipo="P", cuenta_contable="100")
        final = MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1.1", descripcion="Final", tipo="V", cuenta_contable="101")
        self.assertEqual(resolve_rubro_final(proyecto, "1.1"), final)
        with self.assertRaises(CatalogoNoResuelto):
            resolve_rubro_final(proyecto, "1")

    def test_resolves_product_vat_and_category_account(self) -> None:
        empresa, proyecto = self.create_scope()
        product = MapeoProductoIVA.objects.create(
            proyecto=proyecto, codigo_producto="IZ-101135", descripcion="Hormigon", porcentaje_iva=Decimal("15"), categoria="MATERIALES"
        )
        category = MapeoCategoriaCuenta.objects.create(
            proyecto=proyecto, categoria="MATERIALES", cuenta_general_original="101031005"
        )
        self.assertEqual(resolve_producto_iva(proyecto, "iz-101135"), product)
        self.assertEqual(resolve_categoria_cuenta(proyecto, "materiales"), category)

    def test_control_case_uses_persisted_catalogs(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"
        document = read_consulta_documento(fixture)
        empresa = Empresa.objects.create(nombre=document.company, codigo=normalize_identifier(document.company))
        proyecto = Proyecto.objects.create(empresa=empresa, codigo=document.project, nombre=document.project)
        MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1.2.3.2.3.05", descripcion="Cubierta", tipo="V", cuenta_contable="IZEEHE5")
        MapeoRubroCuenta.objects.create(proyecto=proyecto, codigo_rubro="1.2.3.2.3.07", descripcion="Escaleras", tipo="V", cuenta_contable="IZEEHE7")
        MapeoProductoIVA.objects.create(proyecto=proyecto, codigo_producto="IZ-101135", descripcion="Hormigon", porcentaje_iva=Decimal("15"), categoria="MATERIALES")
        MapeoProductoIVA.objects.create(proyecto=proyecto, codigo_producto="IZ-301004", descripcion="Alquiler", porcentaje_iva=Decimal("15"), categoria="EQUIPO Y MAQUINARIA")
        MapeoCategoriaCuenta.objects.create(proyecto=proyecto, categoria="MATERIALES", cuenta_general_original="101031005")
        MapeoCategoriaCuenta.objects.create(proyecto=proyecto, categoria="EQUIPO Y MAQUINARIA", cuenta_general_original="101031003")

        catalogs = catalogos_para_documento(document)
        result = build_reclassification(document, catalogs.rubro_accounts, catalogs.product_vats, catalogs.category_general_accounts)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_debits, Decimal("5330.75"))
        self.assertEqual(result.total_credits, Decimal("5330.75"))
        self.assertEqual([entry.account for entry in result.entries[:2]], ["IZEEHE5", "IZEEHE7"])
