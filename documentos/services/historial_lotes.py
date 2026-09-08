"""Consulta de solo lectura para el historial visible de lotes."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Count, Q

from documentos.models import LoteCarga
from documentos.services.previsualizacion_lotes import money_string


def get_batch_history(search: str = "", *, empresa: str = "", proyecto: str = "", estado: str = "", page: int = 1):
    """Devuelve lotes recientes sin datos tecnicos de almacenamiento."""
    batches = LoteCarga.objects.select_related("proyecto__empresa").annotate(document_count=Count("documentos"))
    search = search.strip()
    if search:
        filter_query = (
            Q(documentos__proveedor__icontains=search)
            | Q(documentos__ruc_proveedor__icontains=search)
            | Q(documentos__numero_factura_proveedor__icontains=search)
            | Q(documentos__numero_factura_normalizado__icontains=search)
        )
        if search.isdigit():
            filter_query |= Q(pk=int(search))
        batches = batches.filter(filter_query).distinct()
    if empresa:
        batches = batches.filter(proyecto__empresa_id=empresa)
    if proyecto:
        batches = batches.filter(proyecto_id=proyecto)
    if estado:
        batches = batches.filter(estado=estado)
    paginator = Paginator(batches.order_by("-creado_en"), 20)
    page_obj = paginator.get_page(page)
    rows = [
        {
            "id": batch.id,
            "fecha_creacion": batch.creado_en,
            "empresa": batch.proyecto.empresa.nombre,
            "proyecto": batch.proyecto.codigo,
            "estado": batch.estado,
            "documentos": batch.document_count,
            "total_debito": money_string(batch.total_debito),
            "total_credito": money_string(batch.total_credito),
            "diferencia": money_string(batch.diferencia),
        }
        for batch in page_obj.object_list
    ]
    return {"lotes": rows, "page_obj": page_obj}
