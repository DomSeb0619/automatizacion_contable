"""Generacion BIFF8 .xls para el diario de importacion del ERP."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
import re

from django.db import transaction
import xlrd
import xlwt

from documentos.models import DocumentoLote, LineaReclasificacion, LoteCarga
from documentos.services.previsualizacion_lotes import get_batch_preview


OLE2_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
MIME_XLS = "application/vnd.ms-excel"
HEADERS = ["Cuenta", "Valor", "Debito/Credito", "Detalle", "numero", "CC01", "CC02", "CC03", "CC04", "CC05"]
ZERO = Decimal("0.00")


class ErrorExportacion(ValueError):
    """La seleccion no cumple las reglas para crear un diario ERP."""


@dataclass(frozen=True)
class ArchivoExportado:
    content: bytes
    filename: str
    content_type: str = MIME_XLS


def export_batch(lote_id: int) -> ArchivoExportado:
    """Genera y valida un .xls antes de marcar los documentos incluidos."""
    # La previsualizacion es la fuente de reglas de elegibilidad para una exportacion nueva.
    preview = get_batch_preview(lote_id)
    with transaction.atomic():
        lote = LoteCarga.objects.select_for_update().get(pk=lote_id)
        documents = list(
            DocumentoLote.objects.select_for_update()
            .filter(lote=lote)
            .prefetch_related("lineas")
            .order_by("id")
        )
        selected = select_documents(lote, documents, preview)
        validate_documents(selected)
        rows = export_rows(selected)
        content = build_xls(rows)
        verify_xls(content, rows)

        if lote.estado != LoteCarga.Estado.EXPORTADO:
            DocumentoLote.objects.filter(pk__in=[document.id for document in selected]).update(
                estado=DocumentoLote.Estado.EXPORTADO
            )
            lote.estado = LoteCarga.Estado.EXPORTADO
            lote.save(update_fields=["estado"])
        return ArchivoExportado(content=content, filename=f"diario_reclasificacion_lote_{lote.id}.xls")


def select_documents(lote: LoteCarga, documents: list[DocumentoLote], preview: dict) -> list[DocumentoLote]:
    if lote.estado == LoteCarga.Estado.EXPORTADO:
        selected = [document for document in documents if document.estado == DocumentoLote.Estado.EXPORTADO]
    else:
        preview_exportable = {document["id"] for document in preview["documentos"] if document["puede_exportar"]}
        selected = [
            document
            for document in documents
            if document.id in preview_exportable
            and document.estado in {DocumentoLote.Estado.VALIDO, DocumentoLote.Estado.ADVERTENCIA}
        ]
    if not selected:
        raise ErrorExportacion("El lote no contiene documentos exportables.")
    return selected


def validate_documents(documents: list[DocumentoLote]) -> None:
    total_debit = ZERO
    total_credit = ZERO
    for document in documents:
        lines = list(document.lineas.all())
        if document.diferencia != ZERO:
            raise ErrorExportacion(f"El documento {document.id} no esta cuadrado.")
        if not lines:
            raise ErrorExportacion(f"El documento {document.id} no tiene lineas.")
        document_debit = ZERO
        document_credit = ZERO
        for line in lines:
            validate_line(line)
            if line.tipo_movimiento == "1":
                document_debit += line.valor
            else:
                document_credit += line.valor
        if document_debit != document_credit:
            raise ErrorExportacion(f"Las lineas del documento {document.id} no cuadran.")
        total_debit += document_debit
        total_credit += document_credit
    if total_debit != total_credit:
        raise ErrorExportacion("El lote exportable no cuadra.")


def validate_line(line: LineaReclasificacion) -> None:
    if not line.cuenta.strip():
        raise ErrorExportacion("Existe una linea sin cuenta.")
    if line.valor <= ZERO:
        raise ErrorExportacion("Existe una linea con valor no positivo.")
    if line.tipo_movimiento not in {"1", "2"}:
        raise ErrorExportacion("Existe una linea con tipo de movimiento invalido.")


def export_rows(documents: list[DocumentoLote]) -> list[tuple]:
    rows: list[tuple] = []
    for document in documents:
        detail = journal_detail(document)
        for line in sorted(document.lineas.all(), key=lambda item: item.orden):
            rows.append((line.cuenta, line.valor, int(line.tipo_movimiento), detail, "", "", "", "", "", ""))
    return rows


def journal_detail(document: DocumentoLote) -> str:
    """Texto legible para el diario; el numero completo sigue persistido aparte."""
    groups = re.findall(r"\d+", document.numero_factura_normalizado or document.numero_factura_proveedor)
    invoice = str(int(groups[-1])) if groups else document.numero_factura_normalizado
    return f"RECLASIFICACION F {invoice} {document.proveedor}".strip()


def build_xls(rows: list[tuple]) -> bytes:
    output = BytesIO()
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("DIARIO_CONT")
    widths = [3948, 3072, 3692, 7936, 5404, 2924, 2924, 2924, 2924, 2924]
    for column, width in enumerate(widths):
        sheet.col(column).width = width
    sheet.row(0).height = 300
    header_style = xlwt.easyxf("font: bold on")
    text_style = xlwt.easyxf("")
    amount_style = xlwt.easyxf("", num_format_str="0.00")
    integer_style = xlwt.easyxf("", num_format_str="0")
    for column, header in enumerate(HEADERS):
        sheet.write(0, column, header, header_style)
    for row_index, row in enumerate(rows, start=1):
        sheet.write(row_index, 0, row[0], text_style)
        sheet.write(row_index, 1, row[1], amount_style)
        sheet.write(row_index, 2, row[2], integer_style)
        sheet.write(row_index, 3, row[3], text_style)
        for column in range(4, 10):
            sheet.write(row_index, column, "", text_style)
    workbook.save(output)
    return output.getvalue()


def verify_xls(content: bytes, rows: list[tuple]) -> None:
    if not content.startswith(OLE2_MAGIC):
        raise ErrorExportacion("El archivo generado no tiene formato OLE2.")
    workbook = xlrd.open_workbook(file_contents=content, formatting_info=True)
    if workbook.biff_version != 80 or workbook.sheet_names() != ["DIARIO_CONT"]:
        raise ErrorExportacion("El archivo generado no tiene la hoja BIFF esperada.")
    sheet = workbook.sheet_by_name("DIARIO_CONT")
    if sheet.row_values(0) != HEADERS:
        raise ErrorExportacion("Los encabezados del archivo generado no coinciden.")
    if sheet.nrows != len(rows) + 1 or sheet.ncols != 10:
        raise ErrorExportacion("La cantidad de filas o columnas no coincide.")
    for index, expected in enumerate(rows, start=1):
        values = sheet.row_values(index)
        if values[0] != expected[0] or Decimal(str(values[1])).quantize(Decimal("0.01")) != expected[1]:
            raise ErrorExportacion("Los valores exportados no coinciden.")
        if int(values[2]) != expected[2] or values[3] != expected[3] or any(values[column] != "" for column in range(4, 10)):
            raise ErrorExportacion("El mapeo de columnas exportado no coincide.")
