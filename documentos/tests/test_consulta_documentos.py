from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from documentos.services.consulta_documentos import read_consulta_documento


class ConsultaDocumento2175Tests(SimpleTestCase):
    def test_reads_the_control_invoice(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"

        document = read_consulta_documento(fixture)

        self.assertEqual(document.company, "EMPRESA DEMO CONSTRUCTORA S.A.")
        self.assertEqual(document.project, "IZARI")
        self.assertEqual(document.supplier, "PROVEEDOR SINTETICO HORMIGON S.A.")
        self.assertEqual(document.supplier_tax_id, "9999999999001")
        self.assertEqual(document.erp_document_number, "001-001-000000101")
        self.assertEqual(document.supplier_invoice_number, "001-001- 000002175")
        self.assertEqual(document.subtotal, Decimal("5330.75"))
        self.assertEqual(document.vat, Decimal("312.10"))
        self.assertEqual(document.total, Decimal("5642.85"))

        self.assertEqual(len(document.products), 2)
        self.assertEqual(document.products[0].code, "PRD-2175-A")
        self.assertEqual(document.products[0].total, Decimal("4875.06"))
        self.assertEqual(document.products[1].code, "PRD-2175-B")
        self.assertEqual(document.products[1].total, Decimal("455.69"))

        self.assertEqual(len(document.distributions), 4)
        self.assertEqual(document.distributions[0].gross_total, Decimal("4678.59"))
        self.assertEqual(document.distributions[3].gross_total, Decimal("45.07"))

        inventory_accounts = document.original_inventory_accounts
        self.assertEqual(len(inventory_accounts), 2)
        self.assertEqual(
            [account.account for account in inventory_accounts],
            [
                "101031005 - INVENTARIO MATERIALES",
                "101031003 - INVENTARIO SERVICIOS",
            ],
        )
        self.assertEqual(document.reclasificable_total, Decimal("5330.75"))
