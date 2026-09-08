from decimal import Decimal
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from documentos.models import DocumentoLote, Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.procesamiento_lotes import process_temporary_files


class InterfazWebTests(TestCase):
    def setUp(self) -> None:
        self.upload_directory = TemporaryDirectory()
        self.upload_root = Path(self.upload_directory.name) / 'uploads'
        self.upload_settings = override_settings(UPLOAD_TEMP_DIR=self.upload_root)
        self.upload_settings.enable()
        self.addCleanup(self.upload_settings.disable)
        self.addCleanup(self.upload_directory.cleanup)
        self.fixture = Path(__file__).parent / 'fixtures' / 'factura_2175.xls'
        self.fixture_bytes = self.fixture.read_bytes()
        self.empresa = Empresa.objects.create(nombre='MC-INTERVALLES S.C.C.', codigo=normalize_identifier('MC-INTERVALLES S.C.C.'))
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo='IZARI', nombre='IZARI')
        self.inactivo = Proyecto.objects.create(empresa=self.empresa, codigo='INACTIVO', nombre='No disponible', activo=False)
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro='1.2.3.2.3.05', descripcion='Cubierta', tipo='V', cuenta_contable='IZEEHE5')
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro='1.2.3.2.3.07', descripcion='Escaleras', tipo='V', cuenta_contable='IZEEHE7')
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto='IZ-101135', porcentaje_iva=Decimal('15'), categoria='MATERIALES')
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto='IZ-301004', porcentaje_iva=Decimal('15'), categoria='EQUIPO Y MAQUINARIA')
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria='MATERIALES', cuenta_general_original='101031005')
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto, categoria='EQUIPO Y MAQUINARIA', cuenta_general_original='101031003')

    def upload(self, name='factura_2175.xls'):
        file = SimpleUploadedFile(name, self.fixture_bytes, content_type='application/vnd.ms-excel')
        return self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': file})

    def processed_lote(self) -> LoteCarga:
        response = self.upload()
        return LoteCarga.objects.get(pk=int(response.url.rstrip('/').split('/')[-1]))

    def test_home_get_lists_only_active_projects(self) -> None:
        response = self.client.get(reverse('documentos:cargar_lote'))

        self.assertContains(response, 'IZARI')
        self.assertNotContains(response, 'No disponible')
        self.assertContains(response, 'Procesar facturas')

    def test_upload_requires_files(self) -> None:
        response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selecciona al menos un archivo .xls.')

    def test_upload_rejects_invalid_extension_and_ole_signature(self) -> None:
        extension = SimpleUploadedFile('consulta.txt', b'not xls')
        response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': extension})
        self.assertContains(response, 'solo se permiten archivos .xls')

        bad_signature = SimpleUploadedFile('consulta.xls', b'not an ole file')
        response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': bad_signature})
        self.assertContains(response, 'no es un archivo .xls OLE2 válido')

    def test_upload_rejects_renamed_xlsx_large_files_and_too_many_files(self) -> None:
        renamed = SimpleUploadedFile('renombrado.xls', b'PK\x03\x04fake-xlsx')
        response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': renamed})
        self.assertContains(response, 'no es un archivo .xls OLE2 válido')

        with override_settings(MAX_UPLOAD_FILE_SIZE=8):
            large = SimpleUploadedFile('grande.xls', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1extra')
            response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': large})
            self.assertContains(response, 'supera el máximo')

        with override_settings(MAX_UPLOAD_FILES=1):
            first = SimpleUploadedFile('uno.xls', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
            second = SimpleUploadedFile('dos.xls', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
            response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.proyecto.id, 'archivos': [first, second]})
            self.assertContains(response, 'máximo de 1 archivos')

    def test_correct_upload_uses_post_redirect_get_and_cleans_temporaries(self) -> None:
        response = self.upload()

        self.assertEqual(response.status_code, 302)
        self.assertRegex(response.url, r'^/lotes/\d+/$')
        self.assertEqual(LoteCarga.objects.count(), 1)
        self.assertFalse(self.upload_root.exists() and any(self.upload_root.iterdir()))

    def test_preview_shows_warning_four_lines_and_separated_debit_credit(self) -> None:
        lote = self.processed_lote()
        response = self.client.get(reverse('documentos:previsualizar_lote', args=[lote.id]))

        self.assertContains(response, 'maestro 15.00%', status_code=200)
        self.assertContains(response, 'IZEEHE5')
        self.assertContains(response, '$4872.30')
        self.assertContains(response, '$455.69')
        self.assertContains(response, 'Se incluirá en la exportación')
        self.assertContains(response, 'Descargar diario para ERP')

    def test_preview_visually_excludes_error_or_duplicate_documents(self) -> None:
        lote = LoteCarga.objects.create(proyecto=self.proyecto, estado=LoteCarga.Estado.CON_ERRORES, cantidad_archivos=1)
        DocumentoLote.objects.create(lote=lote, nombre_archivo_original='error.xls', hash_sha256='a' * 64, estado=DocumentoLote.Estado.ERROR, mensajes=[{'level': 'error', 'code': 'ERROR', 'message': 'Documento inválido'}])
        response = self.client.get(reverse('documentos:previsualizar_lote', args=[lote.id]))

        self.assertContains(response, 'Excluida de la exportación')
        self.assertContains(response, 'Documento inválido')
        self.assertContains(response, 'No hay facturas exportables')

    def test_export_requires_post_and_returns_attachment_then_redownloads(self) -> None:
        lote = self.processed_lote()
        export_url = reverse('documentos:exportar_lote', args=[lote.id])
        self.assertEqual(self.client.get(export_url).status_code, 405)

        first = self.client.post(export_url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first['Content-Type'], 'application/vnd.ms-excel')
        self.assertEqual(first['Content-Disposition'], f'attachment; filename="diario_reclasificacion_lote_{lote.id}.xls"')
        lote.refresh_from_db()
        self.assertEqual(lote.estado, LoteCarga.Estado.EXPORTADO)
        self.assertEqual(lote.documentos.get().estado, DocumentoLote.Estado.EXPORTADO)

        second = self.client.post(export_url)
        self.assertEqual(b''.join(first.streaming_content) if hasattr(first, 'streaming_content') else first.content, b''.join(second.streaming_content) if hasattr(second, 'streaming_content') else second.content)

    def test_inactive_project_is_not_accepted_and_unknown_lote_is_404(self) -> None:
        response = self.client.post(reverse('documentos:cargar_lote'), {'proyecto': self.inactivo.id, 'archivos': SimpleUploadedFile('ok.xls', self.fixture_bytes)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escoja una opción válida')
        self.assertEqual(self.client.get(reverse('documentos:previsualizar_lote', args=[999999])).status_code, 404)
