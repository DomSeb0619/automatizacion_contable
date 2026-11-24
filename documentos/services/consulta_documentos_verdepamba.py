"""Lector aislado para el reporte PAC de VERDEPAMBA (.xls o .xlsx).

No comparte heurísticas con ``Consulta de Documentos``: esta fuente PAC no
incluye distribuciones presupuestarias ni identificador explícito de proyecto.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
import xlrd

from documentos.services.consulta_documentos import (
    ConsultaDocumento,
    ConsultaDocumentoError,
    OriginalAccount,
    Product,
    as_text,
    decimal_value,
    money,
)


OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
XLSX_MAGIC = b"PK\x03\x04"


def read_consulta_verdepamba(path: str | Path) -> ConsultaDocumento:
    source = Path(path)
    if source.suffix.lower() not in {".xls", ".xlsx"}:
        raise ConsultaDocumentoError("Se esperaba un archivo PAC de VERDEPAMBA con extensión .xls o .xlsx.")
    try:
        content = source.read_bytes()
        if content.startswith(OLE2_MAGIC):
            # Los .xls de PAC pueden tener una inconsistencia inocua en el
            # contenedor OLE2. xlrd puede leer sus hojas BIFF8 en este modo.
            workbook = xlrd.open_workbook(file_contents=content, on_demand=True, ignore_workbook_corruption=True)
            sheet = workbook.sheet_by_index(0)
            rows = [sheet.row_values(index) for index in range(sheet.nrows)]
            workbook.release_resources()
        elif content.startswith(XLSX_MAGIC):
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            try:
                rows = list(workbook.active.iter_rows(values_only=True))
            finally:
                workbook.close()
        else:
            raise ConsultaDocumentoError("El archivo PAC no contiene un formato .xls BIFF/OLE2 ni .xlsx válido.")
    except Exception as error:
        if isinstance(error, ConsultaDocumentoError):
            raise
        raise ConsultaDocumentoError("No se pudo leer el archivo PAC de VERDEPAMBA.") from error

    company = _company(rows)
    supplier = _after_label(rows, "PROVEEDOR:")
    ruc = _after_label(rows, "RUC:")
    erp = _after_label(rows, "NUMERO:")
    invoice = _after_label(rows, "NUMERO DOCUMENTO PROVEEDOR:")
    product_header = _header_row(rows, "PRODUCTO", "PTOTAL")
    accounting_header = _header_row(rows, "CATEGORIA", "CUENTA", "DEBITO", "CREDITO")
    products = _products(rows, product_header)
    accounts = _accounts(rows, accounting_header)
    subtotal = _after_label(rows, "SUBTOTAL 1:")
    vat = _after_label(rows, "I.V.A. %:")
    total = _after_label(rows, "T O T A L:")
    return ConsultaDocumento(
        company=company,
        project="",
        supplier=as_text(supplier),
        supplier_tax_id=as_text(ruc).replace(" ", ""),
        erp_document_number=as_text(erp).replace(" ", ""),
        supplier_invoice_number=as_text(invoice),
        products=tuple(products), subtotal=money(subtotal), vat=money(vat), total=money(total),
        distributions=(), original_accounts=tuple(accounts),
    )


def _text(value) -> str:
    return as_text(value).upper().replace(".", "").replace(":", "")


def _header_row(rows, *labels: str) -> int:
    expected = {_text(label) for label in labels}
    for index, row in enumerate(rows):
        values = {_text(value) for value in row if _text(value)}
        if expected.issubset(values):
            return index
    raise ConsultaDocumentoError(f"No se encontró el encabezado PAC requerido: {', '.join(labels)}.")


def _after_label(rows, label: str):
    expected = _text(label)
    for row in rows:
        for index, value in enumerate(row):
            if _text(value) == expected:
                return row[index + 1] if index + 1 < len(row) else ""
    raise ConsultaDocumentoError(f"No se encontró el valor PAC para {label}.")


def _company(rows) -> str:
    value = as_text(rows[0][0]) if rows and rows[0] else ""
    if not value:
        raise ConsultaDocumentoError("No se encontró la empresa PAC.")
    return value.split(" - ", 1)[0]


def _products(rows, header_row: int) -> list[Product]:
    header = {_text(value): index for index, value in enumerate(rows[header_row]) if _text(value)}
    required = ("PRODUCTO", "CANT", "PUNIT", "PNETO", "PTOTAL")
    if any(value not in header for value in required):
        raise ConsultaDocumentoError("El detalle de productos PAC no contiene las columnas esperadas.")
    result = []
    for row in rows[header_row + 1:]:
        first = as_text(row[0]) if row else ""
        if first.upper().startswith("TOTAL"):
            break
        raw = as_text(row[header["PRODUCTO"]]) if header["PRODUCTO"] < len(row) else ""
        if not raw:
            continue
        code, _, description = raw.partition(" -- ")
        value = lambda key: row[header[key]] if header[key] < len(row) else 0
        result.append(Product(code.strip(), description.strip(), decimal_value(value("CANT")), decimal_value(value("PUNIT")), decimal_value(value("PNETO")), money(value("PTOTAL"))))
    if not result:
        raise ConsultaDocumentoError("No se encontraron productos PAC.")
    return result


def _accounts(rows, header_row: int) -> list[OriginalAccount]:
    header = {_text(value): index for index, value in enumerate(rows[header_row]) if _text(value)}
    result = []
    for row in rows[header_row + 1:]:
        category = as_text(row[header["CATEGORIA"]]) if header["CATEGORIA"] < len(row) else ""
        if category.upper().startswith("TOTAL"):
            break
        account = as_text(row[header["CUENTA"]]) if header["CUENTA"] < len(row) else ""
        if not account:
            continue
        get = lambda key: row[header[key]] if header[key] < len(row) else 0
        result.append(OriginalAccount(category, account, money(get("DEBITO")), money(get("CREDITO")), "", ""))
    if not result:
        raise ConsultaDocumentoError("No se encontraron cuentas PAC.")
    return result
