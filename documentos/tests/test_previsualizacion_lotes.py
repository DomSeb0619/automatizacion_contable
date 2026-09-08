from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from shutil import copy2
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase

from documentos.models import DocumentoLote, Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.consulta_documentos import read_consulta_documento
from documentos.services.previsualizacion_lotes import LoteNoEncontrado, get_batch_preview
from documentos.services.procesamiento_lotes import process_temporary_files


class PrevisualizacionLotesTests(TestCase):
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

    def processed_preview(self) -> dict:
        result = process_temporary_files(self.proyecto, [self.temporary_copy("control.xls")])
        return get_batch_preview(result.lote_id)

    def test_complete_preview_for_2175_separates_company_and_project(self) -> None:
        preview = self.processed_preview()

        self.assertEqual(preview["empresa"], "MC-INTERVALLES S.C.C.")
        self.assertEqual(preview["proyecto"], {"codigo": "IZARI", "nombre": "IZARI"})
        self.assertEqual(preview["cantidad_advertencias"], 1)
        self.assertTrue(preview["puede_exportar"])

    def test_orders_lines_and_serializes_amounts_with_two_decimals(self) -> None:
        document = self.processed_preview()["documentos"][0]

        self.assertEqual([line["orden"] for line in document["lineas"]], [1, 2, 3, 4])
        self.assertEqual([line["etiqueta_tipo"] for line in document["lineas"]], ["Débito", "Débito", "Crédito", "Crédito"])
        self.assertEqual([line["valor"] for line in document["lineas"]], ["4872.30", "458.45", "455.69", "4875.06"])
        self.assertEqual(document["total_debito"], "5330.75")
        self.assertEqual(document["diferencia"], "0.00")

    def test_vat_warning_is_visible_without_blocking_export(self) -> None:
        document = self.processed_preview()["documentos"][0]

        self.assertTrue(document["puede_exportar"])
        self.assertEqual(document["errores"], [])
        self.assertIn("CONFLICTO_IVA_MAESTRO", [warning["code"] for warning in document["advertencias"]])

    def test_error_and_duplicate_documents_are_not_exportable(self) -> None:
        error_path = self.temporary_copy("error.xls")
        with patch("documentos.services.procesamiento_lotes.read_consulta_documento", return_value=replace(self.source_document, project="OTRO")):
            result = process_temporary_files(self.proyecto, [error_path])
        error_document = get_batch_preview(result.lote_id)["documentos"][0]
        self.assertFalse(error_document["puede_exportar"])

        duplicate_result = process_temporary_files(self.proyecto, [self.temporary_copy("a.xls"), self.temporary_copy("b.xls")])
        duplicate_document = next(
            item for item in get_batch_preview(duplicate_result.lote_id)["documentos"] if item["estado"] == DocumentoLote.Estado.DUPLICADO
        )
        self.assertFalse(duplicate_document["puede_exportar"])

    def test_mixed_batch_exports_only_valid_documents(self) -> None:
        valid_path = self.temporary_copy("valid.xls")
        invalid_path = self.temporary_copy("invalid.xls")
        invalid_path.write_bytes(invalid_path.read_bytes() + b"different")
        with patch(
            "documentos.services.procesamiento_lotes.read_consulta_documento",
            side_effect=[self.source_document, replace(self.source_document, company="OTRA")],
        ):
            result = process_temporary_files(self.proyecto, [valid_path, invalid_path])
        preview = get_batch_preview(result.lote_id)

        self.assertTrue(preview["puede_exportar"])
        self.assertEqual(sum(document["puede_exportar"] for document in preview["documentos"]), 1)
        self.assertEqual(preview["cantidad_con_errores"], 1)

    def test_batch_without_exportable_documents_and_document_without_lines(self) -> None:
        lote = LoteCarga.objects.create(proyecto=self.proyecto, estado=LoteCarga.Estado.CON_ERRORES, cantidad_archivos=1)
        DocumentoLote.objects.create(
            lote=lote,
            nombre_archivo_original="sin_lineas.xls",
            hash_sha256="a" * 64,
            estado=DocumentoLote.Estado.VALIDO,
            diferencia=Decimal("0.00"),
        )

        preview = get_batch_preview(lote.id)
        self.assertFalse(preview["documentos"][0]["puede_exportar"])
        self.assertFalse(preview["puede_exportar"])

    def test_handles_an_unknown_batch(self) -> None:
        with self.assertRaises(LoteNoEncontrado):
            get_batch_preview(999999)

    def test_preview_does_not_modify_persisted_records(self) -> None:
        result = process_temporary_files(self.proyecto, [self.temporary_copy("readonly.xls")])
        lote = LoteCarga.objects.get(pk=result.lote_id)
        document = lote.documentos.get()
        before = (lote.estado, lote.total_debito, lote.total_credito, document.estado, document.mensajes, document.lineas.count())

        get_batch_preview(lote.id)

        lote.refresh_from_db()
        document.refresh_from_db()
        after = (lote.estado, lote.total_debito, lote.total_credito, document.estado, document.mensajes, document.lineas.count())
        self.assertEqual(after, before)
