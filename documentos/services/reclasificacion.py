"""Motor puro para construir una reclasificacion contable por factura."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from documentos.services.consulta_documentos import ConsultaDocumento, Distribution, money


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class RubroAccount:
    company: str
    project: str
    rubro: str
    budget_account: str


@dataclass(frozen=True)
class ProductVat:
    product_code: str
    category: str
    vat_rate: Decimal | None


@dataclass(frozen=True)
class CategoryGeneralAccount:
    company: str
    project: str
    category: str
    original_general_account: str


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass(frozen=True)
class JournalEntry:
    invoice_number: str
    account: str
    amount: Decimal
    debit_credit: str
    rubro: str | None = None
    cent_adjustment: Decimal = ZERO


@dataclass(frozen=True)
class ReclassificationResult:
    entries: tuple[JournalEntry, ...]
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    total_debits: Decimal
    total_credits: Decimal
    difference: Decimal

    @property
    def is_valid(self) -> bool:
        return not self.errors and self.difference == ZERO


def build_reclassification(
    document: ConsultaDocumento,
    rubro_accounts: list[RubroAccount],
    product_vats: list[ProductVat],
    category_general_accounts: list[CategoryGeneralAccount],
) -> ReclassificationResult:
    """Construye un diario desde la evidencia contable de una sola factura.

    Los catalogos orientan y validan la relacion. Las cuentas y bases del
    asiento original prevalecen si la relacion con cada categoria es unica.
    """
    errors: list[Issue] = []
    warnings: list[Issue] = []
    products_by_category = index_products(document, product_vats, warnings)
    distributions_by_category = group_distributions(document.distributions)
    rubro_by_key = index_rubros(rubro_accounts)
    validate_rubros(document, distributions_by_category, rubro_by_key, errors)

    inventory_by_code = aggregate_inventory_accounts(document)
    if money(sum(inventory_by_code.values(), Decimal())) != document.subtotal:
        errors.append(Issue("DIFERENCIA_CONTABLE", "Los debitos originales de inventario no coinciden con el subtotal de la factura."))

    category_by_key = index_category_accounts(category_general_accounts)
    category_accounts = resolve_category_accounts(
        document, distributions_by_category, inventory_by_code, products_by_category, category_by_key, errors, warnings
    )
    category_bases = calculate_category_bases(
        distributions_by_category, category_accounts, inventory_by_code, products_by_category, errors, warnings
    )
    reconcile_account_bases(category_bases, category_accounts, inventory_by_code, errors, warnings)

    debit_amounts: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for category, bases in category_bases.items():
        for distribution, base in zip(distributions_by_category[category], bases, strict=True):
            rubro = rubro_by_key.get((scope_key(document.company), scope_key(document.project), rubro_code(distribution.rubro)))
            if rubro is not None:
                debit_amounts[(distribution.rubro, rubro.budget_account)] += base

    total_debits = money(sum(debit_amounts.values(), Decimal()))
    total_credits = money(sum(inventory_by_code.values(), Decimal()))
    difference = money(total_debits - total_credits)
    if difference != ZERO:
        errors.append(Issue("DIFERENCIA_CONTABLE", f"Debitos y creditos difieren {difference}."))
    if errors:
        return ReclassificationResult((), tuple(errors), tuple(warnings), total_debits, total_credits, difference)

    entries = [
        JournalEntry(document.supplier_invoice_number, account, money(amount), "1", rubro)
        for (rubro, account), amount in sorted(debit_amounts.items())
    ]
    entries.extend(
        JournalEntry(document.supplier_invoice_number, account, money(amount), "2")
        for account, amount in sorted(inventory_by_code.items())
    )
    return ReclassificationResult(tuple(entries), (), tuple(warnings), total_debits, total_credits, difference)


def index_products(document: ConsultaDocumento, rules: list[ProductVat], warnings: list[Issue]) -> dict[str, list[ProductVat]]:
    by_code = {rule.product_code: rule for rule in rules}
    by_category: dict[str, list[ProductVat]] = defaultdict(list)
    for product in document.products:
        rule = by_code.get(product.code)
        if rule is None or rule.vat_rate is None:
            warnings.append(Issue("PRODUCTO_SIN_IVA_MAESTRO", f"El producto {product.code} no tiene IVA configurado; debe actualizarse el maestro."))
            continue
        by_category[category_key(rule.category)].append(rule)
    return by_category


def group_distributions(distributions: tuple[Distribution, ...]) -> dict[str, list[Distribution]]:
    grouped: dict[str, list[Distribution]] = defaultdict(list)
    for distribution in distributions:
        grouped[category_key(distribution.category)].append(distribution)
    return grouped


def index_rubros(rubros: list[RubroAccount]) -> dict[tuple[str, str, str], RubroAccount]:
    return {(scope_key(rule.company), scope_key(rule.project), rubro_code(rule.rubro)): rule for rule in rubros}


def validate_rubros(document: ConsultaDocumento, distributions: dict[str, list[Distribution]], rubros: dict, errors: list[Issue]) -> None:
    for category_distributions in distributions.values():
        for distribution in category_distributions:
            key = (scope_key(document.company), scope_key(document.project), rubro_code(distribution.rubro))
            if key not in rubros:
                errors.append(Issue("RUBRO_SIN_CUENTA", f"El rubro {distribution.rubro} no tiene cuenta presupuestaria."))


def aggregate_inventory_accounts(document: ConsultaDocumento) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for account in document.original_inventory_accounts:
        totals[account_code(account.account)] += account.debit
    return {code: money(amount) for code, amount in totals.items()}


def index_category_accounts(rules: list[CategoryGeneralAccount]) -> dict[tuple[str, str, str], set[str]]:
    indexed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for rule in rules:
        key = (scope_key(rule.company), scope_key(rule.project), category_key(rule.category))
        indexed[key].add(account_code(rule.original_general_account))
    return indexed


def resolve_category_accounts(
    document: ConsultaDocumento,
    distributions: dict[str, list[Distribution]],
    inventory: dict[str, Decimal],
    products: dict[str, list[ProductVat]],
    category_rules: dict[tuple[str, str, str], set[str]],
    errors: list[Issue],
    warnings: list[Issue],
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    configured_codes: dict[str, set[str]] = {}
    ambiguous_categories: set[str] = set()
    for category in distributions:
        configured = category_rules.get((scope_key(document.company), scope_key(document.project), category), set())
        configured_codes[category] = configured
        matches = configured.intersection(inventory)
        if len(matches) == 1:
            resolved[category] = next(iter(matches))
        elif len(matches) > 1:
            ambiguous_categories.add(category)
            errors.append(
                Issue(
                    "CASO_AMBIGUO_CUENTA_GENERAL",
                    f"La categoria {category} coincide con varias cuentas originales permitidas.",
                )
            )

    pending = [category for category in distributions if category not in resolved and category not in ambiguous_categories]
    while pending:
        progress = False
        used_by_config = set(resolved.values())
        for category in list(pending):
            candidates = [code for code in inventory if code not in used_by_config]
            if len(candidates) == 1:
                resolved[category] = candidates[0]
                pending.remove(category)
                progress = True
                continue
            expected = expected_base_from_master(distributions[category], products.get(category, []))
            matches = [code for code in candidates if expected is not None and inventory[code] == expected]
            if len(matches) == 1:
                resolved[category] = matches[0]
                pending.remove(category)
                progress = True
        if not progress:
            for category in pending:
                errors.append(Issue("CASO_AMBIGUO_CUENTA_GENERAL", f"No se puede relacionar la categoria {category} con una sola cuenta original de inventario."))
            break

    for category, original_code in resolved.items():
        configured = configured_codes[category]
        if not configured:
            warnings.append(Issue("CATEGORIA_SIN_CUENTA_GENERAL", f"La categoria {category} no tiene cuenta general configurada; se uso {original_code} del asiento original."))
        elif original_code not in configured:
            allowed = ", ".join(sorted(configured))
            warnings.append(Issue("CONFLICTO_CUENTA_GENERAL_MAPEO", f"La categoria {category} permite {allowed} y el asiento original usa {original_code}; se uso la cuenta original."))
    return resolved


def expected_base_from_master(distributions: list[Distribution], products: list[ProductVat]) -> Decimal | None:
    rates = {product.vat_rate for product in products if product.vat_rate is not None}
    if len(rates) != 1:
        return None
    rate = next(iter(rates))
    return money(sum((money(distribution.gross_total / (Decimal("1") + rate)) for distribution in distributions), Decimal()))


def calculate_category_bases(
    distributions: dict[str, list[Distribution]],
    category_accounts: dict[str, str],
    inventory: dict[str, Decimal],
    products: dict[str, list[ProductVat]],
    errors: list[Issue],
    warnings: list[Issue],
) -> dict[str, list[Decimal]]:
    categories_by_account: dict[str, list[str]] = defaultdict(list)
    for category, code in category_accounts.items():
        categories_by_account[code].append(category)

    bases: dict[str, list[Decimal]] = {}
    for category, category_distributions in distributions.items():
        code = category_accounts.get(category)
        if code is None:
            continue
        rates = {product.vat_rate for product in products.get(category, []) if product.vat_rate is not None}
        if len(rates) > 1:
            errors.append(Issue("CASO_AMBIGUO_IVA", f"La categoria {category} tiene productos con tarifas de IVA diferentes."))
            continue
        if len(categories_by_account[code]) == 1:
            base = inventory[code]
            gross = money(sum((distribution.gross_total for distribution in category_distributions), Decimal()))
            if base <= ZERO or gross <= ZERO:
                errors.append(Issue("IVA_NO_DETERMINABLE", f"No se puede determinar IVA para la categoria {category}."))
                continue
            rate = (gross / base - Decimal("1")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        elif len(rates) == 1:
            rate = next(iter(rates))
        else:
            errors.append(Issue("CASO_AMBIGUO_IVA", f"No se puede determinar una tarifa unica para la categoria {category}."))
            continue
        for product in products.get(category, []):
            if product.vat_rate != rate:
                warnings.append(Issue("CONFLICTO_IVA_MAESTRO", f"{product.product_code}: maestro {percent(product.vat_rate)}; factura {percent(rate)}."))
        bases[category] = [money(distribution.gross_total / (Decimal("1") + rate)) for distribution in category_distributions]
    return bases


def reconcile_account_bases(
    category_bases: dict[str, list[Decimal]],
    category_accounts: dict[str, str],
    inventory: dict[str, Decimal],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    bases_by_account: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for category, bases in category_bases.items():
        for index in range(len(bases)):
            bases_by_account[category_accounts[category]].append((category, index))
    for code, original_amount in inventory.items():
        references = bases_by_account.get(code, [])
        calculated = money(sum((category_bases[category][index] for category, index in references), Decimal()))
        difference = money(original_amount - calculated)
        if abs(difference) <= Decimal("0.01") and difference != ZERO and references:
            category, index = max(references, key=lambda item: (category_bases[item[0]][item[1]], item[0], -item[1]))
            category_bases[category][index] = money(category_bases[category][index] + difference)
            warnings.append(Issue("AJUSTE_CENTAVO_APLICADO", f"Se ajustaron {difference} en la mayor distribucion de la cuenta {code} para reconciliar centavos."))
        elif difference != ZERO:
            errors.append(Issue("DIFERENCIA_CONTABLE", f"Las bases asignadas a la cuenta {code} difieren {difference} del asiento original."))


def account_code(value: str) -> str:
    return value.split(" - ", 1)[0].strip()


def rubro_code(value: str) -> str:
    return value.split(" - ", 1)[0].strip()


def category_key(value: str) -> str:
    return " ".join(value.upper().split())


def scope_key(value: str) -> str:
    return " ".join(value.upper().split())


def percent(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
