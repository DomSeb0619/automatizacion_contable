"""Consulta de solo lectura para la futura previsualizacion de un lote."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Prefetch

from documentos.models import DocumentoLote, LineaReclasificacion, LoteCarga


ZERO = Decimal("0.00")


class LoteNoEncontrado(LookupError):
    """El lote solicitado no existe."""


def get_batch_preview(lote_id: int) -> dict:
    """Devuelve datos serializables sin escribir en la base de datos."""
    try:
        lote = (
            LoteCarga.objects.select_related("proyecto__empresa")
            .prefetch_related(
                Prefetch(
                    "documentos",
                    queryset=DocumentoLote.objects.order_by("id").prefetch_related(
                        Prefetch("lineas", queryset=LineaReclasificacion.objects.order_by("orden"))
                    ),
                )
            )
            .get(pk=lote_id)
        )
    except LoteCarga.DoesNotExist as error:
        raise LoteNoEncontrado(f"No existe el lote {lote_id}.") from error

    documents = [serialize_document(document) for document in lote.documentos.all()]
    duplicate_only = bool(documents) and all(document["estado"] == DocumentoLote.Estado.DUPLICADO for document in documents)
    return {
        "id": lote.id,
        "empresa": lote.proyecto.empresa.nombre,
        "proyecto": {"codigo": lote.proyecto.codigo, "nombre": lote.proyecto.nombre},
        "fecha_creacion": lote.creado_en.isoformat(),
        "estado": "SIN DOCUMENTOS NUEVOS" if duplicate_only else lote.estado,
        "cantidad_archivos": lote.cantidad_archivos,
        "cantidad_validos": sum(document["estado"] == DocumentoLote.Estado.VALIDO for document in documents),
        "cantidad_exportables": sum(document["puede_exportar"] for document in documents),
        "cantidad_advertencias": sum(document["estado"] == DocumentoLote.Estado.ADVERTENCIA for document in documents),
        "cantidad_con_errores": sum(document["estado"] == DocumentoLote.Estado.ERROR for document in documents),
        "cantidad_duplicados": sum(document["estado"] == DocumentoLote.Estado.DUPLICADO for document in documents),
        "total_debito": money_string(lote.total_debito),
        "total_credito": money_string(lote.total_credito),
        "diferencia": money_string(lote.diferencia),
        "puede_exportar": any(document["puede_exportar"] or document["puede_redescargar"] for document in documents),
        "documentos": documents,
    }


def serialize_document(document: DocumentoLote) -> dict:
    lines = [serialize_line(line) for line in document.lineas.all()]
    errors = [item for item in document.mensajes if item.get("level") == "error"]
    warnings = [item for item in document.mensajes if item.get("level") == "warning"]
    can_export = (
        document.estado in {DocumentoLote.Estado.VALIDO, DocumentoLote.Estado.ADVERTENCIA}
        and bool(lines)
        and document.diferencia == ZERO
    )
    can_redownload = document.estado == DocumentoLote.Estado.EXPORTADO and bool(lines) and document.diferencia == ZERO
    return {
        "id": document.id,
        "nombre_original": document.nombre_archivo_original,
        "proveedor": document.proveedor,
        "ruc": document.ruc_proveedor or None,
        "numero_factura_original": document.numero_factura_proveedor,
        "numero_factura_normalizado": document.numero_factura_normalizado,
        "numero_interno_erp": document.numero_interno_erp,
        "estado": document.estado,
        "subtotal": money_string(document.subtotal),
        "iva": money_string(document.iva),
        "total": money_string(document.total),
        "total_debito": money_string(document.total_debito),
        "total_credito": money_string(document.total_credito),
        "diferencia": money_string(document.diferencia),
        "errores": errors,
        "advertencias": warnings,
        "puede_exportar": can_export,
        "puede_redescargar": can_redownload,
        "lineas": lines,
    }


def serialize_line(line: LineaReclasificacion) -> dict:
    return {
        "orden": line.orden,
        "cuenta": line.cuenta,
        "valor": money_string(line.valor),
        "tipo": line.tipo_movimiento,
        "etiqueta_tipo": "Débito" if line.tipo_movimiento == "1" else "Crédito",
        "detalle": line.detalle,
        "codigo_rubro": line.codigo_rubro or None,
        "categoria": line.categoria or None,
        "cuenta_general_origen": line.cuenta_general_origen or None,
    }


def money_string(value: Decimal) -> str:
    return f"{Decimal(value):.2f}"
