"""Revisión auditable de equivalencias VERDEPAMBA sin persistir datos."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from documentos.services.consulta_documentos_verdepamba import read_consulta_verdepamba


@dataclass(frozen=True)
class EquivalenciaRevisada:
    producto: str
    destino_catalogo: str
    destino_manual: str
    categoria_contable: str
    confirmada: bool


def revisar_equivalencias_rhr(factura: str | Path, catalogo: str | Path, diario_manual: str | Path) -> list[EquivalenciaRevisada]:
    """Compara por secuencia documental y códigos, nunca por importe solamente."""
    document = read_consulta_verdepamba(factura)
    catalog_workbook = load_workbook(catalogo, read_only=True, data_only=True)
    catalog_sheet = catalog_workbook.active
    catalog = {
        str(row[0]).strip(): str(row[2]).strip()
        for row in catalog_sheet.iter_rows(min_row=3, values_only=True)
        if len(row) >= 3 and row[0] and row[2]
    }
    catalog_workbook.close()
    manual_workbook = load_workbook(diario_manual, read_only=True, data_only=True)
    manual_sheet = manual_workbook.active
    manual = [row for row in manual_sheet.iter_rows(min_row=12, max_row=153, values_only=True) if row[3]]
    manual_workbook.close()
    if len(manual) != len(document.products):
        raise ValueError("El diario manual no contiene una línea por producto para revisión.")
    result = []
    unused = list(manual)
    for product in document.products:
        expected = catalog.get(product.code, "")
        # El diario está ordenado por categoría, no por los productos de la
        # factura. El importe solo reduce candidatos; la confirmación exige
        # además coincidencia exacta de código destino. No se usa por el motor.
        candidates = [row for row in unused if Decimal(str(row[5] or 0)).quantize(Decimal("0.01")) == product.total]
        row = next((candidate for candidate in candidates if str(candidate[3]).split(" - ", 1)[0].strip() == expected), candidates[0] if candidates else ("", "", "", "", "", ""))
        if row in unused:
            unused.remove(row)
        actual = str(row[3]).split(" - ", 1)[0].strip()
        category = str(row[1]).split(" - ", 1)[0].strip()
        result.append(EquivalenciaRevisada(product.code, expected, actual, category, bool(expected) and expected == actual))
    return result
