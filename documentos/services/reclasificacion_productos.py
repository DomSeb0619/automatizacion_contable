"""Motor puro de reclasificación por producto para fuentes PAC."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from documentos.services.consulta_documentos import ConsultaDocumento, money
from documentos.services.reclasificacion import Issue, JournalEntry, ReclassificationResult, account_code


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ProductAccount:
    product_code: str
    destination_account: str
    accounting_category: str
    original_inventory_account: str
    validated: bool


def build_product_reclassification(
    document: ConsultaDocumento, mappings: list[ProductAccount], *, category_accounts: set[tuple[str, str]] | None = None,
) -> ReclassificationResult:
    """Crea un débito por producto validado y créditos por inventario real.

    La evidencia del asiento original siempre determina el crédito. El mapeo
    confirma que cada producto puede pertenecer a dicha cuenta, sin inferirlo
    por importes ni alterar IVA.
    """
    mapping_by_product = {item.product_code: item for item in mappings if item.destination_account.strip()}
    inventory = _inventory(document)
    errors: list[Issue] = []
    debits: list[JournalEntry] = []
    credits_by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
    # PAC no vincula una fila de producto con una fila contable. Solo se puede
    # asignar su crédito sin inferencias cuando el asiento tiene una única
    # cuenta de inventario y el total de productos concilia con ella.
    if len(inventory) != 1:
        return ReclassificationResult((), (Issue(
            "CUENTAS_INVENTARIO_AMBIGUAS",
            "El asiento PAC contiene varias cuentas de inventario y no permite asociarlas inequívocamente a los productos.",
        ),), (), ZERO, ZERO, ZERO)
    original_account, inventory_total = next(iter(inventory.items()))
    product_total = money(sum((item.total for item in document.products), ZERO))
    if product_total != inventory_total:
        return ReclassificationResult((), (Issue(
            "DIFERENCIA_PRODUCTOS_INVENTARIO",
            "La suma de productos no coincide con el débito original de inventario.",
        ),), (), ZERO, ZERO, ZERO)
    for product in document.products:
        mapping = mapping_by_product.get(product.code)
        if mapping is None:
            errors.append(Issue("PRODUCTO_SIN_CORRESPONDENCIA", f"El producto {product.code} no tiene correspondencia configurada."))
            continue
        # La validación categoría/cuenta se conserva solo cuando hay datos de
        # configuración explícitos. El catálogo PAC básico no los necesita.
        original = account_code(mapping.original_inventory_account) or original_account
        category_key = (" ".join(mapping.accounting_category.upper().split()), original)
        if category_accounts is not None and mapping.accounting_category and mapping.original_inventory_account and category_key not in category_accounts:
            errors.append(Issue("CATEGORIA_CUENTA_NO_AUTORIZADA", f"La categoría {mapping.accounting_category} no tiene autorizada la cuenta de inventario {original}."))
            continue
        if original != original_account:
            errors.append(Issue("CUENTA_ORIGEN_NO_ENCONTRADA", f"El producto {product.code} espera la cuenta original {original}, que no está en el asiento."))
            continue
        debits.append(JournalEntry(
            document.supplier_invoice_number, mapping.destination_account, product.total, "1",
            rubro=product.code, category=mapping.accounting_category,
        ))
        # Un diario preliminar solo revierte la porción de inventario que ya
        # tiene una equivalencia validada; no inventa un ajuste para pendientes.
        credits_by_account[original_account] += product.total
    total_debits = money(sum((entry.amount for entry in debits), ZERO))
    total_credits = money(sum(credits_by_account.values(), ZERO))
    difference = money(total_debits - total_credits)
    if not errors and difference != ZERO:
        errors.append(Issue("DIFERENCIA_CONTABLE", f"Débitos y créditos difieren {difference}."))
    credits = [JournalEntry(document.supplier_invoice_number, code, money(amount), "2") for code, amount in sorted(credits_by_account.items())]
    return ReclassificationResult(tuple(debits + credits), tuple(errors), (), total_debits, total_credits, difference)


def _inventory(document: ConsultaDocumento) -> dict[str, Decimal]:
    values: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for account in document.original_inventory_accounts:
        values[account_code(account.account)] += account.debit
    return {code: money(value) for code, value in values.items()}
