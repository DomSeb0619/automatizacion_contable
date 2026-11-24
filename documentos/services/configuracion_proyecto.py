"""Resumen de configuracion aislado por proyecto para la interfaz y el procesamiento."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from documentos.models import ActualizacionCatalogo, MapeoCategoriaCuenta, MapeoProductoCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto


@dataclass(frozen=True)
class ResumenConfiguracionProyecto:
    productos_activos: int
    rubros_finales_activos: int
    categorias_configuradas: int
    cuentas_generales_permitidas: int
    ultima_actualizacion: datetime | None
    modalidad_productos: bool = False

    @property
    def completa(self) -> bool:
        if self.modalidad_productos:
            return self.productos_activos > 0 and self.categorias_configuradas > 0 and self.cuentas_generales_permitidas > 0
        return (
            self.productos_activos > 0
            and self.rubros_finales_activos > 0
            and self.categorias_configuradas > 0
            and self.cuentas_generales_permitidas > 0
        )

    @property
    def estado(self) -> str:
        return "Configuracion completa" if self.completa else "Configuracion incompleta"


def resumir_configuracion(proyecto: Proyecto) -> ResumenConfiguracionProyecto:
    if proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS:
        equivalencias = MapeoProductoCuenta.objects.filter(proyecto=proyecto, activo=True, validada=True)
        ultima = ActualizacionCatalogo.objects.filter(proyecto=proyecto).order_by("-actualizado_en").first()
        return ResumenConfiguracionProyecto(
            productos_activos=equivalencias.count(),
            rubros_finales_activos=0,
            categorias_configuradas=equivalencias.exclude(categoria_contable="").values("categoria_contable").distinct().count(),
            cuentas_generales_permitidas=equivalencias.exclude(cuenta_inventario_origen="").values("cuenta_inventario_origen").distinct().count(),
            ultima_actualizacion=ultima.actualizado_en if ultima else None,
            modalidad_productos=True,
        )
    productos = MapeoProductoIVA.objects.filter(
        proyecto=proyecto,
        activo=True,
        porcentaje_iva__gte=0,
        porcentaje_iva__lte=100,
    ).count()
    rubros = MapeoRubroCuenta.objects.filter(
        proyecto=proyecto,
        activo=True,
        tipo=MapeoRubroCuenta.Tipo.VALORABLE,
    ).exclude(cuenta_contable="").count()
    categorias = MapeoCategoriaCuenta.objects.filter(proyecto=proyecto, activo=True)
    ultima = ActualizacionCatalogo.objects.filter(proyecto=proyecto).order_by("-actualizado_en").first()
    return ResumenConfiguracionProyecto(
        productos_activos=productos,
        rubros_finales_activos=rubros,
        categorias_configuradas=categorias.values("categoria").distinct().count(),
        cuentas_generales_permitidas=categorias.count(),
        ultima_actualizacion=ultima.actualizado_en if ultima else None,
    )


def actualizacion_catalogo(proyecto: Proyecto, tipo: str) -> ActualizacionCatalogo | None:
    return ActualizacionCatalogo.objects.filter(proyecto=proyecto, tipo=tipo).first()
