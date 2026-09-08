from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from documentos.models import Empresa, MapeoProductoIVA, MapeoRubroCuenta, Proyecto, normalize_identifier
from documentos.services.configuracion_catalogos import CatalogoInvalido, ConfirmacionInvalida, aplicar_previsualizacion, previsualizar_catalogo


class ConfiguracionCatalogosTests(TestCase):
    def setUp(self) -> None:
        self.empresa = Empresa.objects.create(nombre='Empresa Uno', codigo=normalize_identifier('Empresa Uno'))
        self.proyecto = Proyecto.objects.create(empresa=self.empresa, codigo='UNO', nombre='Proyecto Uno')
        self.otra_empresa = Empresa.objects.create(nombre='Empresa Dos', codigo=normalize_identifier('Empresa Dos'))
        self.otro_proyecto = Proyecto.objects.create(empresa=self.otra_empresa, codigo='DOS', nombre='Proyecto Dos')
        self.temp_directory = TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)

    def workbook(self, name, headers, rows, sheet_name='Hoja1') -> Path:
        path = Path(self.temp_directory.name) / name
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        return path

    def product_workbook(self, rows) -> Path:
        return self.workbook('productos.xlsx', ['CODPROD01', 'DESPROD01', 'PORCIVA01'], rows)

    def rubro_workbook(self, rows) -> Path:
        return self.workbook('rubros.xlsx', ['CODIGO DE PROYECTO', 'CODIGO DEL RUBRO PADRE', 'CODIGO DE RUBRO', 'DESCRIPCION DEL RUBRO', 'TIPO', 'CUENTAS'], rows, 'Hoja2')

    def test_preview_and_confirmation_create_update_and_skip_without_duplicates(self) -> None:
        MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto='P-1', descripcion='Anterior', porcentaje_iva=Decimal('12'))
        path = self.product_workbook([('P-1', 'Actualizado', 15), ('P-2', 'Nuevo', 5)])

        preview = previsualizar_catalogo(self.proyecto, 'productos', path)

        self.assertEqual((preview.creados, preview.actualizados, preview.omitidos, preview.rechazados), (1, 1, 0, 0))
        self.assertEqual(MapeoProductoIVA.objects.count(), 1)
        applied = aplicar_previsualizacion(preview.as_session())
        self.assertEqual((applied.creados, applied.actualizados, applied.omitidos), (1, 1, 0))
        self.assertEqual(MapeoProductoIVA.objects.filter(proyecto=self.proyecto).count(), 2)

        second = previsualizar_catalogo(self.proyecto, 'productos', path)
        self.assertEqual((second.creados, second.actualizados, second.omitidos, second.rechazados), (0, 0, 2, 0))
        applied_second = aplicar_previsualizacion(second.as_session())
        self.assertEqual((applied_second.creados, applied_second.actualizados, applied_second.omitidos), (0, 0, 2))
        self.assertEqual(MapeoProductoIVA.objects.filter(proyecto=self.proyecto).count(), 2)

    def test_rejected_rows_require_acceptance_and_are_excluded_after_confirmation(self) -> None:
        existing = MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto='P-1', descripcion='Anterior', porcentaje_iva=Decimal('12'))
        path = self.product_workbook([('P-1', 'No debe cambiar', 15), ('P-2', 'Invalido', 9999.99)])
        preview = previsualizar_catalogo(self.proyecto, 'productos', path)

        self.assertEqual(preview.rechazados, 1)
        with self.assertRaises(ConfirmacionInvalida):
            aplicar_previsualizacion(preview.as_session())
        applied = aplicar_previsualizacion(preview.as_session(), acepta_rechazados=True)
        self.assertEqual((applied.actualizados, applied.rechazados), (1, 1))
        existing.refresh_from_db()
        self.assertEqual(existing.descripcion, 'No debe cambiar')
        self.assertEqual(existing.porcentaje_iva, Decimal('15.0000'))
        self.assertFalse(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto='P-2').exists())

    def test_rubro_preview_is_scoped_to_project_and_validates_rows(self) -> None:
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto, codigo_rubro='1.1', descripcion='Anterior', tipo='V', cuenta_contable='A1')
        MapeoRubroCuenta.objects.create(proyecto=self.otro_proyecto, codigo_rubro='1.1', descripcion='Otro', tipo='V', cuenta_contable='B1')
        path = self.rubro_workbook([('UNO', '', '1.1', 'Actualizado', 'V', 'A2'), ('UNO', '', '1.2', 'Invalido', 'X', '')])

        preview = previsualizar_catalogo(self.proyecto, 'rubros', path)

        self.assertEqual((preview.creados, preview.actualizados, preview.rechazados), (0, 1, 1))
        aplicar_previsualizacion(preview.as_session(), acepta_rechazados=True)
        self.assertEqual(MapeoRubroCuenta.objects.get(proyecto=self.proyecto, codigo_rubro='1.1').cuenta_contable, 'A2')
        self.assertEqual(MapeoRubroCuenta.objects.get(proyecto=self.otro_proyecto, codigo_rubro='1.1').cuenta_contable, 'B1')

    def test_full_master_accepts_6445_valid_rows_and_excludes_two_invalid_vat_rows(self) -> None:
        rows = [(f'PROD-{index:04d}', f'Producto {index}', 15) for index in range(1, 6446)]
        rows.extend([('INVALIDO-1', 'No guardar', 9999.99), ('INVALIDO-2', 'No guardar', 9999.99)])
        preview = previsualizar_catalogo(self.proyecto, 'productos', self.product_workbook(rows))

        self.assertEqual((preview.creados, preview.actualizados, preview.omitidos, preview.rechazados), (6445, 0, 0, 2))
        rejected = [row for row in preview.filas if row['estado'] == 'RECHAZADO']
        self.assertEqual([(row['fila'], row['codigo'], row['valor_problematico']) for row in rejected], [(6447, 'INVALIDO-1', 9999.99), (6448, 'INVALIDO-2', 9999.99)])
        aplicar_previsualizacion(preview.as_session(), acepta_rechazados=True)
        self.assertEqual(MapeoProductoIVA.objects.filter(proyecto=self.proyecto).count(), 6445)
        self.assertFalse(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto__startswith='INVALIDO').exists())

    def test_structural_error_blocks_even_when_rejected_rows_are_accepted(self) -> None:
        invalid_scope = self.rubro_workbook([('OTRO', '', '1.1', 'Rubro', 'V', 'A1')])
        with self.assertRaises(CatalogoInvalido):
            previsualizar_catalogo(self.proyecto, 'rubros', invalid_scope)
        payload = {'tipo': 'productos', 'proyecto_id': self.proyecto.id, 'filas': [], 'creados': 0, 'actualizados': 0, 'omitidos': 0, 'rechazados': 1, 'errores': ['Encabezados ausentes']}
        with self.assertRaises(ConfirmacionInvalida):
            aplicar_previsualizacion(payload, acepta_rechazados=True)

    def test_unexpected_valid_row_failure_rolls_back_entire_confirmation(self) -> None:
        preview = previsualizar_catalogo(self.proyecto, 'productos', self.product_workbook([('P-1', 'Uno', 15), ('P-2', 'Dos', 15)]))
        from documentos.services.configuracion_catalogos import apply_producto as original_apply
        calls = {'count': 0}

        def fail_after_first(*args):
            calls['count'] += 1
            if calls['count'] == 2:
                raise RuntimeError('fallo inesperado')
            return original_apply(*args)

        with patch('documentos.services.configuracion_catalogos.apply_producto', side_effect=fail_after_first):
            with self.assertRaises(RuntimeError):
                aplicar_previsualizacion(preview.as_session())
        self.assertEqual(MapeoProductoIVA.objects.filter(proyecto=self.proyecto).count(), 0)

    def test_manual_product_rubro_and_category_are_saved_in_selected_scope(self) -> None:
        product_response = self.client.post(reverse('documentos:configurar_catalogos'), {
            'accion': 'guardar_producto', 'proyecto': self.proyecto.id, 'codigo_producto': 'NUEVO', 'descripcion': 'Producto confirmado',
            'porcentaje_iva': '15.0000', 'categoria': 'MATERIALES', 'activo': 'on',
        })
        rubro_response = self.client.post(reverse('documentos:configurar_catalogos'), {
            'accion': 'guardar_rubro', 'proyecto': self.proyecto.id, 'codigo_rubro': '9.9', 'descripcion': 'Rubro manual',
            'tipo': 'V', 'cuenta_contable': 'CTA-9', 'activo': 'on',
        })
        category_response = self.client.post(reverse('documentos:configurar_catalogos'), {
            'accion': 'guardar_categoria', 'proyecto': self.proyecto.id, 'categoria': 'MATERIALES',
            'cuenta_general_original': '101031001', 'descripcion': 'Referencia', 'activo': 'on',
        })
        second_category_response = self.client.post(reverse('documentos:configurar_catalogos'), {
            'accion': 'guardar_categoria', 'proyecto': self.proyecto.id, 'categoria': 'MATERIALES',
            'cuenta_general_original': '101031002', 'descripcion': 'Segunda referencia', 'activo': 'on',
        })
        deactivate_response = self.client.post(reverse('documentos:configurar_catalogos'), {
            'accion': 'guardar_categoria', 'proyecto': self.proyecto.id, 'categoria': 'MATERIALES',
            'cuenta_general_original': '101031002', 'descripcion': 'Segunda referencia',
        })

        self.assertEqual(product_response.status_code, 302)
        self.assertEqual(rubro_response.status_code, 302)
        self.assertEqual(category_response.status_code, 302)
        self.assertEqual(second_category_response.status_code, 302)
        self.assertEqual(deactivate_response.status_code, 302)
        self.assertTrue(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto='NUEVO', porcentaje_iva=Decimal('15')).exists())
        self.assertTrue(MapeoRubroCuenta.objects.filter(proyecto=self.proyecto, codigo_rubro='9.9', cuenta_contable='CTA-9').exists())
        self.assertEqual(self.proyecto.mapeos_categoria.get(categoria='MATERIALES', cuenta_general_original='101031001').cuenta_general_original, '101031001')
        self.assertEqual(self.proyecto.mapeos_categoria.filter(categoria='MATERIALES').count(), 2)
        self.assertFalse(self.proyecto.mapeos_categoria.get(categoria='MATERIALES', cuenta_general_original='101031002').activo)
        self.assertFalse(MapeoProductoIVA.objects.filter(proyecto=self.otro_proyecto, codigo_producto='NUEVO').exists())

    def test_interface_preview_confirmation_search_and_scope_isolation(self) -> None:
        MapeoProductoIVA.objects.create(proyecto=self.otro_proyecto, codigo_producto='AISLADO', descripcion='No mostrar', porcentaje_iva=Decimal('15'))
        source = self.product_workbook([('WEB-1', 'Producto web', 15)])
        with override_settings(UPLOAD_TEMP_DIR=Path(self.temp_directory.name) / 'uploads'):
            response = self.client.post(reverse('documentos:configurar_catalogos'), {
                'accion': 'previsualizar', 'proyecto': self.proyecto.id, 'tipo': 'productos',
                'archivo': SimpleUploadedFile('productos.xlsx', source.read_bytes()),
            })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Creados')
        self.assertContains(response, 'Confirmar y guardar')
        confirmed = self.client.post(reverse('documentos:confirmar_catalogo'))
        self.assertEqual(confirmed.status_code, 302)
        response = self.client.get(f"{reverse('documentos:configurar_catalogos')}?proyecto={self.proyecto.id}&q=WEB-1")
        self.assertContains(response, 'WEB-1')
        self.assertNotContains(response, 'AISLADO')

    def test_interface_requires_checkbox_before_excluding_rejected_rows(self) -> None:
        source = self.product_workbook([('WEB-OK', 'Valido', 15), ('WEB-BAD', 'Invalido', 9999.99)])
        with override_settings(UPLOAD_TEMP_DIR=Path(self.temp_directory.name) / 'uploads'):
            preview = self.client.post(reverse('documentos:configurar_catalogos'), {
                'accion': 'previsualizar', 'proyecto': self.proyecto.id, 'tipo': 'productos',
                'archivo': SimpleUploadedFile('productos.xlsx', source.read_bytes()),
            })
        self.assertContains(preview, 'Confirmo que las filas rechazadas serán excluidas')
        blocked = self.client.post(reverse('documentos:confirmar_catalogo'))
        self.assertEqual(blocked.status_code, 200)
        self.assertFalse(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto='WEB-OK').exists())
        accepted = self.client.post(reverse('documentos:confirmar_catalogo'), {'acepta_rechazados': 'on'})
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto='WEB-OK').exists())
        self.assertFalse(MapeoProductoIVA.objects.filter(proyecto=self.proyecto, codigo_producto='WEB-BAD').exists())
