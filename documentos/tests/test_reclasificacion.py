from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from documentos.services.consulta_documentos import read_consulta_documento
from documentos.services.reclasificacion import (
    CategoryGeneralAccount,
    ProductVat,
    RubroAccount,
    build_reclassification,
)


class Reclassification2175Tests(SimpleTestCase):
    def setUp(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "factura_2175.xls"
        self.document = read_consulta_documento(fixture)
        self.rubros = [
            RubroAccount(self.document.company, self.document.project, "1.2.3.2.3.05 - Hormigon premezclado 280 kgcm2 en losas y vigas de cubierta", "IZEEHE5"),
            RubroAccount(self.document.company, self.document.project, "1.2.3.2.3.07 - Hormigon premezclado 280 kgcm2 en escaleras comunales y privadas", "IZEEHE7"),
        ]
        self.products = [
            ProductVat("IZ-101135", "MATERIALES", Decimal("0.15")),
            ProductVat("IZ-301004", "EQUIPO Y MAQUINARIA", Decimal("0.15")),
        ]
        self.categories = [
            CategoryGeneralAccount(self.document.company, self.document.project, "MATERIALES", "101031005"),
            CategoryGeneralAccount(self.document.company, self.document.project, "EQUIPO Y MAQUINARIA", "101031003"),
        ]

    def build(self, **overrides):
        return build_reclassification(
            overrides.get("document", self.document),
            overrides.get("rubros", self.rubros),
            overrides.get("products", self.products),
            overrides.get("categories", self.categories),
        )

    def test_2175_generates_the_expected_balanced_journal(self) -> None:
        result = self.build()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_debits, Decimal("5330.75"))
        self.assertEqual(result.total_credits, Decimal("5330.75"))
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertEqual(
            [(entry.account, entry.amount, entry.debit_credit) for entry in result.entries],
            [
                ("IZEEHE5", Decimal("4872.30"), "1"),
                ("IZEEHE7", Decimal("458.45"), "1"),
                ("101031003", Decimal("455.69"), "2"),
                ("101031005", Decimal("4875.06"), "2"),
            ],
        )
        self.assertIn("CONFLICTO_IVA_MAESTRO", [warning.code for warning in result.warnings])

    def test_blocks_a_missing_rubro(self) -> None:
        result = self.build(rubros=self.rubros[:1])

        self.assertFalse(result.is_valid)
        self.assertIn("RUBRO_SIN_CUENTA", [error.code for error in result.errors])

    def test_missing_product_master_is_a_warning_when_invoice_is_unambiguous(self) -> None:
        result = self.build(products=self.products[1:])

        self.assertTrue(result.is_valid)
        self.assertIn("PRODUCTO_SIN_IVA_MAESTRO", [warning.code for warning in result.warnings])

    def test_missing_category_mapping_uses_the_unique_original_account_with_warning(self) -> None:
        result = self.build(categories=self.categories[1:])

        self.assertTrue(result.is_valid)
        self.assertIn("CATEGORIA_SIN_CUENTA_GENERAL", [warning.code for warning in result.warnings])

    def test_blocks_an_original_accounting_difference(self) -> None:
        accounts = list(self.document.original_accounts)
        accounts[1] = replace(accounts[1], debit=Decimal("4800.00"))
        document = replace(self.document, original_accounts=tuple(accounts))

        result = self.build(document=document)

        self.assertFalse(result.is_valid)
        self.assertIn("DIFERENCIA_CONTABLE", [error.code for error in result.errors])

    def test_reports_the_master_vat_conflict_without_overriding_the_invoice(self) -> None:
        result = self.build()

        warning = next(warning for warning in result.warnings if warning.code == "CONFLICTO_IVA_MAESTRO")
        self.assertIn("IZ-101135", warning.message)
        self.assertIn("15.00%", warning.message)
        self.assertIn("5.00%", warning.message)

    def test_uses_one_for_debits_and_two_for_credits(self) -> None:
        result = self.build()

        self.assertEqual({entry.debit_credit for entry in result.entries[:2]}, {"1"})
        self.assertEqual({entry.debit_credit for entry in result.entries[2:]}, {"2"})
