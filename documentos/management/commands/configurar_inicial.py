from django.core.management.base import BaseCommand
from django.db import transaction

from documentos.management.commands._catalogo_base import (
    CODIGO_EMPRESA_INICIAL,
    EMPRESA_INICIAL,
    PROYECTO_INICIAL,
)
from documentos.models import Empresa, MapeoCategoriaCuenta, MapeoProductoIVA, Proyecto


class Command(BaseCommand):
    help = "Configura empresa, proyecto y relaciones necesarias para el caso inicial."

    def handle(self, *args, **options):
        with transaction.atomic():
            empresa, _ = Empresa.objects.update_or_create(
                codigo=CODIGO_EMPRESA_INICIAL,
                defaults={"nombre": EMPRESA_INICIAL, "activa": True},
            )
            proyecto, _ = Proyecto.objects.update_or_create(
                empresa=empresa,
                codigo=PROYECTO_INICIAL,
                defaults={"nombre": PROYECTO_INICIAL, "activo": True},
            )
            for category, account in {
                "MATERIALES": "101031005",
                "EQUIPO Y MAQUINARIA": "101031003",
            }.items():
                MapeoCategoriaCuenta.objects.update_or_create(
                    proyecto=proyecto,
                    categoria=category,
                    cuenta_general_original=account,
                    defaults={"descripcion": "Configuracion inicial", "activo": True},
                )
            for product, category in {
                "IZ-101135": "MATERIALES",
                "IZ-301004": "EQUIPO Y MAQUINARIA",
            }.items():
                MapeoProductoIVA.objects.filter(proyecto=proyecto, codigo_producto=product).update(categoria=category)
        self.stdout.write("Configuracion inicial aplicada.")
