"""Lector aislado para el reporte ERP llamado "Consulta de Documentos".

El lector extrae hechos contables del archivo fuente. No calcula la
reclasificacion ni usa catalogos: esas responsabilidades se agregaran despues.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
from typing import Any

import xlrd


CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Product:
    code: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    net_price: Decimal
    total: Decimal


@dataclass(frozen=True)
class Distribution:
    project: str
    rubro: str
    category: str
    gross_total: Decimal


@dataclass(frozen=True)
class OriginalAccount:
    category: str
    account: str
    debit: Decimal
    credit: Decimal
    cost_center: str
    detail: str


@dataclass(frozen=True)
class ConsultaDocumento:
    company: str
    project: str
    supplier: str
    supplier_tax_id: str
    erp_document_number: str
    supplier_invoice_number: str
    products: tuple[Product, ...]
    subtotal: Decimal
    vat: Decimal
    total: Decimal
    distributions: tuple[Distribution, ...]
    original_accounts: tuple[OriginalAccount, ...]

    @property
    def original_inventory_accounts(self) -> tuple[OriginalAccount, ...]:
        """Cuentas que se revertiran como credito en el diario futuro."""
        return tuple(
            account
            for account in self.original_accounts
            if account.debit > 0 and "INVENTARIO" in account.account.upper()
        )

    @property
    def reclasificable_total(self) -> Decimal:
        """Base a reclasificar, reconciliada con los debitos de inventario."""
        return money(sum((account.debit for account in self.original_inventory_accounts), Decimal()))


class ConsultaDocumentoError(ValueError):
    """El archivo no contiene la estructura esperada por el lector."""


def read_consulta_documento(path: str | Path) -> ConsultaDocumento:
    """Lee un archivo `.xls` de Consulta de Documentos sin modificarlo."""
    source = Path(path)
    if source.suffix.lower() != ".xls":
        raise ConsultaDocumentoError("Se esperaba un archivo con extension .xls.")

    # Algunos reportes ERP tienen una cadena OLE repetida; xlrd puede leer sus hojas
    # BIFF si se ignora esa inconsistencia de contenedor.
    workbook = xlrd.open_workbook(source, ignore_workbook_corruption=True)
    sheet = workbook.sheet_by_index(0)
    rows = [sheet.row_values(index) for index in range(sheet.nrows)]

    product_header = find_header_row(rows, "PRODUCTO", "P.TOTAL")
    distribution_header = find_header_row(rows, "PROYECTO", "RUBRO", "CATEGORIA PRODUCTO", "TOTAL DISTRIBUIDO")
    accounting_header = find_header_row(rows, "CATEGORIA", "CUENTA", "DEBITO", "CREDITO")

    company = company_name(cell(rows[0], 0))
    project = next_nonempty(rows, distribution_header + 1, 0, "proyecto")
    supplier = value_after_label(rows, "PROVEEDOR:")
    supplier_tax_id = value_after_label(rows, "RUC:")
    erp_document_number = value_after_label(rows, "NUMERO:")
    supplier_invoice_number = as_text(value_after_label(rows, "NUMERO DOCUMENTO PROVEEDOR:"))

    products = parse_products(rows, product_header)
    subtotal = value_after_label(rows, "SUBTOTAL 1:")
    vat = value_after_label(rows, "I.V.A. %:")
    total = value_after_label(rows, "T O T A L:")
    distributions = parse_distributions(rows, distribution_header)
    original_accounts = parse_original_accounts(rows, accounting_header)

    return ConsultaDocumento(
        company=as_text(company),
        project=as_text(project),
        supplier=as_text(supplier),
        supplier_tax_id=normalize_invoice_number(supplier_tax_id),
        erp_document_number=normalize_invoice_number(erp_document_number),
        supplier_invoice_number=as_text(supplier_invoice_number),
        products=tuple(products),
        subtotal=money(subtotal),
        vat=money(vat),
        total=money(total),
        distributions=tuple(distributions),
        original_accounts=tuple(original_accounts),
    )


def parse_products(rows: list[list[Any]], header_row: int) -> list[Product]:
    header = header_map(rows[header_row])
    products: list[Product] = []
    for row in rows[header_row + 1 :]:
        product_value = cell(row, column(header, "PRODUCTO"))
        if as_text(cell(row, 0)).upper().startswith("TOTAL"):
            break
        if not as_text(product_value):
            continue
        code, description = split_product(as_text(product_value))
        products.append(
            Product(
                code=code,
                description=description,
                quantity=decimal_value(cell(row, column(header, "CANT."))),
                unit_price=decimal_value(cell(row, column(header, "P.UNIT."))),
                net_price=decimal_value(cell(row, column(header, "P.NETO"))),
                total=money(cell(row, column(header, "P.TOTAL"))),
            )
        )
    if not products:
        raise ConsultaDocumentoError("No se encontraron productos.")
    return products


def parse_distributions(rows: list[list[Any]], header_row: int) -> list[Distribution]:
    header = header_map(rows[header_row])
    distributions: list[Distribution] = []
    for row in rows[header_row + 1 :]:
        project = as_text(cell(row, column(header, "PROYECTO")))
        rubro = as_text(cell(row, column(header, "RUBRO")))
        if not project and not rubro:
            break
        distributions.append(
            Distribution(
                project=project,
                rubro=rubro,
                category=as_text(cell(row, column(header, "CATEGORIA PRODUCTO"))),
                gross_total=money(cell(row, column(header, "TOTAL DISTRIBUIDO"))),
            )
        )
    if not distributions:
        raise ConsultaDocumentoError("No se encontraron distribuciones.")
    return distributions


def parse_original_accounts(rows: list[list[Any]], header_row: int) -> list[OriginalAccount]:
    header = header_map(rows[header_row])
    accounts: list[OriginalAccount] = []
    for row in rows[header_row + 1 :]:
        category = as_text(cell(row, column(header, "CATEGORIA")))
        if category.upper().startswith("TOTAL"):
            break
        account = as_text(cell(row, column(header, "CUENTA")))
        if not account:
            continue
        accounts.append(
            OriginalAccount(
                category=category,
                account=account,
                debit=money(cell(row, column(header, "DEBITO"))),
                credit=money(cell(row, column(header, "CREDITO"))),
                cost_center=as_text(cell(row, header.get(normalize_label("C.C."), -1))),
                detail=as_text(cell(row, header.get(normalize_label("DETALLE"), -1))),
            )
        )
    if not accounts:
        raise ConsultaDocumentoError("No se encontraron cuentas contables originales.")
    return accounts


def find_header_row(rows: list[list[Any]], *required_labels: str) -> int:
    required = {normalize_label(label) for label in required_labels}
    for index, row in enumerate(rows):
        if required.issubset(set(header_map(row))):
            return index
    labels = ", ".join(required_labels)
    raise ConsultaDocumentoError(f"No se encontro el encabezado requerido: {labels}.")


def header_map(row: list[Any]) -> dict[str, int]:
    return {
        normalized: index
        for index, value in enumerate(row)
        if (normalized := normalize_label(as_text(value)))
    }


def column(header: dict[str, int], label: str) -> int:
    try:
        return header[normalize_label(label)]
    except KeyError as error:
        raise ConsultaDocumentoError(f"Falta la columna requerida: {label}.") from error


def value_after_label(rows: list[list[Any]], label: str) -> Any:
    expected = normalize_label(label)
    for row in rows:
        for index, value in enumerate(row):
            if normalize_label(as_text(value)) == expected:
                return cell(row, index + 1)
    raise ConsultaDocumentoError(f"No se encontro el valor para {label}.")


def next_nonempty(rows: list[list[Any]], start_row: int, column: int, field: str) -> Any:
    for row in rows[start_row:]:
        value = cell(row, column)
        if as_text(value):
            return value
    raise ConsultaDocumentoError(f"No se encontro {field}.")


def normalize_label(value: str) -> str:
    return " ".join(value.upper().replace(".", "").replace(":", "").split())


def normalize_invoice_number(value: Any) -> str:
    return re.sub(r"\s+", "", as_text(value))


def company_name(value: Any) -> str:
    """El encabezado agrega la sede despues de un guion; no es parte de la empresa."""
    return as_text(value).split(" - ", 1)[0]


def split_product(value: str) -> tuple[str, str]:
    code, separator, description = value.partition(" -- ")
    return (code.strip(), description.strip() if separator else "")


def cell(row: list[Any], index: int) -> Any:
    return row[index] if 0 <= index < len(row) else ""


def as_text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    return Decimal(str(value or 0))


def money(value: Any) -> Decimal:
    return decimal_value(value).quantize(CENTS, rounding=ROUND_HALF_UP)
