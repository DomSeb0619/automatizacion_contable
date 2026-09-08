from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from documentos.models import Empresa, MapeoProductoIVA, Proyecto
from documentos.services.categorias_predeterminadas import categoria_predeterminada


class CategoriasPredeterminadasTests(TestCase):
    def setUp(self):
        empresa = Empresa.objects.create(nombre="Empresa", codigo="EMP")
        self.proyecto = Proyecto.objects.create(empresa=empresa, codigo="UNO", nombre="Uno")

    def test_prefixes_accept_codes_with_or_without_iz(self):
        self.assertEqual(categoria_predeterminada("IZ-101066"), "MATERIALES")
        self.assertEqual(categoria_predeterminada("201999"), "MANO DE OBRA")
        self.assertEqual(categoria_predeterminada("IZ-301001"), "ALQUILERES Y SERVICIOS")
        self.assertEqual(categoria_predeterminada("401010"), "SUBCONTRATOS")
        self.assertEqual(categoria_predeterminada("IZ-999001"), "")

    def test_command_only_fills_empty_categories_and_does_not_overwrite_manual_data(self):
        blank = MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-101066", porcentaje_iva=Decimal("15"))
        manual = MapeoProductoIVA.objects.create(proyecto=self.proyecto, codigo_producto="IZ-201001", porcentaje_iva=Decimal("15"), categoria="CATEGORIA MANUAL")
        call_command("asignar_categorias_predeterminadas", proyecto=self.proyecto.id)
        blank.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(blank.categoria, "MATERIALES")
        self.assertEqual(manual.categoria, "CATEGORIA MANUAL")
