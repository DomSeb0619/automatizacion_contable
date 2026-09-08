from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST

from documentos.forms import CatalogUploadForm, CategoriaManualForm, CargaLoteForm, ConfiguracionInstitucionalForm, EmpresaForm, ProductoManualForm, ProyectoForm, RubroManualForm
from documentos.models import ConfiguracionInstitucional, Empresa, LoteCarga, MapeoCategoriaCuenta, MapeoProductoIVA, MapeoRubroCuenta, Proyecto
from documentos.services.configuracion_catalogos import CatalogoInvalido, ConfirmacionInvalida, VistaCatalogo, aplicar_previsualizacion, previsualizar_catalogo
from documentos.services.exportacion_erp import ErrorExportacion, export_batch
from documentos.services.historial_lotes import get_batch_history
from documentos.services.previsualizacion_lotes import LoteNoEncontrado, get_batch_preview
from documentos.services.procesamiento_lotes import process_temporary_files
from documentos.services.configuracion_proyecto import actualizacion_catalogo, resumir_configuracion


def cargar_lote(request):
    if request.method == 'POST':
        form = CargaLoteForm(request.POST, request.FILES)
        if form.is_valid():
            resumen = resumir_configuracion(form.cleaned_data['proyecto'])
            if not resumen.completa:
                messages.warning(request, 'El proyecto tiene una configuración incompleta; revise sus catálogos antes de validar el resultado.')
            try:
                settings.UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=settings.UPLOAD_TEMP_DIR) as temporary_directory:
                    paths = write_temporary_uploads(Path(temporary_directory), form.cleaned_data['archivos'])
                    result = process_temporary_files(form.cleaned_data['proyecto'], paths)
            except Exception:
                form.add_error(None, 'No fue posible procesar los archivos. Intenta nuevamente.')
            else:
                return redirect('documentos:previsualizar_lote', lote_id=result.lote_id)
    else:
        form = CargaLoteForm()
    return render(request, 'documentos/carga_lote.html', {'form': form})


def previsualizar_lote(request, lote_id: int):
    try:
        preview = get_batch_preview(lote_id)
    except LoteNoEncontrado as error:
        raise Http404(str(error)) from error
    return render(request, 'documentos/previsualizacion_lote.html', {'preview': preview})


def historial_lotes(request):
    search = request.GET.get('q', '')
    history = get_batch_history(
        search, empresa=request.GET.get('empresa', ''), proyecto=request.GET.get('proyecto', ''),
        estado=request.GET.get('estado', ''), page=request.GET.get('page', 1),
    )
    return render(request, 'documentos/historial_lotes.html', {
        **history, 'query': search, 'empresas': Empresa.objects.filter(activa=True),
        'proyectos': Proyecto.objects.filter(activo=True, empresa__activa=True), 'estados': LoteCarga.Estado.choices,
    })


def configurar_institucion(request):
    config, _ = ConfiguracionInstitucional.objects.get_or_create(pk=1)
    if request.method == 'POST':
        form = ConfiguracionInstitucionalForm(request.POST, request.FILES, instance=config)
        if form.is_valid():
            if form.cleaned_data['eliminar_logotipo'] and config.logotipo:
                config.logotipo.delete(save=False)
                config.logotipo = ''
            form.save()
            messages.success(request, 'Identidad institucional actualizada.')
            return redirect('documentos:configurar_institucion')
    else:
        form = ConfiguracionInstitucionalForm(instance=config)
    return render(request, 'documentos/configurar_institucion.html', {'form': form, 'configuracion': config})


@require_POST
def exportar_lote(request, lote_id: int):
    try:
        exported = export_batch(lote_id)
    except LoteNoEncontrado as error:
        raise Http404(str(error)) from error
    except ErrorExportacion as error:
        messages.error(request, str(error))
        return redirect('documentos:previsualizar_lote', lote_id=lote_id)
    response = HttpResponse(exported.content, content_type=exported.content_type)
    response['Content-Disposition'] = f'attachment; filename="{exported.filename}"'
    return response


def configurar_catalogos(request):
    if request.method == 'POST':
        action = request.POST.get('accion')
        if action == 'previsualizar':
            request.session.pop('catalogo_preview', None)
            form = CatalogUploadForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    preview = preview_uploaded_catalog(form.cleaned_data['proyecto'], form.cleaned_data['tipo'], form.cleaned_data['archivo'])
                except CatalogoInvalido as error:
                    form.add_error('archivo', str(error))
                else:
                    request.session['catalogo_preview'] = preview.as_session()
                    return render_catalogos(request, form.cleaned_data['proyecto'], preview=preview, upload_form=form)
            return render_catalogos(request, upload_form=form)
        if action in {'guardar_producto', 'guardar_rubro', 'guardar_categoria'}:
            return guardar_catalogo_manual(request, action)
    project = selected_project(request)
    return render_catalogos(request, project)


