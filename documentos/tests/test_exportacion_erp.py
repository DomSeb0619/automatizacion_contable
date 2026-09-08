from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase
import xlrd

from documentos.models import DocumentoLote, Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.consulta_documentos import read_consulta_documento
from documentos.services.exportacion_erp import (
    OLE2_MAGIC,
    ErrorExportacion,
    export_batch,
    journal_detail,
    validate_line,
)
from documentos.services.procesamiento_lotes import process_temporary_files


class ExportacionErpTests(TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"
        self.source_document = read_consulta_documento(self.fixture)
        empresa = Empresa.objects.create(nombre=self.source_document.company, codigo=normalize_identifier(self.source_document.company))
        self.proyecto = Proyecto.objects.create(empresa=empresa, codigo=self.source_document.project, nombre=self.source_document.project)
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro="1.2.3.2.3.05", descripcion="Cubierta", tipo="V", cuenta_contable="IZEEHE5")
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro="1.2.3.2.3.07", descripcion="Escaleras", tipo="V", cuenta_contable="IZEEHE7")
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-101135", porcentaje_iva=Decimal("15"), categoria="MATERIALES")
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-301004", porcentaje_iva=Decimal("15"), categoria="EQUIPO Y MAQUINARIA")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="MATERIALES", cuenta_general_original="101031005")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria="EQUIPO Y MAQUINARIA", cuenta_general_original="101031003")

    def temporary_copy(self, name: str) -> Path:
        directory = settings.BASE_DIR / ".test_artifacts" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        copy2(self.fixture, path)
        return path

    def processed_lote(self) -> LoteCarga:
        result = process_temporary_files(self.proyecto, [self.temporary_copy("control.xls")])
        return LoteCarga.objects.get(pk=result.lote_id)

    def test_exports_2175_as_real_biff_with_exact_rows(self) -> None:
        lote = self.processed_lote()
        exported = export_batch(lote.id)
        workbook = xlrd.open_workbook(file_contents=exported.content, formatting_info=True)
        sheet = workbook.sheet_by_name("DIARIO_CONT")

        self.assertTrue(exported.content.startswith(OLE2_MAGIC))
        self.assertEqual(workbook.biff_version, 80)
        self.assertEqual(workbook.sheet_names(), ["DIARIO_CONT"])
        self.assertEqual(sheet.row_values(0), ["Cuenta", "Valor", "Debito/Credito", "Detalle", "numero", "CC01", "CC02", "CC03", "CC04", "CC05"])
        self.assertEqual(sheet.nrows, 5)
        self.assertEqual(sheet.ncols, 10)
        self.assertEqual(
            [(sheet.cell_value(row, 0), sheet.cell_value(row, 1), int(sheet.cell_value(row, 2))) for row in range(1, 5)],
            [("IZEEHE5", 4872.30, 1), ("IZEEHE7", 458.45, 1), ("101031003", 455.69, 2), ("101031005", 4875.06, 2)],
        )
        self.assertEqual(sheet.cell_type(1, 0), xlrd.XL_CELL_TEXT)
        self.assertEqual(sheet.cell_type(1, 1), xlrd.XL_CELL_NUMBER)
        self.assertEqual(sheet.cell_value(1, 3), "RECLASIFICACION F 2175 HOLCIM ECUADOR S.A.")
        self.assertTrue(all(sheet.cell_value(row, column) == "" for row in range(1, 5) for column in range(4, 10)))
        self.assertEqual(exported.filename, f"diario_reclasificacion_lote_{lote.id}.xls")
        self.assertEqual(exported.content_type, "application/vnd.ms-excel")

    def test_marks_only_included_documents_and_lote_as_exported(self) -> None:
        lote = self.processed_lote()
        export_batch(lote.id)
        lote.refresh_from_db()

        self.assertEqual(lote.estado, LoteCarga.Estado.EXPORTADO)
        self.assertEqual(lote.documentos.get().estado, DocumentoLote.Estado.EXPORTADO)

    def test_redownload_is_identical_and_does_not_change_states(self) -> None:
        lote = self.processed_lote()
        first = export_batch(lote.id)
        second = export_batch(lote.id)
        lote.refresh_from_db()

        self.assertEqual(first.content, second.content)
        self.assertEqual(lote.estado, LoteCarga.Estado.EXPORTADO)
        self.assertEqual(lote.documentos.get().estado, DocumentoLote.Estado.EXPORTADO)

    def test_mixed_batch_exports_only_valid_document(self) -> None:
        valid_path = self.temporary_copy("valid.xls")
        invalid_path = self.temporary_copy("invalid.xls")
        invalid_path.write_bytes(invalid_path.read_bytes() + b"different")
        with patch(
            "documentos.services.procesamiento_lotes.read_consulta_documento",
            side_effect=[self.source_document, replace(self.source_document, project="OTRO")],
        ):
            result = process_temporary_files(self.proyecto, [valid_path, invalid_path])
        lote = LoteCarga.objects.get(pk=result.lote_id)
        exported = export_batch(lote.id)

        self.assertEqual(xlrd.open_workbook(file_contents=exported.content).sheet_by_index(0).nrows, 5)
        self.assertEqual(lote.documentos.get(estado=DocumentoLote.Estado.ERROR).estado, DocumentoLote.Estado.ERROR)
        self.assertEqual(lote.documentos.get(estado=DocumentoLote.Estado.EXPORTADO).lineas.count(), 4)

    def test_rejects_lote_without_exportable_documents(self) -> None:
        lote = LoteCarga.objects.create(proyecto=self.proyecto, estado=LoteCarga.Estado.CON_ERRORES, cantidad_archivos=1)
        DocumentoLote.objects.create(lote=lote, nombre_archivo_original="error.xls", hash_sha256="a" * 64, estado=DocumentoLote.Estado.ERROR)

        with self.assertRaises(ErrorExportacion):
            export_batch(lote.id)
        lote.refresh_from_db()
        self.assertEqual(lote.estado, LoteCarga.Estado.CON_ERRORES)

    def test_rejects_unbalanced_document_without_state_change(self) -> None:
        lote = self.processed_lote()
        document = lote.documentos.get()
        document.diferencia = Decimal("0.01")
        document.save(update_fields=["diferencia"])

        with self.assertRaises(ErrorExportacion):
            export_batch(lote.id)
        document.refresh_from_db()
        self.assertEqual(document.estado, DocumentoLote.Estado.ADVERTENCIA)

    def test_rejects_empty_account_and_nonpositive_value(self) -> None:
        lote = self.processed_lote()
        line = lote.documentos.get().lineas.first()
        line.cuenta = ""
        line.save(update_fields=["cuenta"])
        with self.assertRaises(ErrorExportacion):
            export_batch(lote.id)

        line.cuenta = "IZEEHE5"
        line.valor = Decimal("0.00")
        line.save(update_fields=["cuenta", "valor"])
        with self.assertRaises(ErrorExportacion):
            export_batch(lote.id)

        line.valor = Decimal("-1.00")
        line.save(update_fields=["valor"])
        with self.assertRaises(ErrorExportacion):
            export_batch(lote.id)

    def test_rejects_invalid_type_before_writing(self) -> None:
        lote = self.processed_lote()
        line = lote.documentos.get().lineas.first()
        line.tipo_movimiento = "9"
        with self.assertRaises(ErrorExportacion):
            validate_line(line)

    def test_generation_failure_preserves_states(self) -> None:
        lote = self.processed_lote()
        with patch("documentos.services.exportacion_erp.build_xls", side_effect=OSError("sin disco")):
            with self.assertRaises(OSError):
                export_batch(lote.id)
        lote.refresh_from_db()
        self.assertEqual(lote.estado, LoteCarga.Estado.VALIDADO)
        self.assertEqual(lote.documentos.get().estado, DocumentoLote.Estado.ADVERTENCIA)

    def test_exported_invoice_blocks_a_new_lote(self) -> None:
        lote = self.processed_lote()
        export_batch(lote.id)
        second = process_temporary_files(self.proyecto, [self.temporary_copy("repetida.xls")])

        self.assertEqual(LoteCarga.objects.get(pk=second.lote_id).documentos.get().estado, DocumentoLote.Estado.DUPLICADO)

    def test_detail_formatter_keeps_full_number_persisted_but_uses_2175_for_diary(self) -> None:
        lote = self.processed_lote()
        document = lote.documentos.get()

        self.assertEqual(document.numero_factura_normalizado, "033-002-000002175")
        self.assertEqual(journal_detail(document), "RECLASIFICACION F 2175 HOLCIM ECUADOR S.A.")
