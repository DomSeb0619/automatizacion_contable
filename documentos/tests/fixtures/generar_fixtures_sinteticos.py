"""Genera fixtures BIFF8 sintéticos para pruebas, sin datos de proveedores reales."""

from __future__ import annotations

from pathlib import Path

import xlwt


FIXTURES_DIR = Path(__file__).parent


def write_row(sheet: xlwt.Worksheet, row: int, values: list[object]) -> None:
    for column, value in enumerate(values):
        sheet.write(row, column, value)


def build_invoice(path: Path, data: dict[str, object]) -> None:
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("Worksheet")

    sheet.write(0, 0, data["company"])
    for row, label, value in [
        (2, "PROVEEDOR:", data["supplier"]),
        (3, "RUC:", data["tax_id"]),
        (4, "NUMERO:", data["erp_number"]),
        (5, "NUMERO DOCUMENTO PROVEEDOR:", data["invoice_number"]),
        (6, "SUBTOTAL 1:", data["subtotal"]),
        (7, "I.V.A. %:", data["vat"]),
        (8, "T O T A L:", data["total"]),
    ]:
        sheet.write(row, 0, label)
        sheet.write(row, 1, value)

    product_header = 10
    write_row(sheet, product_header, ["PRODUCTO", "CANT.", "P.UNIT.", "P.NETO", "P.TOTAL"])
    for row, product in enumerate(data["products"], start=product_header + 1):
        write_row(sheet, row, product)
    sheet.write(product_header + 1 + len(data["products"]), 0, "TOTAL PRODUCTOS")

    distribution_header = product_header + 4 + len(data["products"])
    write_row(sheet, distribution_header, ["PROYECTO", "RUBRO", "CATEGORIA PRODUCTO", "TOTAL DISTRIBUIDO"])
    for row, distribution in enumerate(data["distributions"], start=distribution_header + 1):
        write_row(sheet, row, distribution)

    accounting_header = distribution_header + 4 + len(data["distributions"])
    write_row(sheet, accounting_header, ["CATEGORIA", "CUENTA", "DEBITO", "CREDITO", "C.C.", "DETALLE"])
    for row, account in enumerate(data["accounts"], start=accounting_header + 1):
        write_row(sheet, row, account)
    sheet.write(accounting_header + 1 + len(data["accounts"]), 0, "TOTAL ASIENTO")
    workbook.save(str(path))


def main() -> None:
    build_invoice(
        FIXTURES_DIR / "factura_2175.xls",
        {
            "company": "EMPRESA DEMO CONSTRUCTORA S.A. - SEDE PRUEBA",
            "supplier": "PROVEEDOR SINTETICO HORMIGON S.A.",
            "tax_id": "9999999999001",
            "erp_number": "001-001-000000101",
            "invoice_number": "001-001- 000002175",
            "subtotal": 5330.75,
            "vat": 312.10,
            "total": 5642.85,
            "products": [
                ["PRD-2175-A -- MATERIAL SINTETICO A", 46.5, 104.84, 104.84, 4875.06],
                ["PRD-2175-B -- SERVICIO SINTETICO B", 46.5, 9.7997, 9.799785, 455.69],
            ],
            "distributions": [
                ["IZARI", "1.2.3.2.3.05 - RUBRO SINTETICO A", "MATERIALES", 4678.59],
                ["IZARI", "1.2.3.2.3.05 - RUBRO SINTETICO A", "EQUIPO Y MAQUINARIA", 478.98],
                ["IZARI", "1.2.3.2.3.07 - RUBRO SINTETICO B", "MATERIALES", 440.22],
                ["IZARI", "1.2.3.2.3.07 - RUBRO SINTETICO B", "EQUIPO Y MAQUINARIA", 45.07],
            ],
            "accounts": [
                ["10105 - ACTIVOS POR IMPUESTOS", "1010501 - IVA EN COMPRAS", 312.10, 0, "-", "ASIENTO SINTETICO 2175"],
                ["10103 - INVENTARIOS", "101031005 - INVENTARIO MATERIALES", 4875.06, 0, "-", "ASIENTO SINTETICO 2175"],
                ["10103 - INVENTARIOS", "101031003 - INVENTARIO SERVICIOS", 455.69, 0, "-", "ASIENTO SINTETICO 2175"],
                ["2010301 - PROVEEDORES", "201030101 - PROVEEDORES LOCALES", 0, 5642.85, "-", "ASIENTO SINTETICO 2175"],
            ],
        },
    )
    build_invoice(
        FIXTURES_DIR / "factura_5347.xls",
        {
            "company": "EMPRESA DEMO CONSTRUCTORA S.A. - SEDE PRUEBA",
            "supplier": "PROVEEDOR SINTETICO ACABADOS S.A.",
            "tax_id": "9999999999002",
            "erp_number": "001-001-000000102",
            "invoice_number": "001-001- 000005347",
            "subtotal": 8740.70,
            "vat": 1311.10,
            "total": 10051.80,
            "products": [
                ["PRD-5347-01 -- MATERIAL SINTETICO 01", 51.9819, 21.85, 21.849913, 1135.80],
                ["PRD-5347-02 -- MATERIAL SINTETICO 02", 174.5302, 21.85, 21.849972, 3813.48],
                ["PRD-5347-03 -- MATERIAL SINTETICO 03", 47.5205, 21.85, 21.849938, 1038.32],
                ["PRD-5347-04 -- MATERIAL SINTETICO 04", 42.13, 21.85, 21.849988, 920.54],
                ["PRD-5347-05 -- MATERIAL SINTETICO 05", 83.87, 21.85, 21.850006, 1832.56],
            ],
            "distributions": [
                ["IZARI", "1.2.3.5.3.02 - RUBRO SINTETICO 01", "MATERIALES", 1194.07],
                ["IZARI", "1.2.3.5.3.03 - RUBRO SINTETICO 02", "MATERIALES", 5691.67],
                ["IZARI", "1.2.4.2.04 - RUBRO SINTETICO 03", "MATERIALES", 1058.62],
                ["IZARI", "1.2.4.3.01 - RUBRO SINTETICO 04", "MATERIALES", 2107.44],
            ],
            "accounts": [
                ["10105 - ACTIVOS POR IMPUESTOS", "1010501 - IVA EN COMPRAS", 1311.10, 0, "-", "ASIENTO SINTETICO 5347"],
                ["10103 - INVENTARIOS", "101031001 - INVENTARIO MATERIALES", 1135.80, 0, "-", "ASIENTO SINTETICO 5347"],
                ["10103 - INVENTARIOS", "101031001 - INVENTARIO MATERIALES", 3813.48, 0, "-", "ASIENTO SINTETICO 5347"],
                ["10103 - INVENTARIOS", "101031001 - INVENTARIO MATERIALES", 1038.32, 0, "-", "ASIENTO SINTETICO 5347"],
                ["10103 - INVENTARIOS", "101031001 - INVENTARIO MATERIALES", 920.54, 0, "-", "ASIENTO SINTETICO 5347"],
                ["10103 - INVENTARIOS", "101031001 - INVENTARIO MATERIALES", 1832.56, 0, "-", "ASIENTO SINTETICO 5347"],
                ["2010301 - PROVEEDORES", "201030101 - PROVEEDORES LOCALES", 0, 10051.80, "-", "ASIENTO SINTETICO 5347"],
            ],
        },
    )


if __name__ == "__main__":
    main()
