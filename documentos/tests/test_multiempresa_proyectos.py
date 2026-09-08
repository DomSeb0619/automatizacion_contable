from decimal import Decimal

from django.test import TestCase

from documentos.models import Empresa, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto
from documentos.services.catalogos_sqlite import catalogos_para_proyecto, resolve_producto_iva
from documentos.services.configuracion_proyecto import resumir_configuracion


class MultiempresaProyectoTests(TestCase):
    def setUp(self):
        self.empresa_a = Empresa.objects.create(nombre="Empresa A", codigo="EMP-A")
        self.empresa_b = Empresa.objects.create(nombre="Empresa B", codigo="EMP-B")
        self.proyecto_a = Proyecto.objects.create(empresa=self.empresa_a, codigo="UNO", nombre="Uno")
        self.proyecto_b = Proyecto.objects.create(empresa=self.empresa_b, codigo="UNO", nombre="Uno")

    def test_same_product_code_is_isolated_by_project(self):
        MapeoProductoIVA.objects.create(proyecto=self.proyecto_a, codigo_producto="COMPARTIDO", porcentaje_iva=Decimal("5"))
        MapeoProductoIVA.objects.create(proyecto=self.proyecto_b, codigo_producto="COMPARTIDO", porcentaje_iva=Decimal("15"))
        self.assertEqual(resolve_producto_iva(self.proyecto_a, "COMPARTIDO").porcentaje_iva, Decimal("5"))
        self.assertEqual(resolve_producto_iva(self.proyecto_b, "COMPARTIDO").porcentaje_iva, Decimal("15"))

    def test_categories_and_accounts_are_exact_and_isolated(self):
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto_a, categoria="MATERIALES", cuenta_general_original="CUENTA-A")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto_b, categoria="MATERIALES", cuenta_general_original="CUENTA-B")
        accounts_a = [item.original_general_account for item in catalogos_para_proyecto(self.proyecto_a).category_general_accounts]
        accounts_b = [item.original_general_account for item in catalogos_para_proyecto(self.proyecto_b).category_general_accounts]
        self.assertEqual(accounts_a, ["CUENTA-A"])
        self.assertEqual(accounts_b, ["CUENTA-B"])

    def test_project_summary_distinguishes_incomplete_and_complete(self):
        self.assertFalse(resumir_configuracion(self.proyecto_a).completa)
        MapeoProductoIVA.objects.create(proyecto=self.proyecto_a, codigo_producto="P1", porcentaje_iva=Decimal("15"))
        MapeoRubroCuenta.objects.create(proyecto=self.proyecto_a, codigo_rubro="R1", tipo="V", cuenta_contable="CTA")
        MapeoCategoriaCuenta.objects.create(proyecto=self.proyecto_a, categoria="MATERIALES", cuenta_general_original="ORIGEN")
        self.assertTrue(resumir_configuracion(self.proyecto_a).completa)

    def test_company_and_project_can_be_deactivated_without_deletion(self):
        self.proyecto_a.activo = False
        self.proyecto_a.save()
        self.empresa_b.activa = False
        self.empresa_b.save()
        self.assertFalse(Proyecto.objects.get(pk=self.proyecto_a.pk).activo)
        self.assertFalse(Empresa.objects.get(pk=self.empresa_b.pk).activa)
