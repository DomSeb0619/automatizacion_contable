"""Procesamiento transaccional de archivos temporales, sin dependencia HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re

from django.db import transaction

from documentos.models import DocumentoLote, LineaReclasificacion, LoteCarga, MapeoCategoriaCuenta, MapeoProductoCuenta, Proyecto, normalize_code, normalize_category
from documentos.services.catalogos_sqlite import CatalogoNoResuelto, catalogos_para_documento, catalogos_productos_para_proyecto, categorias_productos_para_proyecto
from documentos.services.consulta_documentos import ConsultaDocumento, ConsultaDocumentoError, OriginalAccount, Product, read_consulta_documento
from documentos.services.reclasificacion import Issue, ReclassificationResult, account_code, build_reclassification
from documentos.services.consulta_documentos_verdepamba import read_consulta_verdepamba
from documentos.services.reclasificacion_productos import build_product_reclassification


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
        document = read_document_for_project(lote.proyecto, path)
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

    if lote.proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS:
        result = build_product_reclassification(
            document, catalogos_productos_para_proyecto(lote.proyecto),
            category_accounts=categorias_productos_para_proyecto(lote.proyecto),
        )
    else:
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
    messages = product_revision_messages(record.lote.proyecto, document, result) if record.lote.proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS else [message("error", issue.code, issue.message) for issue in result.errors]
    messages.extend(message("warning", issue.code, issue.message) for issue in result.warnings)
    if record.lote.proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS:
        messages.append(message("metadata", "FUENTE_PRODUCTOS_PAC", "Datos fuente para reprocesamiento local.", snapshot=document_snapshot(document)))
    record.total_debito = result.total_debits
    record.total_credito = result.total_credits
    record.diferencia = result.difference
    record.mensajes = messages
    if result.errors:
        record.estado = DocumentoLote.Estado.ERROR
        record.save(update_fields=["estado", "total_debito", "total_credito", "diferencia", "mensajes"])
        if record.lote.proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS and result.entries:
            persist_lines(record, document, result)
        return

    record.estado = DocumentoLote.Estado.ADVERTENCIA if result.warnings else DocumentoLote.Estado.VALIDO
    record.save(update_fields=["estado", "total_debito", "total_credito", "diferencia", "mensajes"])
    persist_lines(record, document, result)


def persist_lines(record: DocumentoLote, document: ConsultaDocumento, result: ReclassificationResult) -> None:
    """Persiste tanto el diario final como el parcial revisable de PRODUCTOS."""
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
                categoria=entry.category or "",
                cuenta_general_origen=entry.account if entry.debit_credit == "2" else "",
            )
            for index, entry in enumerate(result.entries, start=1)
        ]
    )


def product_revision_messages(proyecto: Proyecto, document: ConsultaDocumento, result: ReclassificationResult) -> list[dict]:
    product_codes = {"PRODUCTO_SIN_CORRESPONDENCIA", "EQUIVALENCIA_PENDIENTE_VALIDACION", "CATEGORIA_CUENTA_NO_AUTORIZADA", "CUENTA_ORIGEN_NO_ENCONTRADA"}
    generic = [message("error", issue.code, issue.message) for issue in result.errors if issue.code not in product_codes]
    mappings = {item.codigo_producto: item for item in MapeoProductoCuenta.objects.filter(proyecto=proyecto)}
    allowed = {(item.categoria, item.cuenta_general_original) for item in MapeoCategoriaCuenta.objects.filter(proyecto=proyecto, activo=True)}
    inventory = {account_code(item.account) for item in document.original_inventory_accounts}
    revisions = []
    for product in document.products:
        mapping = mappings.get(product.code)
        reason = None
        if mapping is None:
            reason = "PRODUCTO_SIN_CORRESPONDENCIA"
        elif not mapping.validada:
            reason = "EQUIVALENCIA_PENDIENTE_VALIDACION"
        elif (mapping.categoria_contable, account_code(mapping.cuenta_inventario_origen)) not in allowed:
            reason = "CATEGORIA_CUENTA_NO_AUTORIZADA"
        elif account_code(mapping.cuenta_inventario_origen) not in inventory:
            reason = "CUENTA_ORIGEN_NO_ENCONTRADA"
        if reason:
            detail = {
                "codigo": product.code, "descripcion": product.description, "importe": f"{product.total:.2f}",
                "destino_actual": mapping.cuenta_destino if mapping else "",
                "categoria": mapping.categoria_contable if mapping else "",
                "cuenta_inventario": mapping.cuenta_inventario_origen if mapping else "",
                "referencia_manual": mapping.justificacion_validacion if mapping and mapping.justificacion_validacion.startswith("Referencia RHR") else "",
                "destino_sugerido": suggested_destination(mapping.justificacion_validacion) if mapping else "",
            }
            text = {
                "PRODUCTO_SIN_CORRESPONDENCIA": f"El producto {product.code} no tiene equivalencia configurada.",
                "EQUIVALENCIA_PENDIENTE_VALIDACION": f"El producto {product.code} tiene una equivalencia pendiente de validación.",
                "CATEGORIA_CUENTA_NO_AUTORIZADA": f"La categoría del producto {product.code} no autoriza su cuenta de inventario.",
                "CUENTA_ORIGEN_NO_ENCONTRADA": f"La cuenta configurada para {product.code} no existe en el asiento original.",
            }[reason]
            revisions.append(message("error", reason, text, revision=detail))
    return generic + revisions


def suggested_destination(justification: str) -> str:
    match = re.search(r"diario manual\s+([^.;]+)", justification or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def document_snapshot(document: ConsultaDocumento) -> dict:
    return {
        "company": document.company, "project": document.project,
        "products": [{"code": item.code, "description": item.description, "quantity": str(item.quantity), "unit_price": str(item.unit_price), "net_price": str(item.net_price), "total": str(item.total)} for item in document.products],
        "accounts": [{"category": item.category, "account": item.account, "debit": str(item.debit), "credit": str(item.credit), "cost_center": item.cost_center, "detail": item.detail} for item in document.original_accounts],
    }


def reprocess_product_document(document_id: int) -> DocumentoLote:
    with transaction.atomic():
        record = DocumentoLote.objects.select_for_update().select_related("lote__proyecto").get(pk=document_id)
        if record.lote.proyecto.modalidad_reclasificacion != Proyecto.ModalidadReclasificacion.PRODUCTOS:
            raise ErrorDocumentoEsperado("La corrección sin archivo solo está disponible para la modalidad PRODUCTOS.")
        if record.estado == DocumentoLote.Estado.EXPORTADO:
            raise ErrorDocumentoEsperado("No se puede reprocesar un documento ya exportado.")
        document = document_from_snapshot(record)
        result = build_product_reclassification(document, catalogos_productos_para_proyecto(record.lote.proyecto), category_accounts=categorias_productos_para_proyecto(record.lote.proyecto))
        record.lineas.all().delete()
        persist_result(record, document, result)
        finalize_batch(record.lote)
        return record


def document_from_snapshot(record: DocumentoLote) -> ConsultaDocumento:
    metadata = next((item for item in record.mensajes if item.get("code") == "FUENTE_PRODUCTOS_PAC"), None)
    if not metadata or not metadata.get("snapshot"):
        raise ErrorDocumentoEsperado("El documento no conserva datos fuente para reprocesarlo.")
    source = metadata["snapshot"]
    products = tuple(Product(item["code"], item.get("description", ""), Decimal(item["quantity"]), Decimal(item["unit_price"]), Decimal(item["net_price"]), Decimal(item["total"])) for item in source["products"])
    accounts = tuple(OriginalAccount(item["category"], item["account"], Decimal(item["debit"]), Decimal(item["credit"]), item.get("cost_center", ""), item.get("detail", "")) for item in source["accounts"])
    return ConsultaDocumento(source["company"], source.get("project", ""), record.proveedor, record.ruc_proveedor, record.numero_interno_erp, record.numero_factura_proveedor, products, record.subtotal, record.iva, record.total, (), accounts)


def correct_product_mapping_for_document(document_id: int, *, code: str, destination: str, category: str, inventory_account: str, justification: str) -> DocumentoLote:
    return correct_product_mappings_for_document(document_id, [{
        "code": code, "destination": destination, "category": category,
        "inventory_account": inventory_account, "justification": justification,
    }])


def correct_product_mappings_for_document(document_id: int, corrections: list[dict]) -> DocumentoLote:
    """Guarda varias correcciones del mismo documento y lo reprocesa una vez."""
    if not corrections:
        raise ErrorDocumentoEsperado("Selecciona al menos una equivalencia para corregir.")
    with transaction.atomic():
        record = DocumentoLote.objects.select_for_update().select_related("lote__proyecto").get(pk=document_id)
        project = record.lote.proyecto
        document = document_from_snapshot(record)
        document_codes = {item.code for item in document.products}
        inventory_codes = {account_code(item.account) for item in document.original_inventory_accounts}
        for correction in corrections:
            code = normalize_code(correction["code"])
            destination = normalize_code(correction["destination"])
            category = normalize_category(correction["category"])
            inventory_account = normalize_code(correction["inventory_account"])
            justification = correction["justification"].strip()
            if not destination or not category or not inventory_account or not justification:
                raise ErrorDocumentoEsperado("Cada corrección debe incluir destino, categoría, cuenta y justificación.")
            if code not in document_codes:
                raise ErrorDocumentoEsperado("El producto no pertenece a este documento.")
            if inventory_account not in inventory_codes:
                raise ErrorDocumentoEsperado("La cuenta de inventario no existe en el asiento original.")
            if not MapeoCategoriaCuenta.objects.filter(proyecto=project, categoria=category, cuenta_general_original=inventory_account, activo=True).exists():
                raise ErrorDocumentoEsperado("La cuenta de inventario no está autorizada para la categoría seleccionada.")
            mapping, _ = MapeoProductoCuenta.objects.get_or_create(proyecto=project, codigo_producto=code, defaults={"cuenta_destino": destination, "categoria_contable": category, "cuenta_inventario_origen": inventory_account})
            mapping.cuenta_destino = destination
            mapping.categoria_contable = category
            mapping.cuenta_inventario_origen = inventory_account
            mapping.validada = True
            mapping.justificacion_validacion = f"Corrección manual documento {record.id}: {justification}"
            mapping.activo = True
            mapping.save()
    return reprocess_product_document(document_id)


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
    if document.project:
        expected_project = proyecto.codigo_consulta_documentos or proyecto.codigo
        if document.project != expected_project:
            messages.append(message("error", "PROYECTO_DIFERENTE", "El proyecto detectado no coincide con el lote."))
    elif proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS:
        enabled = Proyecto.objects.filter(
            empresa=proyecto.empresa, activo=True,
            modalidad_reclasificacion=Proyecto.ModalidadReclasificacion.PRODUCTOS,
            permite_consulta_sin_proyecto=True,
        ).count()
        if not proyecto.permite_consulta_sin_proyecto or enabled != 1:
            messages.append(message("error", "PROYECTO_NO_DETERMINABLE", "El archivo no identifica proyecto y no existe una única configuración PAC habilitada para esta empresa."))
    else:
        messages.append(message("error", "PROYECTO_DIFERENTE", "El proyecto detectado no coincide con el lote."))
    return messages


def read_document_for_project(proyecto: Proyecto, path: Path) -> ConsultaDocumento:
    if proyecto.modalidad_reclasificacion == Proyecto.ModalidadReclasificacion.PRODUCTOS:
        return read_consulta_verdepamba(path)
    return read_consulta_documento(path)


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
