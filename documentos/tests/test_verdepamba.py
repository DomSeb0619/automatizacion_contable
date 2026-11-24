from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from documentos.models import Empresa, MapeoCategoriaCuenta, MapeoProductoCuenta, Proyecto
from documentos.services.configuracion_catalogos import aplicar_previsualizacion, previsualizar_catalogo, previsualizar_validacion_rhr
from documentos.services.consulta_documentos_verdepamba import read_consulta_verdepamba
from documentos.services.procesamiento_lotes import scope_messages
from documentos.services.reclasificacion_productos import ProductAccount, build_product_reclassification
from documentos.services.verdepamba_equivalencias import revisar_equivalencias_rhr
from documentos.services.procesamiento_lotes import process_temporary_files
from documentos.services.procesamiento_lotes import correct_product_mapping_for_document
from documentos.services.exportacion_erp import export_batch, export_observations_report, export_preliminary_batch
from documentos.services.previsualizacion_lotes import get_batch_preview
import xlrd


DOWNLOADS = Path.home() / "Downloads"


class VerdepambaReaderTests(SimpleTestCase):
    def test_reads_real_pac_xls_when_available(self):
        path = DOWNLOADS / "Consulta de Documentos-20261123-1642.xls"
        if not path.exists():
            self.skipTest("Archivo .xls PAC local no disponible.")
        document = read_consulta_verdepamba(path)
        self.assertEqual(document.company, "VERDEPAMBA S.A.S.")
        self.assertEqual(len(document.products), 142)
        self.assertEqual(document.subtotal, Decimal("86733.28"))
        self.assertEqual(document.vat, Decimal("13009.99"))
        self.assertEqual(document.total, Decimal("99743.27"))
        self.assertEqual(document.reclasificable_total, Decimal("86733.28"))
        self.assertEqual({account.account.split(" - ", 1)[0] for account in document.original_inventory_accounts}, {"1010312"})

    def test_reads_real_rhr_source_when_available(self):
        path = DOWNLOADS / "fc rhr.xlsx"
        if not path.exists():
            self.skipTest("Archivo local VERDEPAMBA no disponible.")
        document = read_consulta_verdepamba(path)
        self.assertEqual(len(document.products), 142)
        self.assertEqual(document.subtotal, Decimal("86733.28"))
        self.assertEqual(document.vat, Decimal("13009.99"))
        self.assertEqual(document.total, Decimal("99743.27"))
        self.assertEqual(document.reclasificable_total, Decimal("86733.28"))

    def test_real_rhr_review_has_138_confirmed_and_four_pending(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx", "reclasificacion rhr.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        review = revisar_equivalencias_rhr(*paths)
        self.assertEqual(len(review), 142)
        self.assertEqual(sum(item.confirmada for item in review), 138)
        pending = {item.producto: (item.destino_catalogo, item.destino_manual) for item in review if not item.confirmada}
        self.assertEqual(pending, {"C20135": ("CCU2", "CCU1"), "C20137": ("CCU4", "CCU3"), "CE10136": ("CCU7", "CCU5"), "U10114": ("UIC3", "UIC2")})

    def test_rhr_balances_only_after_all_equivalences_are_resolved(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx", "reclasificacion rhr.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        document = read_consulta_verdepamba(paths[0])
        review = revisar_equivalencias_rhr(*paths)
        mappings = [ProductAccount(item.producto, item.destino_manual, item.categoria_contable, "1010312", True) for item in review]
        result = build_product_reclassification(document, mappings)
        self.assertTrue(result.is_valid)
        self.assertEqual(len([entry for entry in result.entries if entry.debit_credit == "1"]), 142)
        self.assertEqual(len([entry for entry in result.entries if entry.debit_credit == "2"]), 1)
        self.assertEqual(result.total_debits, Decimal("86733.28"))
        self.assertEqual(result.total_credits, Decimal("86733.28"))


class VerdepambaProductEngineTests(SimpleTestCase):
    def test_blocks_unvalidated_equivalence_without_creating_a_journal(self):
        from documentos.services.consulta_documentos import ConsultaDocumento, OriginalAccount, Product
        document = ConsultaDocumento(
            company="VERDEPAMBA S.A.S.", project="", supplier="PROVEEDOR FICTICIO", supplier_tax_id="", erp_document_number="ERP-1", supplier_invoice_number="F-1",
            products=(Product("C20135", "Producto", Decimal("1"), Decimal("10"), Decimal("10"), Decimal("10.00")),),
            subtotal=Decimal("10.00"), vat=Decimal("0.00"), total=Decimal("10.00"), distributions=(),
            original_accounts=(OriginalAccount("10103 - INVENTARIOS", "1010312 - INVENTARIO", Decimal("10.00"), Decimal("0"), "", ""),),
        )
        result = build_product_reclassification(document, [ProductAccount("C20135", "CCU2", "10103", "1010312", False)])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.entries, ())
        self.assertEqual(result.errors[0].code, "EQUIVALENCIA_PENDIENTE_VALIDACION")

    def test_one_debit_per_product_and_one_inventory_credit(self):
        from documentos.services.consulta_documentos import ConsultaDocumento, OriginalAccount, Product
        document = ConsultaDocumento(
            company="VERDEPAMBA S.A.S.", project="", supplier="PROVEEDOR FICTICIO", supplier_tax_id="", erp_document_number="ERP-1", supplier_invoice_number="F-1",
            products=(Product("A", "", Decimal("1"), Decimal("10"), Decimal("10"), Decimal("10.00")), Product("B", "", Decimal("1"), Decimal("13.05"), Decimal("13.05"), Decimal("13.05"))),
            subtotal=Decimal("23.05"), vat=Decimal("0"), total=Decimal("23.05"), distributions=(),
            original_accounts=(OriginalAccount("INVENTARIO", "1010312 - INVENTARIO", Decimal("23.05"), Decimal("0"), "", ""),),
        )
        mappings = [ProductAccount("A", "DEST-A", "CAT", "1010312", True), ProductAccount("B", "DEST-B", "CAT", "1010312", True)]
        result = build_product_reclassification(document, mappings)
        self.assertTrue(result.is_valid)
        self.assertEqual([entry.debit_credit for entry in result.entries], ["1", "1", "2"])
        self.assertEqual(result.total_debits, Decimal("23.05"))
        self.assertEqual(result.total_credits, Decimal("23.05"))

    def test_blocks_authorized_product_when_category_account_is_missing(self):
        from documentos.services.consulta_documentos import ConsultaDocumento, OriginalAccount, Product
        document = ConsultaDocumento("VERDEPAMBA S.A.S.", "", "", "", "", "F-1", (Product("A", "", Decimal("1"), Decimal("10"), Decimal("10"), Decimal("10.00")),), Decimal("10"), Decimal("0"), Decimal("10"), (), (OriginalAccount("INVENTARIO", "1010312 - INVENTARIO", Decimal("10"), Decimal("0"), "", ""),))
        result = build_product_reclassification(document, [ProductAccount("A", "DEST", "10103", "1010312", True)], category_accounts=set())
        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors[0].code, "CATEGORIA_CUENTA_NO_AUTORIZADA")


