"""Adaptadores entre registros SQLite y las estructuras puras del motor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from django.db.models import Q

from documentos.models import (
    Empresa,
    MapeoCategoriaCuenta,
    MapeoProductoIVA,
    MapeoRubroCuenta,
    Proyecto,
    normalize_code,
    normalize_identifier,
)
from documentos.services.consulta_documentos import ConsultaDocumento
from documentos.services.reclasificacion import CategoryGeneralAccount, ProductVat, RubroAccount


class CatalogoNoResuelto(ValueError):
    """El catalogo persistido no permite resolver una regla requerida."""


@dataclass(frozen=True)
class CatalogosMotor:
    rubro_accounts: list[RubroAccount]
    product_vats: list[ProductVat]
    category_general_accounts: list[CategoryGeneralAccount]


def catalogos_para_documento(document: ConsultaDocumento) -> CatalogosMotor:
    company_key = company_comparison_key(document.company)
    empresa = next(
        (candidate for candidate in Empresa.objects.filter(activa=True) if company_comparison_key(candidate.nombre) == company_key),
        None,
    )
    if empresa is None:
        raise Empresa.DoesNotExist(document.company)
    document_project = normalize_code(document.project)
    proyecto = Proyecto.objects.get(
        Q(codigo=document_project) | Q(codigo_consulta_documentos=document_project),
        empresa=empresa,
        activo=True,
    )
    return catalogos_para_proyecto(proyecto, company=document.company, project=document.project)


def catalogos_para_proyecto(
    proyecto: Proyecto, *, company: str | None = None, project: str | None = None
) -> CatalogosMotor:
    empresa = proyecto.empresa
    company_scope = company or empresa.nombre
    project_scope = project or proyecto.codigo
    rubros = [
        RubroAccount(company_scope, project_scope, mapping.codigo_rubro, mapping.cuenta_contable)
        for mapping in MapeoRubroCuenta.objects.filter(
            proyecto=proyecto,
            activo=True,
            tipo=MapeoRubroCuenta.Tipo.VALORABLE,
        )
    ]
    products = [
        ProductVat(mapping.codigo_producto, mapping.categoria, mapping.porcentaje_iva / Decimal("100"))
        for mapping in MapeoProductoIVA.objects.filter(proyecto=proyecto, activo=True)
        if mapping.categoria
    ]
    categories = [
        CategoryGeneralAccount(company_scope, project_scope, mapping.categoria, mapping.cuenta_general_original)
        for mapping in MapeoCategoriaCuenta.objects.filter(proyecto=proyecto, activo=True)
    ]
    return CatalogosMotor(rubros, products, categories)


def resolve_rubro_final(proyecto: Proyecto, codigo_rubro: str) -> MapeoRubroCuenta:
    mapping = MapeoRubroCuenta.objects.filter(
        proyecto=proyecto,
        codigo_rubro=normalize_code(codigo_rubro),
        activo=True,
    ).first()
    if mapping is None:
        raise CatalogoNoResuelto(f"No existe el rubro {codigo_rubro}.")
    if mapping.tipo != MapeoRubroCuenta.Tipo.VALORABLE:
        raise CatalogoNoResuelto(f"El rubro {codigo_rubro} es tipo P y no puede ser destino final.")
    return mapping


def resolve_producto_iva(proyecto: Proyecto, codigo_producto: str) -> MapeoProductoIVA:
    try:
        return MapeoProductoIVA.objects.get(
            proyecto=proyecto,
            codigo_producto=normalize_code(codigo_producto),
            activo=True,
        )
    except MapeoProductoIVA.DoesNotExist as error:
        raise CatalogoNoResuelto(f"No existe IVA para el producto {codigo_producto}.") from error


def resolve_categoria_cuenta(proyecto: Proyecto, categoria: str) -> MapeoCategoriaCuenta:
    try:
        return MapeoCategoriaCuenta.objects.get(proyecto=proyecto, categoria=" ".join(categoria.upper().split()), activo=True)
    except MapeoCategoriaCuenta.DoesNotExist as error:
        raise CatalogoNoResuelto(f"No existe cuenta para la categoria {categoria}.") from error


def company_comparison_key(value: str) -> str:
    return re.sub(r"[\s.,]+", "", str(value).upper())
