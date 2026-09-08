"""Adaptadores entre registros SQLite y las estructuras puras del motor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

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
    empresa = Empresa.objects.get(codigo=normalize_identifier(document.company), activa=True)
    proyecto = Proyecto.objects.get(empresa=empresa, codigo=normalize_code(document.project), activo=True)
    return catalogos_para_proyecto(proyecto)


def catalogos_para_proyecto(proyecto: Proyecto) -> CatalogosMotor:
    empresa = proyecto.empresa
    rubros = [
        RubroAccount(empresa.nombre, proyecto.codigo, mapping.codigo_rubro, mapping.cuenta_contable)
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
        CategoryGeneralAccount(empresa.nombre, proyecto.codigo, mapping.categoria, mapping.cuenta_general_original)
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