class VerdepambaScopeTests(TestCase):
    def test_empty_project_requires_exactly_one_enabled_product_project(self):
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA")
        project = Proyecto.objects.create(empresa=company, codigo="PAC", nombre="PAC", modalidad_reclasificacion="PRODUCTOS", permite_consulta_sin_proyecto=True)
        from documentos.services.consulta_documentos import ConsultaDocumento
        document = ConsultaDocumento("VERDEPAMBA S.A.S.", "", "", "", "", "", (), Decimal(), Decimal(), Decimal(), (), ())
        self.assertEqual(scope_messages(project, document), [])
        Proyecto.objects.create(empresa=company, codigo="PAC2", nombre="PAC2", modalidad_reclasificacion="PRODUCTOS", permite_consulta_sin_proyecto=True)
        self.assertEqual(scope_messages(project, document)[0]["code"], "PROYECTO_NO_DETERMINABLE")

    def test_product_mapping_is_project_isolated(self):
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA")
        one = Proyecto.objects.create(empresa=company, codigo="UNO", nombre="Uno", modalidad_reclasificacion="PRODUCTOS")
        two = Proyecto.objects.create(empresa=company, codigo="DOS", nombre="Dos", modalidad_reclasificacion="PRODUCTOS")
        MapeoProductoCuenta.objects.create(proyecto=one, codigo_producto="C20135", cuenta_destino="CCU2", categoria_contable="10103", cuenta_inventario_origen="1010312", validada=True)
        MapeoProductoCuenta.objects.create(proyecto=two, codigo_producto="C20135", cuenta_destino="OTRA", categoria_contable="10103", cuenta_inventario_origen="999", validada=True)
        self.assertEqual(one.mapeos_producto_cuenta.get(codigo_producto="C20135").cuenta_destino, "CCU2")
        self.assertEqual(two.mapeos_producto_cuenta.get(codigo_producto="C20135").cuenta_destino, "OTRA")

    def test_rhr_preview_and_confirmation_are_scoped_and_idempotent_when_files_exist(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "reclasificacion rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA-RHR")
        project = Proyecto.objects.create(empresa=company, codigo="RHR", nombre="RHR", modalidad_reclasificacion="PRODUCTOS")
        other = Proyecto.objects.create(empresa=company, codigo="OTRO", nombre="Otro", modalidad_reclasificacion="PRODUCTOS")
        aplicar_previsualizacion(previsualizar_catalogo(project, "equivalencias", paths[2]).as_session())
        preview = previsualizar_validacion_rhr(project, paths[0], paths[1], paths[2])
        self.assertEqual((preview.actualizados, preview.pendientes, preview.detalles["categorias_identificadas"], preview.detalles["cuenta_inventario"]), (138, 539, 39, "1010312"))
        self.assertEqual(sum(row["estado"] == "CONFLICTO" for row in preview.filas), 4)
        aplicar_previsualizacion(preview.as_session())
        self.assertEqual(MapeoProductoCuenta.objects.filter(proyecto=project, validada=True).count(), 138)
        self.assertEqual(MapeoProductoCuenta.objects.filter(proyecto=project, validada=False).count(), 539)
        self.assertEqual(MapeoCategoriaCuenta.objects.filter(proyecto=project, cuenta_general_original="1010312").count(), 39)
        self.assertFalse(MapeoProductoCuenta.objects.filter(proyecto=other).exists())
        again = previsualizar_validacion_rhr(project, paths[0], paths[1], paths[2])
        aplicar_previsualizacion(again.as_session())
        self.assertEqual(MapeoCategoriaCuenta.objects.filter(proyecto=project, cuenta_general_original="1010312").count(), 39)

    def test_rhr_interface_preview_does_not_save_before_confirmation_when_files_exist(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "reclasificacion rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA-UI")
        project = Proyecto.objects.create(empresa=company, codigo="UI", nombre="UI", modalidad_reclasificacion="PRODUCTOS")
        aplicar_previsualizacion(previsualizar_catalogo(project, "equivalencias", paths[2]).as_session())
        response = self.client.post(reverse("documentos:configurar_catalogos"), {
            "accion": "previsualizar_rhr", "proyecto": project.id,
            "factura": SimpleUploadedFile(paths[0].name, paths[0].read_bytes()),
            "diario_manual": SimpleUploadedFile(paths[1].name, paths[1].read_bytes()),
            "catalogo": SimpleUploadedFile(paths[2].name, paths[2].read_bytes()),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Por validar")
        self.assertContains(response, "1010312")
        self.assertEqual(MapeoProductoCuenta.objects.filter(proyecto=project, validada=True).count(), 0)

    def test_monthly_rhr_processing_uses_only_invoice_and_saved_mappings(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "reclasificacion rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA-MENSUAL")
        project = Proyecto.objects.create(empresa=company, codigo="MENSUAL", nombre="Mensual", modalidad_reclasificacion="PRODUCTOS", permite_consulta_sin_proyecto=True)
        review = revisar_equivalencias_rhr(paths[0], paths[2], paths[1])
        for item in review:
            MapeoProductoCuenta.objects.create(
                proyecto=project, codigo_producto=item.producto, cuenta_destino=item.destino_catalogo,
                categoria_contable=item.categoria_contable if item.confirmada else "",
                cuenta_inventario_origen="1010312" if item.confirmada else "", validada=item.confirmada,
            )
            if item.confirmada:
                MapeoCategoriaCuenta.objects.get_or_create(
                    proyecto=project, categoria=item.categoria_contable, cuenta_general_original="1010312",
                    defaults={"descripcion": "Prueba RHR", "activo": True},
                )
        result = process_temporary_files(project, [paths[0]], cleanup=False)
        document = project.lotes.get(pk=result.lote_id).documentos.get()
        self.assertEqual(document.estado, "ERROR")
        self.assertEqual(sum(message["code"] == "EQUIVALENCIA_PENDIENTE_VALIDACION" for message in document.mensajes), 4)
        self.assertEqual(document.lineas.filter(tipo_movimiento="1").count(), 138)
        self.assertEqual(document.lineas.filter(tipo_movimiento="2").count(), 1)
        self.assertEqual(document.diferencia, Decimal("0.00"))
        preview = get_batch_preview(document.lote_id)
        self.assertTrue(preview["documentos"][0]["puede_descargar_preliminar"])

    def test_pending_equivalences_allow_balanced_preliminary_and_observations(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "reclasificacion rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA-PRELIM")
        project = Proyecto.objects.create(empresa=company, codigo="PRELIM", nombre="Prelim", modalidad_reclasificacion="PRODUCTOS", permite_consulta_sin_proyecto=True)
        for item in revisar_equivalencias_rhr(paths[0], paths[2], paths[1]):
            MapeoProductoCuenta.objects.create(proyecto=project, codigo_producto=item.producto, cuenta_destino=item.destino_catalogo, categoria_contable=item.categoria_contable if item.confirmada else "", cuenta_inventario_origen="1010312" if item.confirmada else "", validada=item.confirmada)
            if item.confirmada:
                MapeoCategoriaCuenta.objects.get_or_create(proyecto=project, categoria=item.categoria_contable, cuenta_general_original="1010312")
        processed = process_temporary_files(project, [paths[0]], cleanup=False)
        document = project.lotes.get(pk=processed.lote_id).documentos.get()
        preliminary = export_preliminary_batch(document.lote_id)
        observations = export_observations_report(document.lote_id)
        journal = xlrd.open_workbook(file_contents=preliminary.content).sheet_by_name("DIARIO_CONT")
        report = xlrd.open_workbook(file_contents=observations.content).sheet_by_name("OBSERVACIONES")
        self.assertEqual(journal.nrows, document.lineas.count() + 1)
        self.assertEqual(report.nrows, 5)
        self.assertIn("preliminar", preliminary.filename)
        document.refresh_from_db()
        self.assertEqual(document.estado, "ERROR")

    def test_rhr_manual_corrections_reprocess_and_export_complete_journal(self):
        paths = [DOWNLOADS / name for name in ("fc rhr.xlsx", "reclasificacion rhr.xlsx", "PARA RECLASIFICACION VERDEPAMBA.xlsx")]
        if not all(path.exists() for path in paths):
            self.skipTest("Archivos locales VERDEPAMBA no disponibles.")
        company = Empresa.objects.create(nombre="VERDEPAMBA S.A.S.", codigo="VERDEPAMBA-COMPLETE")
        project = Proyecto.objects.create(empresa=company, codigo="COMPLETE", nombre="Complete", modalidad_reclasificacion="PRODUCTOS", permite_consulta_sin_proyecto=True)
        review = revisar_equivalencias_rhr(paths[0], paths[2], paths[1])
        for item in review:
            MapeoProductoCuenta.objects.create(proyecto=project, codigo_producto=item.producto, cuenta_destino=item.destino_catalogo, categoria_contable=item.categoria_contable if item.confirmada else "", cuenta_inventario_origen="1010312" if item.confirmada else "", validada=item.confirmada)
            if item.confirmada:
                MapeoCategoriaCuenta.objects.get_or_create(proyecto=project, categoria=item.categoria_contable, cuenta_general_original="1010312")
        processed = process_temporary_files(project, [paths[0]], cleanup=False)
        document = project.lotes.get(pk=processed.lote_id).documentos.get()
        conflicts = [item for item in review if not item.confirmada]
        self.assertEqual(len([item for item in document.mensajes if item.get("revision")]), 4)
        for item in conflicts:
            MapeoCategoriaCuenta.objects.get_or_create(proyecto=project, categoria=item.categoria_contable, cuenta_general_original="1010312")
            document = correct_product_mapping_for_document(
                document.id, code=item.producto, destination=item.destino_manual,
                category=item.categoria_contable, inventory_account="1010312", justification="Revisión contable RHR aprobada.",
            )
        document.refresh_from_db()
        self.assertEqual(document.estado, "VALIDO")
        self.assertEqual(document.lineas.filter(tipo_movimiento="1").count(), 142)
        self.assertEqual(document.lineas.filter(tipo_movimiento="2").count(), 1)
        self.assertEqual(document.total_debito, Decimal("86733.28"))
        self.assertEqual(document.total_credito, Decimal("86733.28"))
        exported = export_batch(document.lote_id)
        sheet = xlrd.open_workbook(file_contents=exported.content).sheet_by_name("DIARIO_CONT")
        self.assertEqual(sheet.nrows, 144)
        self.assertFalse(any("IVA" in str(sheet.cell_value(row, 0)) or "PROVEEDOR" in str(sheet.cell_value(row, 0)) for row in range(1, sheet.nrows)))
