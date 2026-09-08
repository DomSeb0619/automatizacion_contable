from django.core.management.base import BaseCommand
from django.db import transaction

from documentos.models import MapeoProductoIVA, Proyecto
from documentos.services.categorias_predeterminadas import categoria_predeterminada


class Command(BaseCommand):
    help = "Asigna categorias por prefijo solo a productos que aun no tienen categoria."

    def add_arguments(self, parser):
        parser.add_argument("--proyecto", required=True, type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        proyecto = Proyecto.objects.get(pk=options["proyecto"])
        updated = 0
        with transaction.atomic():
            for product in MapeoProductoIVA.objects.filter(proyecto=proyecto, categoria="").iterator():
                category = categoria_predeterminada(product.codigo_producto)
                if not category:
                    continue
                updated += 1
                if not options["dry_run"]:
                    product.categoria = category
                    product.save(update_fields=["categoria", "actualizado_en"])
            if options["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(f"Productos clasificados={updated}; proyecto={proyecto.codigo}; dry_run={options['dry_run']}")
