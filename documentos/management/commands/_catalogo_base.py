from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from documentos.models import Empresa, Proyecto, normalize_code, normalize_identifier


EMPRESA_INICIAL = "MC-INTERVALLES S.C.C."
CODIGO_EMPRESA_INICIAL = "MC-INTERVALLES-SCC"
PROYECTO_INICIAL = "IZARI"


@dataclass
class ImportStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    duplicates: int = 0


class CatalogoCommand(BaseCommand):
    def add_scope_arguments(self, parser) -> None:
        parser.add_argument("--empresa", default=EMPRESA_INICIAL)
        parser.add_argument("--codigo-empresa", default=CODIGO_EMPRESA_INICIAL)
        parser.add_argument("--proyecto", default=PROYECTO_INICIAL)
        parser.add_argument("--nombre-proyecto", default=PROYECTO_INICIAL)
        parser.add_argument("--dry-run", action="store_true")

    def validate_source(self, value: str) -> Path:
        path = Path(value)
        if not path.is_file():
            raise CommandError(f"No existe el archivo: {path}")
        if path.suffix.lower() != ".xlsx":
            raise CommandError("El archivo debe tener extension .xlsx.")
        return path

    def get_scope(self, options) -> tuple[Empresa, Proyecto]:
        empresa, _ = Empresa.objects.get_or_create(
            codigo=normalize_identifier(options["codigo_empresa"]),
            defaults={"nombre": options["empresa"], "activa": True},
        )
        proyecto, _ = Proyecto.objects.get_or_create(
            empresa=empresa,
            codigo=normalize_code(options["proyecto"]),
            defaults={"nombre": options["nombre_proyecto"], "activo": True},
        )
        return empresa, proyecto

    def write_stats(self, stats: ImportStats) -> None:
        self.stdout.write(
            "creados={0.created} actualizados={0.updated} omitidos={0.skipped} "
            "duplicados={0.duplicates} errores={0.errors}".format(stats)
        )

    def run_atomic(self, dry_run: bool, operation):
        with transaction.atomic():
            result = operation()
            if dry_run:
                transaction.set_rollback(True)
            return result
