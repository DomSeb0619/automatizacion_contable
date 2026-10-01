from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from documentos.services.consulta_documentos import ConsultaDocumento, Distribution, OriginalAccount, Product, read_consulta_documento
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
            RubroAccount(self.document.company, self.document.project, "1.2.3.2.3.05 - RUBRO SINTETICO A", "IZEEHE5"),
            RubroAccount(self.document.company, self.document.project, "1.2.3.2.3.07 - RUBRO SINTETICO B", "IZEEHE7"),
        ]
        self.products = [
            ProductVat("PRD-2175-A", "MATERIALES", Decimal("0.15")),
            ProductVat("PRD-2175-B", "EQUIPO Y MAQUINARIA", Decimal("0.15")),
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
        self.assertIn("PRD-2175-A", warning.message)
        self.assertIn("15.00%", warning.message)
        self.assertIn("5.00%", warning.message)

    def test_uses_one_for_debits_and_two_for_credits(self) -> None:
        result = self.build()

        self.assertEqual({entry.debit_credit for entry in result.entries[:2]}, {"1"})
        self.assertEqual({entry.debit_credit for entry in result.entries[2:]}, {"2"})


class RepeatedInventoryAccountTests(SimpleTestCase):
    def setUp(self) -> None:
        debits = ["16.38", "12.28", "32.33", "51.96", "5.62", "21.86", "13.20", "5.36", "36.37", "27.69"]
        self.document = ConsultaDocumento(
            company="IBIS MILA S.C.C.",
            project="IBIS MILA SCC",
            supplier="PROVEEDOR SINTETICO MILA S.A.",
            supplier_tax_id="",
            erp_document_number="",
            supplier_invoice_number="001-002-000011397",
            products=(Product("IZ-101001", "Material", Decimal("1"), Decimal("223.05"), Decimal("223.05"), Decimal("223.05")),),
            subtotal=Decimal("223.05"),
            vat=Decimal("26.63"),
            total=Decimal("249.68"),
            distributions=(
                Distribution("IBIS MILA SCC", "1.2.10.1.0", "MATERIALES", Decimal("150.11")),
                Distribution("IBIS MILA SCC", "1.2.10.1.0", "MATERIALES", Decimal("27.80")),
                Distribution("IBIS MILA SCC", "1.2.4.1.01", "MATERIALES", Decimal("17.20")),
                Distribution("IBIS MILA SCC", "1.2.6.3.04", "MATERIALES", Decimal("54.56")),
            ),
            original_accounts=tuple(
                OriginalAccount("INVENTARIOS", f"101030101- MOVIMIENTO {index}", Decimal(amount), Decimal("0"), "", "")
                for index, amount in enumerate(debits, start=1)
            ),
        )
        self.rubros = [
            RubroAccount(self.document.company, self.document.project, code, account)
            for code, account in [
                ("1.2.10.1.0", "R1"), ("1.2.4.1.01", "R2"), ("1.2.6.3.04", "R3"),
            ]
        ]
        self.categories = [CategoryGeneralAccount(self.document.company, self.document.project, "MATERIALES", "101030101")]

    def test_repeated_inventory_movements_for_one_code_are_reclassified(self) -> None:
        result = build_reclassification(self.document, self.rubros, [], self.categories)

        error_codes = [error.code for error in result.errors]
        self.assertNotIn("DIFERENCIA_CONTABLE", error_codes)
        self.assertNotIn("CASO_AMBIGUO_CUENTA_GENERAL", error_codes)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.total_debits, Decimal("223.05"))
        self.assertEqual(result.total_credits, Decimal("223.05"))
        self.assertEqual([entry.account for entry in result.entries if entry.debit_credit == "2"], ["101030101"])

    def test_two_different_inventory_codes_remain_ambiguous(self) -> None:
        accounts = list(self.document.original_accounts)
        accounts[-1] = OriginalAccount("INVENTARIOS", "101030102- OTRO", Decimal("27.69"), Decimal("0"), "", "")
        document = replace(
            self.document,
            original_accounts=tuple(accounts),
            products=(
                Product("IZ-101001", "Material 5", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
                Product("IZ-101002", "Material 15", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            ),
        )
        products = [
            ProductVat("IZ-101001", "MATERIALES", Decimal("0.05")),
            ProductVat("IZ-101002", "MATERIALES", Decimal("0.15")),
        ]
        result = build_reclassification(
            document, self.rubros, products, []
        )

        self.assertIn("CASO_AMBIGUO_CUENTA_GENERAL", [error.code for error in result.errors])

    def test_mixed_vat_uses_the_unique_original_base_without_changing_invoice_totals(self) -> None:
        products = [
            ProductVat("IZ-101001", "MATERIALES", Decimal("0.05")),
            ProductVat("IZ-101002", "MATERIALES", Decimal("0.15")),
        ]
        document = replace(
            self.document,
            products=(
                Product("IZ-101001", "Material 5", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
                Product("IZ-101002", "Material 15", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            ),
        )

        result = build_reclassification(document, self.rubros, products, self.categories)

        self.assertTrue(result.is_valid)
        self.assertEqual(document.subtotal, Decimal("223.05"))
        self.assertEqual(document.vat, Decimal("26.63"))
        self.assertEqual(result.total_debits, Decimal("223.05"))
        self.assertEqual(result.total_credits, Decimal("223.05"))
        self.assertEqual(result.difference, Decimal("0.00"))
        self.assertEqual(
            [(entry.account, entry.amount) for entry in result.entries if entry.debit_credit == "2"],
            [("101030101", Decimal("223.05"))],
        )
        warning = next(warning for warning in result.warnings if warning.code == "IVA_MIXTO_RECONCILIADO_CON_ASIENTO")
        self.assertIn("MATERIALES", warning.message)
        self.assertIn("101030101", warning.message)

    def test_mixed_vat_cent_adjustment_is_deterministic(self) -> None:
        document = replace(
            self.document,
            distributions=(
                Distribution("IBIS MILA SCC", "1.2.10.1.0", "MATERIALES", Decimal("1.00")),
                Distribution("IBIS MILA SCC", "1.2.4.1.01", "MATERIALES", Decimal("1.00")),
                Distribution("IBIS MILA SCC", "1.2.6.3.04", "MATERIALES", Decimal("1.00")),
            ),
            original_accounts=(OriginalAccount("INVENTARIOS", "101030101 - INVENTARIO", Decimal("1.00"), Decimal("0"), "", ""),),
            subtotal=Decimal("1.00"),
            vat=Decimal("0.00"),
            total=Decimal("1.00"),
            products=(
                Product("IZ-101001", "Material 5", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
                Product("IZ-101002", "Material 15", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            ),
        )
        products = [
            ProductVat("IZ-101001", "MATERIALES", Decimal("0.05")),
            ProductVat("IZ-101002", "MATERIALES", Decimal("0.15")),
        ]

        result = build_reclassification(document, self.rubros, products, self.categories)

        self.assertTrue(result.is_valid)
        self.assertEqual(
            [entry.amount for entry in result.entries if entry.debit_credit == "1"],
            [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")],
        )
        self.assertIn("AJUSTE_CENTAVO_APLICADO", [warning.code for warning in result.warnings])
