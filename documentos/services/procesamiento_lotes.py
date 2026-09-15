"""Procesamiento transaccional de archivos temporales, sin dependencia HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re

from django.db import transaction

from documentos.models import DocumentoLote, LineaReclasificacion, LoteCarga, Proyecto
from documentos.services.catalogos_sqlite import CatalogoNoResuelto, catalogos_para_documento
from documentos.services.consulta_documentos import ConsultaDocumento, ConsultaDocumentoError, read_consulta_documento
from documentos.services.reclasificacion import Issue, ReclassificationResult, build_reclassification


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LoteProcesado:
    lote_id: int
    estado: str
    cantidad_validos: int
    cantidad_con_errores: int
    total_debito: Decimal
    total_credito: Decimal
    diferencia: Decimal


class ErrorDocumentoEsperado(Exception):
    pass


def process_temporary_files(proyecto: Proyecto, temporary_paths: list[str | Path], *, cleanup: bool = True) -> LoteProcesado:
    """Procesa una lista de rutas temporales y las elimina al terminar si corresponde."""
    paths = [Path(path) for path in temporary_paths]
    try:
        with transaction.atomic():
            lote = LoteCarga.objects.create(
                proyecto=proyecto,
                estado=LoteCarga.Estado.PROCESANDO,
                cantidad_archivos=len(paths),
            )
            hashes_in_batch: set[str] = set()
            for path in paths:
                file_hash = file_sha256(path)
                if file_hash in hashes_in_batch:
                    DocumentoLote.objects.create(
                        lote=lote,
                        nombre_archivo_original=path.name,
                        hash_sha256=file_hash,
                        estado=DocumentoLote.Estado.DUPLICADO,
                        mensajes=[message("error", "ARCHIVO_DUPLICADO_LOTE", "El mismo archivo ya existe en este lote.")],
                    )
                    continue
                hashes_in_batch.add(file_hash)
                process_one_file(lote, path, file_hash)
            finalize_batch(lote)
            return LoteProcesado(
                lote_id=lote.pk,
                estado=lote.estado,
                cantidad_validos=lote.cantidad_validos,
                cantidad_con_errores=lote.cantidad_con_errores,
                total_debito=lote.total_debito,
                total_credito=lote.total_credito,
                diferencia=lote.diferencia,
            )
    finally:
        if cleanup:
            for path in paths:
                path.unlink(missing_ok=True)


def process_one_file(lote: LoteCarga, path: Path, file_hash: str) -> DocumentoLote:
    try:
        document = read_consulta_documento(path)
    except (ConsultaDocumentoError, OSError, ValueError) as error:
        return DocumentoLote.objects.create(
            lote=lote,
            nombre_archivo_original=path.name,
            hash_sha256=file_hash,
            estado=DocumentoLote.Estado.ERROR,
            mensajes=[message("error", "LECTURA_FALLIDA", str(error))],
        )

    record = DocumentoLote.objects.create(
        lote=lote,
        nombre_archivo_original=path.name,
        hash_sha256=file_hash,
        empresa_detectada=document.company,
        proyecto_detectado=document.project,
        proveedor=document.supplier,
        ruc_proveedor=document.supplier_tax_id,
        numero_interno_erp=document.erp_document_number,
        numero_factura_proveedor=document.supplier_invoice_number,
        numero_factura_normalizado=display_invoice_number(document.supplier_invoice_number),
        clave_proveedor=accounting_key(document),
        subtotal=document.subtotal,
        iva=document.vat,
        total=document.total,
    )
    mismatch_messages = scope_messages(lote.proyecto, document)
    if mismatch_messages:
        record.estado = DocumentoLote.Estado.ERROR
        record.mensajes = mismatch_messages
        record.save(update_fields=["estado", "mensajes"])
        return record
    previous = exported_document_before(lote.proyecto, record.clave_proveedor)
    if previous is not None:
        record.estado = DocumentoLote.Estado.DUPLICADO
        record.mensajes = [
            message(
                "error",
                "FACTURA_EXPORTADA",
                f"Esta factura ya fue exportada en el lote #{previous.lote_id}.",
                lote_id=previous.lote_id,
            )
        ]
        record.save(update_fields=["estado", "mensajes"])
        return record

    try:
        catalogs = catalogos_para_documento(document)
    except (CatalogoNoResuelto, LookupError) as error:
        record.estado = DocumentoLote.Estado.ERROR
        record.mensajes = [message("error", "CATALOGO_NO_RESUELTO", str(error))]
        record.save(update_fields=["estado", "mensajes"])
        return record

    result = build_reclassification(document, catalogs.rubro_accounts, catalogs.product_vats, catalogs.category_general_accounts)
    persist_result(record, document, result)
    return record


def persist_result(record: DocumentoLote, document: ConsultaDocumento, result: ReclassificationResult) -> None:
    messages = [message("error", issue.code, issue.message) for issue in result.errors]
    messages.extend(message("warning", issue.code, issue.message) for issue in result.warnings)
    record.total_debito = result.total_debits
    record.total_credito = result.total_credits
    record.diferencia = result.difference
    record.mensajes = messages
    if result.errors:
        record.estado = DocumentoLote.Estado.ERROR
        record.save(update_fields=["estado", "total_debito", "total_credito", "diferencia", "mensajes"])
        return

    record.estado = DocumentoLote.Estado.ADVERTENCIA if result.warnings else DocumentoLote.Estado.VALIDO
    record.save(update_fields=["estado", "total_debito", "total_credito", "diferencia", "mensajes"])
    detail = f"RECLASIFICACION F {record.numero_factura_normalizado} {document.supplier}"
    LineaReclasificacion.objects.bulk_create(
        [
            LineaReclasificacion(
                documento=record,
                orden=index,
                cuenta=entry.account,
                valor=entry.amount,
                tipo_movimiento=entry.debit_credit,
                detalle=detail,
                codigo_rubro=entry.rubro or "",
                cuenta_general_origen=entry.account if entry.debit_credit == "2" else "",
            )
            for index, entry in enumerate(result.entries, start=1)
        ]
    )


def finalize_batch(lote: LoteCarga) -> None:
    valid_states = [DocumentoLote.Estado.VALIDO, DocumentoLote.Estado.ADVERTENCIA]
    documents = list(lote.documentos.all())
    valid_documents = [document for document in documents if document.estado in valid_states]
    error_documents = [document for document in documents if document.estado not in valid_states]
    lote.cantidad_validos = len(valid_documents)
    lote.cantidad_con_errores = len(error_documents)
    lote.total_debito = sum((document.total_debito for document in valid_documents), ZERO)
    lote.total_credito = sum((document.total_credito for document in valid_documents), ZERO)
    lote.diferencia = lote.total_debito - lote.total_credito
    lote.estado = LoteCarga.Estado.CON_ERRORES if error_documents else LoteCarga.Estado.VALIDADO
    lote.save(
        update_fields=[
            "cantidad_validos",
            "cantidad_con_errores",
            "total_debito",
            "total_credito",
            "diferencia",
            "estado",
        ]
    )


def scope_messages(proyecto: Proyecto, document: ConsultaDocumento) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if company_comparison_key(document.company) != company_comparison_key(proyecto.empresa.nombre):
        messages.append(message("error", "EMPRESA_DIFERENTE", "La empresa detectada no coincide con el lote."))
    expected_project = proyecto.codigo_consulta_documentos or proyecto.codigo
    if document.project != expected_project:
        messages.append(message("error", "PROYECTO_DIFERENTE", "El proyecto detectado no coincide con el lote."))
    return messages


def company_comparison_key(value: str) -> str:
    """Compara solo diferencias inocuas de mayúsculas, espacios, puntos y comas."""
    return re.sub(r"[\s.,]+", "", str(value).upper())


def exported_document_before(proyecto: Proyecto, provider_key: str) -> DocumentoLote | None:
    return DocumentoLote.objects.filter(
        lote__proyecto=proyecto,
        clave_proveedor=provider_key,
        estado=DocumentoLote.Estado.EXPORTADO,
    ).order_by("lote_id", "id").first()


def accounting_key(document: ConsultaDocumento) -> str:
    invoice = normalize_key(document.supplier_invoice_number)
    if document.supplier_tax_id:
        return f"RUC:{normalize_key(document.supplier_tax_id)}|FACTURA:{invoice}"
    # Sin RUC, proveedor normalizado + factura es la alternativa auditable.
    return f"PROVEEDOR:{normalize_key(document.supplier)}|FACTURA:{invoice}"


def display_invoice_number(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def message(level: str, code: str, text: str, **extra) -> dict[str, str]:
    return {"level": level, "code": code, "message": text, **extra}
