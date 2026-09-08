from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
import xlrd

from documentos.models import Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.consulta_documentos import OriginalAccount, read_consulta_documento
from documentos.services.exportacion_erp import export_batch
from documentos.services.procesamiento_lotes import process_temporary_files
from documentos.services.reclasificacion import CategoryGeneralAccount, ProductVat, RubroAccount, build_reclassification


class Factura5347MotorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.document = read_consulta_documento(Path(__file__).parent / "fixtures" / "factura_5347.xls")
        self.rubros = [
            RubroAccount(self.document.company, self.document.project, "1.2.3.5.3.02", "IZEREC2"),
            RubroAccount(self.document.company, self.document.project, "1.2.3.5.3.03", "IZEREC3"),
            RubroAccount(self.document.company, self.document.project, "1.2.4.2.04", "IZEEP4"),
            RubroAccount(self.document.company, self.document.project, "1.2.4.3.01", "IZEEB1"),
        ]
        self.categories = [CategoryGeneralAccount(self.document.company, self.document.project, "MATERIALES", "101031005")]

    def build(self, **overrides):
        return build_reclassification(
            overrides.get("document", self.document),
            overrides.get("rubros", self.rubros),
            overrides.get("products", []),
            overrides.get("categories", self.categories),
        )

    def test_missing_products_and_configured_account_are_warnings_when_original_is_unique(self) -> None:
        result = self.build()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_debits, Decimal("8740.70"))
        self.assertEqual(result.total_credits, Decimal("8740.70"))
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertEqual(
            [(entry.rubro, entry.account, entry.amount, entry.debit_credit) for entry in result.entries],
            [
                ("1.2.3.5.3.02 - Porcelanato en escaleras", "IZEREC2", Decimal("1038.32"), "1"),
                ("1.2.3.5.3.03 - Porcelanato en hall de circulacion comunales en general", "IZEREC3", Decimal("4949.28"), "1"),
                ("1.2.4.2.04 - Recubrimiento exteriores de piscina", "IZEEP4", Decimal("920.54"), "1"),
                ("1.2.4.3.01 - Recubrimiento de piso incluye desperdicio 10", "IZEEB1", Decimal("1832.56"), "1"),
                (None, "101031001", Decimal("8740.70"), "2"),
            ],
        )
        warning_codes = [warning.code for warning in result.warnings]
        self.assertEqual(warning_codes.count("PRODUCTO_SIN_IVA_MAESTRO"), 5)
        self.assertIn("CONFLICTO_CUENTA_GENERAL_MAPEO", warning_codes)

    def test_multiple_vat_rates_without_distribution_relation_blocks(self) -> None:
        products = [
            ProductVat("IZ-1011042", "MATERIALES", Decimal("0.05")),
            ProductVat("IZ-1011043", "MATERIALES", Decimal("0.15")),
        ]

        result = self.build(products=products)

        self.assertFalse(result.is_valid)
        self.assertIn("CASO_AMBIGUO_IVA", [error.code for error in result.errors])

    def test_multiple_original_accounts_without_relation_blocks(self) -> None:
        accounts = (
            OriginalAccount("INVENTARIOS", "101031001 - INVENTARIO A", Decimal("4370.35"), Decimal("0.00"), "", ""),
            OriginalAccount("INVENTARIOS", "101031002 - INVENTARIO B", Decimal("4370.35"), Decimal("0.00"), "", ""),
        )

        result = self.build(document=replace(self.document, original_accounts=accounts))

        self.assertFalse(result.is_valid)
        self.assertIn("CASO_AMBIGUO_CUENTA_GENERAL", [error.code for error in result.errors])


class Factura5347PipelineTests(TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parent / "fixtures" / "factura_5347.xls"
        self.fixture_bytes = self.fixture.read_bytes()
        source = read_consulta_documento(self.fixture)
        self.empresa = Empresa.objects.create(nombre=source.company, codigo=normalize_identifier(source.company))
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo=source.project, nombre=source.project)
        for code, account in [
            ("1.2.3.5.3.02", "IZEREC2"),
            ("1.2.3.5.3.03", "IZEREC3"),
            ("1.2.4.2.04", "IZEEP4"),
            ("1.2.4.3.01", "IZEEB1"),
        ]:
            MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro=code, tipo="V", cuenta_contable=account)
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="MATERIALES", cuenta_general_original="101031005")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="MATERIALES", cuenta_general_original="101031001")

    def temporary_copy(self, name: str) -> Path:
        directory = settings.BASE_DIR / ".test_artifacts" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        copy2(self.fixture, path)
        return path

    def processed_lote(self) -> LoteCarga:
        result = process_temporary_files(self.proyecto, [self.temporary_copy("factura_5347.xls")])
        return LoteCarga.objects.get(pk=result.lote_id)

    def test_processes_and_exports_the_four_debits_and_one_original_credit(self) -> None:
        lote = self.processed_lote()
        document = lote.documentos.get()

        self.assertEqual(document.lineas.count(), 5)
        self.assertEqual(lote.total_debito, Decimal("8740.70"))
        self.assertEqual(lote.total_credito, Decimal("8740.70"))
        self.assertEqual(lote.diferencia, Decimal("0.00"))
        self.assertEqual(document.estado, document.Estado.ADVERTENCIA)
        self.assertNotIn("CONFLICTO_CUENTA_GENERAL_MAPEO", [item["code"] for item in document.mensajes])
        self.assertEqual(document.lineas.filter(tipo_movimiento="1").count(), 4)
        self.assertEqual(document.lineas.filter(tipo_movimiento="2", cuenta="101031001", valor=Decimal("8740.70")).count(), 1)

        exported = export_batch(lote.id)
        sheet = xlrd.open_workbook(file_contents=exported.content).sheet_by_name("DIARIO_CONT")
        self.assertEqual(sheet.nrows, 6)
        self.assertEqual([(sheet.cell_value(row, 0), int(sheet.cell_value(row, 2))) for row in range(1, 6)], [
            ("IZEREC2", 1), ("IZEREC3", 1), ("IZEEP4", 1), ("IZEEB1", 1), ("101031001", 2)
        ])

    def test_interface_processes_5347_with_warnings_and_preview(self) -> None:
        with TemporaryDirectory() as directory, override_settings(UPLOAD_TEMP_DIR=Path(directory) / "uploads"):
            response = self.client.post(
                reverse("documentos:cargar_lote"),
                {"proyecto": self.proyecto.id, "archivos": SimpleUploadedFile("factura_5347.xls", self.fixture_bytes)},
            )

        self.assertEqual(response.status_code, 302)
        lote = LoteCarga.objects.get(pk=int(response.url.rstrip("/").split("/")[-1]))
        preview = self.client.get(response.url)
        self.assertContains(preview, "101031001")
        self.assertContains(preview, "debe actualizarse el maestro")