def configurar_proyectos(request):
    empresa_id = request.GET.get('editar_empresa')
    proyecto_id = request.GET.get('editar_proyecto')
    empresa = Empresa.objects.filter(pk=empresa_id).first() if empresa_id else None
    proyecto = Proyecto.objects.select_related('empresa').filter(pk=proyecto_id).first() if proyecto_id else None
    if request.method == 'POST':
        action = request.POST.get('accion')
        if action == 'guardar_empresa':
            form = EmpresaForm(request.POST, instance=Empresa.objects.filter(pk=request.POST.get('registro_id')).first())
            if form.is_valid():
                form.save()
                messages.success(request, 'Empresa guardada.')
                return redirect('documentos:configurar_proyectos')
            empresa_form, proyecto_form = form, ProyectoForm()
        else:
            form = ProyectoForm(request.POST, instance=Proyecto.objects.filter(pk=request.POST.get('registro_id')).first())
            if form.is_valid():
                form.save()
                messages.success(request, 'Proyecto guardado.')
                return redirect('documentos:configurar_proyectos')
            empresa_form, proyecto_form = EmpresaForm(), form
    else:
        empresa_form = EmpresaForm(instance=empresa)
        proyecto_form = ProyectoForm(instance=proyecto)
    projects = Proyecto.objects.select_related('empresa').all()
    return render(request, 'documentos/configurar_proyectos.html', {
        'empresa_form': empresa_form,
        'proyecto_form': proyecto_form,
        'empresa_editada': empresa,
        'proyecto_editado': proyecto,
        'empresas': Empresa.objects.all(),
        'proyectos': [(item, resumir_configuracion(item)) for item in projects],
    })


@require_POST
def confirmar_catalogo(request):
    payload = request.session.get('catalogo_preview')
    if not payload:
        messages.error(request, 'No existe una previsualizacion pendiente para confirmar.')
        return redirect('documentos:configurar_catalogos')
    try:
        result = aplicar_previsualizacion(payload, acepta_rechazados=request.POST.get('acepta_rechazados') == 'on')
    except ConfirmacionInvalida as error:
        messages.error(request, str(error))
        project = selected_project_by_id(payload.get('proyecto_id'))
        return render_catalogos(request, project, preview=session_preview(payload))
    del request.session['catalogo_preview']
    messages.success(request, f'Catalogo actualizado: {result.creados} creados, {result.actualizados} actualizados, {result.omitidos} omitidos y {result.rechazados} rechazados.')
    return redirect(f"{redirect('documentos:configurar_catalogos').url}?proyecto={result.proyecto_id}")


def guardar_catalogo_manual(request, action: str):
    forms_by_action = {
        'guardar_producto': (ProductoManualForm, 'producto'),
        'guardar_rubro': (RubroManualForm, 'rubro'),
        'guardar_categoria': (CategoriaManualForm, 'categoria'),
    }
    form_class, kind = forms_by_action[action]
    form = form_class(request.POST)
    if not form.is_valid():
        return render_catalogos(request, upload_form=CatalogUploadForm(), manual_forms={kind: form})
    try:
        save_manual_catalog(kind, form.cleaned_data)
    except (IntegrityError, ValueError) as error:
        form.add_error(None, f'No fue posible guardar el registro: {error}')
        return render_catalogos(request, form.cleaned_data['proyecto'], manual_forms={kind: form})
    messages.success(request, 'Registro de catalogo guardado.')
    return redirect(f"{redirect('documentos:configurar_catalogos').url}?proyecto={form.cleaned_data['proyecto'].id}")


