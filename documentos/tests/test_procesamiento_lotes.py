from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase

from documentos.models import DocumentoLote, Empresa, LineaReclasificacion, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.consulta_documentos import read_consulta_documento
from documentos.services.procesamiento_lotes import process_temporary_files, scope_messages


class ProcesamientoLotesTests(TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"
        self.source_document = read_consulta_documento(self.fixture)
        self.empresa = Empresa.objects.create(nombre=self.source_document.company, codigo=normalize_identifier(self.source_document.company))
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo=self.source_document.project, nombre=self.source_document.project)
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro="1.2.3.2.3.05", descripcion="Cubierta", tipo="V", cuenta_contable="IZEEHE5")
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro="1.2.3.2.3.07", descripcion="Escaleras", tipo="V", cuenta_contable="IZEEHE7")
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-101135", descripcion="Hormigon", porcentaje_iva=Decimal("15"), categoria="MATERIALES")
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-301004", descripcion="Alquiler", porcentaje_iva=Decimal("15"), categoria="EQUIPO Y MAQUINARIA")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="MATERIALES", cuenta_general_original="101031005")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="EQUIPO Y MAQUINARIA", cuenta_general_original="101031003")

    def temporary_copy(self, name: str) -> Path:
        directory = settings.BASE_DIR / ".test_artifacts" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        copy2(self.fixture, path)
        return path

    def process_control(self, name: str = "factura_2175.xls"):
        return process_temporary_files(self.proyecto, [self.temporary_copy(name)])

    def test_processes_2175_and_persists_four_final_lines(self) -> None:
        result = self.process_control()
        lote = LoteCarga.objects.get(pk=result.lote_id)
        document = lote.documentos.get()

        self.assertEqual(lote.estado, LoteCarga.Estado.VALIDADO)
        self.assertEqual(document.estado, DocumentoLote.Estado.ADVERTENCIA)
        self.assertEqual(document.lineas.count(), 4)
        self.assertEqual(lote.total_debito, Decimal("5330.75"))
        self.assertEqual(lote.total_credito, Decimal("5330.75"))
        self.assertEqual(lote.diferencia, Decimal("0.00"))
        self.assertEqual(
            list(document.lineas.values_list("cuenta", "valor", "tipo_movimiento")),
            [
                ("IZEEHE5", Decimal("4872.30"), "1"),
                ("IZEEHE7", Decimal("458.45"), "1"),
                ("101031003", Decimal("455.69"), "2"),
                ("101031005", Decimal("4875.06"), "2"),
            ],
        )
        self.assertIn("CONFLICTO_IVA_MAESTRO", [item["code"] for item in document.mensajes])
        self.assertEqual(document.numero_factura_proveedor, "033-002- 000002175")
        self.assertEqual(document.numero_factura_normalizado, "033-002-000002175")

    def test_marks_same_file_twice_in_one_batch_as_duplicate(self) -> None:
        result = process_temporary_files(self.proyecto, [self.temporary_copy("a.xls"), self.temporary_copy("b.xls")])
        lote = LoteCarga.objects.get(pk=result.lote_id)

        self.assertEqual(lote.estado, LoteCarga.Estado.CON_ERRORES)
        self.assertEqual(lote.cantidad_validos, 1)
        self.assertEqual(lote.cantidad_con_errores, 1)
        self.assertTrue(lote.documentos.filter(estado=DocumentoLote.Estado.DUPLICADO).exists())

    def test_same_file_with_another_name_is_allowed_before_export(self) -> None:
        first = self.process_control("primera.xls")
        second = self.process_control("renombrada.xls")

        self.assertEqual(LoteCarga.objects.get(pk=first.lote_id).documentos.get().estado, DocumentoLote.Estado.ADVERTENCIA)
        self.assertEqual(LoteCarga.objects.get(pk=second.lote_id).documentos.get().estado, DocumentoLote.Estado.ADVERTENCIA)

    def test_exported_invoice_blocks_a_future_batch(self) -> None:
        first = self.process_control()
        exported = LoteCarga.objects.get(pk=first.lote_id).documentos.get()
        exported.estado = DocumentoLote.Estado.EXPORTADO
        exported.save(update_fields=["estado"])

        second = self.process_control("despues_exportado.xls")
        blocked = LoteCarga.objects.get(pk=second.lote_id).documentos.get()

        self.assertEqual(blocked.estado, DocumentoLote.Estado.DUPLICADO)
        self.assertEqual(blocked.lineas.count(), 0)
        self.assertIn("FACTURA_EXPORTADA", [item["code"] for item in blocked.mensajes])

    def test_rejects_different_company_without_lines(self) -> None:
        path = self.temporary_copy("otra_empresa.xls")
        with patch("documentos.services.procesamiento_lotes.read_consulta_documento", return_value=replace(self.source_document, company="OTRA EMPRESA")):
            result = process_temporary_files(self.proyecto, [path])
        document = LoteCarga.objects.get(pk=result.lote_id).documentos.get()

        self.assertEqual(document.estado, DocumentoLote.Estado.ERROR)
        self.assertEqual(document.lineas.count(), 0)
        self.assertIn("EMPRESA_DIFERENTE", [item["code"] for item in document.mensajes])

    def test_rejects_different_project_without_lines(self) -> None:
        path = self.temporary_copy("otro_proyecto.xls")
        with patch("documentos.services.procesamiento_lotes.read_consulta_documento", return_value=replace(self.source_document, project="OTRO")):
            result = process_temporary_files(self.proyecto, [path])
        document = LoteCarga.objects.get(pk=result.lote_id).documentos.get()

        self.assertEqual(document.estado, DocumentoLote.Estado.ERROR)
        self.assertEqual(document.lineas.count(), 0)
        self.assertIn("PROYECTO_DIFERENTE", [item["code"] for item in document.mensajes])

    def test_scope_uses_consulta_alias_and_conservative_company_normalization(self) -> None:
        ibis = Empresa.objects.create(nombre="IBIS MILA S.C.C", codigo="IBIS-MILA-SCC")
        project = Proyecto.objects.create(
            empresa=ibis,
            codigo="1",
            codigo_consulta_documentos="IBIS MILA SCC",
            nombre="MILA",
        )
        document = replace(self.source_document, company="IBIS MILA S.C.C.", project="IBIS MILA SCC")
        self.assertEqual(scope_messages(project, document), [])

    def test_scope_falls_back_to_budget_code_and_rejects_wrong_alias_or_company(self) -> None:
        project = Proyecto.objects.create(empresa=self.empresa, codigo="1", nombre="Uno")
        self.assertEqual(scope_messages(project, replace(self.source_document, project="1")), [])
        project.codigo_consulta_documentos = "ALIAS CONSULTA"
        project.save()
        self.assertIn("PROYECTO_DIFERENTE", [item["code"] for item in scope_messages(project, replace(self.source_document, project="OTRO"))])
        self.assertIn("EMPRESA_DIFERENTE", [item["code"] for item in scope_messages(project, replace(self.source_document, company="EMPRESA DISTINTA", project="ALIAS CONSULTA"))])

    def test_a_batch_keeps_valid_document_when_another_is_invalid(self) -> None:
        valid_path = self.temporary_copy("valida.xls")
        invalid_path = self.temporary_copy("invalida.xls")
        invalid_path.write_bytes(invalid_path.read_bytes() + b"different-temporary-content")
        with patch(
            "documentos.services.procesamiento_lotes.read_consulta_documento",
            side_effect=[self.source_document, replace(self.source_document, project="OTRO")],
        ):
            result = process_temporary_files(self.proyecto, [valid_path, invalid_path])
        lote = LoteCarga.objects.get(pk=result.lote_id)

        self.assertEqual(lote.estado, LoteCarga.Estado.CON_ERRORES)
        self.assertEqual(lote.cantidad_validos, 1)
        self.assertEqual(lote.cantidad_con_errores, 1)
        self.assertEqual(lote.documentos.get(estado=DocumentoLote.Estado.ADVERTENCIA).lineas.count(), 4)
        self.assertEqual(lote.documentos.get(estado=DocumentoLote.Estado.ERROR).lineas.count(), 0)

    def test_cleans_temporary_files_after_processing(self) -> None:
        path = self.temporary_copy("limpiar.xls")
        process_temporary_files(self.proyecto, [path])

        self.assertFalse(path.exists())
