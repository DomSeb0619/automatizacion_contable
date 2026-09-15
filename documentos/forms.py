from pathlib import Path

from django import forms
from django.conf import settings

from documentos.models import ConfiguracionInstitucional, Empresa, Proyecto
from documentos.services.exportacion_erp import OLE2_MAGIC


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleFileField, self).clean(uploaded, initial) for uploaded in files if uploaded]


class CargaLoteForm(forms.Form):
    proyecto = forms.ModelChoiceField(queryset=Proyecto.objects.none(), empty_label='Selecciona empresa y proyecto')
    archivos = MultipleFileField(required=True, widget=MultipleFileInput(attrs={'accept': '.xls'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proyecto'].queryset = Proyecto.objects.select_related('empresa').filter(activo=True, empresa__activa=True)

    def clean_archivos(self):
        files = self.cleaned_data['archivos']
        if not files:
            raise forms.ValidationError('Selecciona al menos un archivo .xls.')
        if len(files) > settings.MAX_UPLOAD_FILES:
            raise forms.ValidationError(f'Puedes cargar un máximo de {settings.MAX_UPLOAD_FILES} archivos por lote.')
        for uploaded in files:
            name = Path(uploaded.name).name
            if Path(name).suffix.lower() != '.xls':
                raise forms.ValidationError(f'{name}: solo se permiten archivos .xls.')
            if uploaded.size > settings.MAX_UPLOAD_FILE_SIZE:
                raise forms.ValidationError(f'{name}: supera el máximo de 5 MB por archivo.')
            signature = uploaded.read(len(OLE2_MAGIC))
            uploaded.seek(0)
            if signature != OLE2_MAGIC:
                raise forms.ValidationError(f'{name}: no es un archivo .xls OLE2 válido.')
        return files


class ProyectoActivoForm(forms.Form):
    proyecto = forms.ModelChoiceField(queryset=Proyecto.objects.none(), empty_label='Selecciona empresa y proyecto')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proyecto'].queryset = Proyecto.objects.select_related('empresa').filter(activo=True, empresa__activa=True)


class CatalogUploadForm(ProyectoActivoForm):
    TIPO_CHOICES = [('productos', 'Maestro producto-IVA'), ('rubros', 'Catalogo rubro-cuenta')]

    tipo = forms.ChoiceField(choices=TIPO_CHOICES)
    archivo = forms.FileField(widget=forms.ClearableFileInput(attrs={'accept': '.xlsx'}))

    def clean_archivo(self):
        uploaded = self.cleaned_data['archivo']
        if Path(uploaded.name).suffix.lower() != '.xlsx':
            raise forms.ValidationError('Selecciona un archivo .xlsx.')
        if uploaded.size > settings.MAX_UPLOAD_FILE_SIZE:
            raise forms.ValidationError('El archivo supera el maximo de 5 MB.')
        signature = uploaded.read(4)
        uploaded.seek(0)
        if signature != b'PK\x03\x04':
            raise forms.ValidationError('El archivo no es un .xlsx valido.')
        return uploaded


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ['nombre', 'codigo', 'activa']


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['empresa', 'codigo', 'codigo_consulta_documentos', 'nombre', 'activo']
        help_texts = {
            'codigo_consulta_documentos': 'Opcional. Úsalo cuando Consulta de Documentos entrega un identificador distinto al código presupuestario.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['empresa'].queryset = Empresa.objects.order_by('nombre')


class ConfiguracionInstitucionalForm(forms.ModelForm):
    eliminar_logotipo = forms.BooleanField(required=False, label='Eliminar logotipo personalizado')

    class Meta:
        model = ConfiguracionInstitucional
        fields = ['nombre_organizacion', 'subtitulo', 'logotipo']
        widgets = {'logotipo': forms.ClearableFileInput(attrs={'accept': '.png,.jpg,.jpeg,.webp'})}

    def clean_logotipo(self):
        logo = self.cleaned_data.get('logotipo')
        if not logo:
            return logo
        if logo.size > settings.INSTITUTION_LOGO_MAX_SIZE:
            raise forms.ValidationError('El logotipo supera el tamaño máximo de 2 MB.')
        if Path(logo.name).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
            raise forms.ValidationError('El logotipo debe ser PNG, JPG, JPEG o WebP.')
        try:
            from PIL import Image
            image = Image.open(logo)
            image.verify()
            logo.seek(0)
        except Exception as error:
            raise forms.ValidationError('El archivo no es una imagen válida.') from error
        return logo


class ProductoManualForm(ProyectoActivoForm):
    registro_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    codigo_producto = forms.CharField(max_length=100)
    descripcion = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    porcentaje_iva = forms.DecimalField(min_value=0, max_value=100, max_digits=7, decimal_places=4)
    categoria = forms.CharField(required=False, max_length=150)
    activo = forms.BooleanField(required=False, initial=True)


class RubroManualForm(ProyectoActivoForm):
    registro_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    codigo_rubro = forms.CharField(max_length=100)
    descripcion = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    tipo = forms.ChoiceField(choices=[('P', 'P - Padre'), ('V', 'V - Valorable')])
    cuenta_contable = forms.CharField(max_length=100)
    activo = forms.BooleanField(required=False, initial=True)


class CategoriaManualForm(ProyectoActivoForm):
    registro_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    categoria = forms.CharField(max_length=150)
    cuenta_general_original = forms.CharField(max_length=100)
    descripcion = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    activo = forms.BooleanField(required=False, initial=True)