def render_catalogos(request, project=None, *, preview: VistaCatalogo | None = None, upload_form=None, manual_forms=None):
    project = project or selected_project(request)
    manual_forms = manual_forms or {}
    query = request.GET.get('q', '').strip()
    category_form = manual_forms.get('categoria') or CategoriaManualForm(initial={'proyecto': project.id} if project else None)
    if project and 'categoria' not in manual_forms:
        edit_id = request.GET.get('editar_categoria')
        record = MapeoCategoriaCuenta.objects.filter(pk=edit_id, proyecto=project).first() if edit_id else None
        if record:
            category_form = CategoriaManualForm(initial={
                'registro_id': record.id,
                'proyecto': project.id,
                'categoria': record.categoria,
                'cuenta_general_original': record.cuenta_general_original,
                'descripcion': record.descripcion,
                'activo': record.activo,
            })
    context = {
        'project': project,
        'query': query,
        'preview': preview or session_preview(request.session.get('catalogo_preview')),
        'upload_form': upload_form or CatalogUploadForm(initial={'proyecto': project.id} if project else None),
        'producto_form': manual_forms.get('producto') or ProductoManualForm(initial={'proyecto': project.id} if project else None),
        'rubro_form': manual_forms.get('rubro') or RubroManualForm(initial={'proyecto': project.id} if project else None),
        'categoria_form': category_form,
        'products': [],
        'rubros': [],
        'categories': [],
    }
    if project:
        product_filter = Q(proyecto=project)
        rubro_filter = Q(proyecto=project)
        category_filter = Q(proyecto=project)
        if query:
            product_filter &= Q(codigo_producto__icontains=query) | Q(descripcion__icontains=query) | Q(categoria__icontains=query)
            rubro_filter &= Q(codigo_rubro__icontains=query) | Q(descripcion__icontains=query) | Q(cuenta_contable__icontains=query)
            category_filter &= Q(categoria__icontains=query) | Q(cuenta_general_original__icontains=query)
        context['products'] = MapeoProductoIVA.objects.filter(product_filter).order_by('codigo_producto')[:100]
        context['rubros'] = MapeoRubroCuenta.objects.filter(rubro_filter).order_by('codigo_rubro')[:100]
        context['categories'] = MapeoCategoriaCuenta.objects.filter(category_filter).order_by('categoria')[:100]
        context['resumen_configuracion'] = resumir_configuracion(project)
        context['actualizacion_productos'] = actualizacion_catalogo(project, 'PRODUCTOS')
        context['actualizacion_rubros'] = actualizacion_catalogo(project, 'RUBROS')
    return render(request, 'documentos/configurar_catalogos.html', context)


def selected_project(request):
    return selected_project_by_id(request.GET.get('proyecto'))


def selected_project_by_id(value):
    if not value:
        return None
    return Proyecto.objects.select_related('empresa').filter(pk=value, activo=True, empresa__activa=True).first()


def preview_uploaded_catalog(project: Proyecto, kind: str, uploaded) -> VistaCatalogo:
    settings.UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=settings.UPLOAD_TEMP_DIR) as temporary_directory:
        path = write_temporary_uploads(Path(temporary_directory), [uploaded])[0]
        return previsualizar_catalogo(project, kind, path, nombre_archivo=Path(uploaded.name).name)


def session_preview(payload) -> VistaCatalogo | None:
    if not payload:
        return None
    return VistaCatalogo(
        payload['tipo'], payload['proyecto_id'], payload['filas'], payload['creados'], payload['actualizados'],
        payload['omitidos'], payload['rechazados'], payload['errores'], payload.get('nombre_archivo', ''),
    )


def save_manual_catalog(kind: str, data: dict) -> None:
    project = data['proyecto']
    with transaction.atomic():
        if kind == 'producto':
            record = manual_record(MapeoProductoIVA, data.get('registro_id'), proyecto=project)
            record = record or MapeoProductoIVA.objects.filter(proyecto=project, codigo_producto=data['codigo_producto']).first()
            record = record or MapeoProductoIVA(proyecto=project)
            record.codigo_producto = data['codigo_producto']
            record.descripcion = data['descripcion']
            record.porcentaje_iva = data['porcentaje_iva']
            record.categoria = data['categoria']
        elif kind == 'rubro':
            record = manual_record(MapeoRubroCuenta, data.get('registro_id'), proyecto=project)
            record = record or MapeoRubroCuenta.objects.filter(proyecto=project, codigo_rubro=data['codigo_rubro']).first()
            record = record or MapeoRubroCuenta(proyecto=project)
            record.codigo_rubro = data['codigo_rubro']
            record.descripcion = data['descripcion']
            record.tipo = data['tipo']
            record.cuenta_contable = data['cuenta_contable']
        else:
            record = manual_record(MapeoCategoriaCuenta, data.get('registro_id'), proyecto=project)
            record = record or MapeoCategoriaCuenta.objects.filter(
                proyecto=project,
                categoria=data['categoria'],
                cuenta_general_original=data['cuenta_general_original'],
            ).first()
            record = record or MapeoCategoriaCuenta(proyecto=project)
            record.categoria = data['categoria']
            record.cuenta_general_original = data['cuenta_general_original']
            record.descripcion = data['descripcion']
        record.activo = data['activo']
        record.save()


def manual_record(model, record_id, **scope):
    if not record_id:
        return None
    record = model.objects.filter(pk=record_id, **scope).first()
    if record is None:
        raise ValueError('El registro no pertenece a la empresa o proyecto seleccionado.')
    return record


def write_temporary_uploads(directory: Path, uploads) -> list[Path]:
    paths: list[Path] = []
    for index, uploaded in enumerate(uploads, start=1):
        filename = get_valid_filename(Path(uploaded.name).name)
        path = directory / f'{index}_{filename}'
        with path.open('wb') as destination:
            for chunk in uploaded.chunks():
                destination.write(chunk)
        paths.append(path)
    return paths
