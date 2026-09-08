from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from documentos.models import DocumentoLote, Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.consulta_documentos import OriginalAccount, read_consulta_documento
from documentos.services.exportacion_erp import export_batch
from documentos.services.historial_lotes import get_batch_history
from documentos.services.procesamiento_lotes import process_temporary_files
from documentos.services.reclasificacion import CategoryGeneralAccount, RubroAccount, build_reclassification


class MultipleCategoryAccountsTests(SimpleTestCase):
    def setUp(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        self.document_2175 = read_consulta_documento(fixtures / "factura_2175.xls")
        self.document_5347 = read_consulta_documento(fixtures / "factura_5347.xls")

    def test_2175_and_5347_accept_two_allowed_material_accounts_without_conflict(self) -> None:
        accounts_2175 = [
            CategoryGeneralAccount(self.document_2175.company, self.document_2175.project, "MATERIALES", "101031005"),
            CategoryGeneralAccount(self.document_2175.company, self.document_2175.project, "MATERIALES", "101031001"),
            CategoryGeneralAccount(self.document_2175.company, self.document_2175.project, "EQUIPO Y MAQUINARIA", "101031003"),
        ]
        accounts_5347 = [
            CategoryGeneralAccount(self.document_5347.company, self.document_5347.project, "MATERIALES", "101031005"),
            CategoryGeneralAccount(self.document_5347.company, self.document_5347.project, "MATERIALES", "101031001"),
        ]
        rubros_2175 = [
            RubroAccount(self.document_2175.company, self.document_2175.project, "1.2.3.2.3.05", "IZEEHE5"),
            RubroAccount(self.document_2175.company, self.document_2175.project, "1.2.3.2.3.07", "IZEEHE7"),
        ]
        rubros_5347 = [
            RubroAccount(self.document_5347.company, self.document_5347.project, code, account)
            for code, account in [("1.2.3.5.3.02", "A"), ("1.2.3.5.3.03", "B"), ("1.2.4.2.04", "C"), ("1.2.4.3.01", "D")]
        ]

        result_2175 = build_reclassification(self.document_2175, rubros_2175, [], accounts_2175)
        result_5347 = build_reclassification(self.document_5347, rubros_5347, [], accounts_5347)

        self.assertTrue(result_2175.is_valid)
        self.assertTrue(result_5347.is_valid)
        self.assertNotIn("CONFLICTO_CUENTA_GENERAL_MAPEO", [warning.code for warning in result_2175.warnings])
        self.assertNotIn("CONFLICTO_CUENTA_GENERAL_MAPEO", [warning.code for warning in result_5347.warnings])

    def test_new_inequivocal_account_warns_and_two_matching_accounts_block(self) -> None:
        rubros = [
            RubroAccount(self.document_5347.company, self.document_5347.project, code, account)
            for code, account in [("1.2.3.5.3.02", "A"), ("1.2.3.5.3.03", "B"), ("1.2.4.2.04", "C"), ("1.2.4.3.01", "D")]
        ]
        warned = build_reclassification(
            self.document_5347, rubros, [],
            [CategoryGeneralAccount(self.document_5347.company, self.document_5347.project, "MATERIALES", "999999999")],
        )
        ambiguous_accounts = (
            OriginalAccount("INVENTARIOS", "101031001 - A", Decimal("4370.35"), Decimal("0.00"), "", ""),
            OriginalAccount("INVENTARIOS", "101031002 - B", Decimal("4370.35"), Decimal("0.00"), "", ""),
        )
        blocked = build_reclassification(
            replace(self.document_5347, original_accounts=ambiguous_accounts), rubros, [],
            [
                CategoryGeneralAccount(self.document_5347.company, self.document_5347.project, "MATERIALES", "101031001"),
                CategoryGeneralAccount(self.document_5347.company, self.document_5347.project, "MATERIALES", "101031002"),
            ],
        )

        self.assertTrue(warned.is_valid)
        self.assertIn("CONFLICTO_CUENTA_GENERAL_MAPEO", [warning.code for warning in warned.warnings])
        self.assertFalse(blocked.is_valid)
        self.assertIn("CASO_AMBIGUO_CUENTA_GENERAL", [error.code for error in blocked.errors])


class HistorialLotesTests(TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"
        source = read_consulta_documento(self.fixture)
        self.source = source
        self.empresa = Empresa.objects.create(nombre=source.company, codigo=normalize_identifier(source.company))
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo=source.project, nombre=source.project)
        for code, account in [("1.2.3.2.3.05", "IZEEHE5"), ("1.2.3.2.3.07", "IZEEHE7")]:
            MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro=code, tipo="V", cuenta_contable=account)
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-101135", porcentaje_iva=Decimal("15"), categoria="MATERIALES")
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-301004", porcentaje_iva=Decimal("15"), categoria="EQUIPO Y MAQUINARIA")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="MATERIALES", cuenta_general_original="101031005")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="EQUIPO Y MAQUINARIA", cuenta_general_original="101031003")

    def temporary_copy(self, name: str) -> Path:
        directory = settings.BASE_DIR / ".test_artifacts" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        copy2(self.fixture, target)
        return target

    def processed_lote(self, name="control.xls") -> LoteCarga:
        result = process_temporary_files(self.proyecto, [self.temporary_copy(name)])
        return LoteCarga.objects.get(pk=result.lote_id)

    def test_empty_history_and_search_by_lote_provider_ruc_and_invoice(self) -> None:
        response = self.client.get(reverse("documentos:historial_lotes"))
        self.assertContains(response, "No hay lotes para mostrar.")
        lote = self.processed_lote()
        for query in [str(lote.id), "HOLCIM", self.source.supplier_tax_id, "000002175"]:
            response = self.client.get(reverse("documentos:historial_lotes"), {"q": query})
            self.assertContains(response, f"#{lote.id}")
        self.assertEqual(get_batch_history(str(lote.id))["lotes"][0]["id"], lote.id)

    def test_duplicate_links_to_original_exported_batch_and_history_redownloads(self) -> None:
        original = self.processed_lote("original.xls")
        first_download = export_batch(original.id)
        duplicate = self.processed_lote("duplicate.xls")
        document = duplicate.documentos.get()

        self.assertEqual(document.estado, DocumentoLote.Estado.DUPLICADO)
        response = self.client.get(reverse("documentos:previsualizar_lote", args=[duplicate.id]))
        self.assertContains(response, f"Esta factura ya fue exportada en el lote #{original.id}.")
        self.assertContains(response, reverse("documentos:previsualizar_lote", args=[original.id]))
        original_page = self.client.get(reverse("documentos:previsualizar_lote", args=[original.id]))
        self.assertContains(original_page, "Incluida en el diario exportado")
        second_download = self.client.post(reverse("documentos:exportar_lote", args=[original.id]))
        self.assertEqual(second_download.content, first_download.content)

    def test_status_texts_for_pending_export_error_duplicate_and_exported(self) -> None:
        lote = self.processed_lote()
        valid_page = self.client.get(reverse("documentos:previsualizar_lote", args=[lote.id]))
        self.assertContains(valid_page, "Se incluirá en la exportación")
        export_batch(lote.id)
        exported_page = self.client.get(reverse("documentos:previsualizar_lote", args=[lote.id]))
        self.assertContains(exported_page, "Incluida en el diario exportado")

        errors = LoteCarga.objects.create(proyecto=self.proyecto, estado=LoteCarga.Estado.CON_ERRORES, cantidad_archivos=2)
        DocumentoLote.objects.create(lote=errors, nombre_archivo_original="error.xls", hash_sha256="a" * 64, estado=DocumentoLote.Estado.ERROR)
        DocumentoLote.objects.create(lote=errors, nombre_archivo_original="duplicate.xls", hash_sha256="b" * 64, estado=DocumentoLote.Estado.DUPLICADO)
        error_page = self.client.get(reverse("documentos:previsualizar_lote", args=[errors.id]))
        self.assertContains(error_page, "Excluida de la exportación", count=2)

    def test_history_filters_and_pagination_do_not_return_every_batch(self) -> None:
        for number in range(22):
            LoteCarga.objects.create(proyecto=self.proyecto, estado=LoteCarga.Estado.VALIDADO)
        response = self.client.get(reverse("documentos:historial_lotes"), {
            "empresa": self.empresa.id, "proyecto": self.proyecto.id, "estado": LoteCarga.Estado.VALIDADO,
        })
        self.assertContains(response, "Página 1")
        self.assertEqual(len(get_batch_history(empresa=str(self.empresa.id), proyecto=str(self.proyecto.id), estado=LoteCarga.Estado.VALIDADO)["lotes"]), 20)
