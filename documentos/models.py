from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


def normalize_identifier(value: str) -> str:
    """Normaliza el identificador compacto de una empresa."""
    return " ".join(str(value).upper().replace(".", "").split()).replace(" ", "-")


def normalize_code(value: str) -> str:
    """Conserva separadores significativos, por ejemplo 1.2.3.2.3.05."""
    return " ".join(str(value).upper().split())


def normalize_category(value: str) -> str:
    return " ".join(str(value).upper().split())


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Empresa(TimeStampedModel):
    nombre = models.CharField(max_length=255)
    codigo = models.CharField(max_length=100, unique=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def save(self, *args, **kwargs):
        self.codigo = normalize_code(self.codigo)
        self.nombre = " ".join(self.nombre.split())
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nombre


class Proyecto(TimeStampedModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="proyectos")
    codigo = models.CharField(max_length=100)
    codigo_consulta_documentos = models.CharField(
        max_length=150,
        blank=True,
        help_text="Identificador mostrado por Consulta de Documentos; no cambia el código presupuestario.",
    )
    nombre = models.CharField(max_length=255)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["empresa__nombre", "codigo"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "codigo"], name="uq_proyecto_empresa_codigo"),
        ]

    def save(self, *args, **kwargs):
        self.codigo = normalize_identifier(self.codigo)
        self.codigo_consulta_documentos = normalize_code(self.codigo_consulta_documentos) if self.codigo_consulta_documentos else ""
        self.nombre = " ".join(self.nombre.split())
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.empresa} / {self.codigo}"


class MapeoRubroCuenta(TimeStampedModel):
    class Tipo(models.TextChoices):
        PADRE = "P", "Padre"
        VALORABLE = "V", "Valorable"

    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="mapeos_rubro")
    codigo_rubro = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    tipo = models.CharField(max_length=1, choices=Tipo.choices)
    cuenta_contable = models.CharField(max_length=100)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["proyecto", "codigo_rubro"]
        constraints = [
            models.UniqueConstraint(fields=["proyecto", "codigo_rubro"], name="uq_rubro_proyecto_codigo"),
            models.CheckConstraint(condition=Q(tipo__in=["P", "V"]), name="ck_rubro_tipo_p_o_v"),
        ]

    def save(self, *args, **kwargs):
        self.codigo_rubro = normalize_code(self.codigo_rubro)
        self.tipo = self.tipo.upper().strip()
        self.cuenta_contable = str(self.cuenta_contable).strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.proyecto.codigo} / {self.codigo_rubro} ({self.tipo})"


class MapeoProductoIVA(TimeStampedModel):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="mapeos_producto_iva")
    codigo_producto = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    porcentaje_iva = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    categoria = models.CharField(
        max_length=150,
        blank=True,
        help_text="Relacion local producto-categoria; no proviene del maestro de IVA.",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["proyecto", "codigo_producto"]
        constraints = [
            models.UniqueConstraint(fields=["proyecto", "codigo_producto"], name="uq_producto_proyecto_codigo"),
            models.CheckConstraint(
                condition=Q(porcentaje_iva__gte=0) & Q(porcentaje_iva__lte=100),
                name="ck_producto_iva_0_100",
            ),
        ]

    def save(self, *args, **kwargs):
        self.codigo_producto = normalize_code(self.codigo_producto)
        self.categoria = normalize_category(self.categoria) if self.categoria else ""
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.proyecto.codigo} / {self.codigo_producto} ({self.porcentaje_iva}%)"


class ActualizacionCatalogo(models.Model):
    """Metadatos de la ultima carga confirmada, sin conservar el archivo fuente."""

    class Tipo(models.TextChoices):
        PRODUCTOS = "PRODUCTOS", "Productos e IVA"
        RUBROS = "RUBROS", "Rubros y cuentas"

    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="actualizaciones_catalogo")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    nombre_archivo = models.CharField(max_length=255)
    actualizado_en = models.DateTimeField(auto_now=True)
    creados = models.PositiveIntegerField(default=0)
    actualizados = models.PositiveIntegerField(default=0)
    omitidos = models.PositiveIntegerField(default=0)
    rechazados = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["proyecto", "tipo"]
        constraints = [
            models.UniqueConstraint(fields=["proyecto", "tipo"], name="uq_actualizacion_catalogo_proyecto_tipo"),
        ]

    def __str__(self) -> str:
        return f"{self.proyecto.codigo} / {self.get_tipo_display()}"


class ConfiguracionInstitucional(TimeStampedModel):
    """Identidad visual unica; el logo se gestiona con el almacenamiento de medios."""

    nombre_organizacion = models.CharField(max_length=255, default="Meneses Constructores")
    subtitulo = models.CharField(max_length=255, default="Automatización y control contable")
    logotipo = models.ImageField(upload_to="institucion/", blank=True)

    def __str__(self) -> str:
        return self.nombre_organizacion


class MapeoCategoriaCuenta(TimeStampedModel):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="mapeos_categoria")
    categoria = models.CharField(max_length=150)
    cuenta_general_original = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["proyecto", "categoria", "cuenta_general_original"]
        constraints = [
            models.UniqueConstraint(
                fields=["proyecto", "categoria", "cuenta_general_original"],
                name="uq_categoria_proyecto_cuenta",
            ),
        ]

    def save(self, *args, **kwargs):
        self.categoria = normalize_category(self.categoria)
        self.cuenta_general_original = str(self.cuenta_general_original).strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.proyecto.codigo} / {self.categoria} / {self.cuenta_general_original}"


class LoteCarga(models.Model):
    class Estado(models.TextChoices):
        CREADO = "CREADO", "Creado"
        PROCESANDO = "PROCESANDO", "Procesando"
        VALIDADO = "VALIDADO", "Validado"
        CON_ERRORES = "CON_ERRORES", "Con errores"
        EXPORTADO = "EXPORTADO", "Exportado"

    proyecto = models.ForeignKey(Proyecto, on_delete=models.PROTECT, related_name="lotes")
    creado_en = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CREADO)
    cantidad_archivos = models.PositiveIntegerField(default=0)
    cantidad_validos = models.PositiveIntegerField(default=0)
    cantidad_con_errores = models.PositiveIntegerField(default=0)
    total_debito = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_credito = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    diferencia = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return f"Lote {self.pk} / {self.proyecto.codigo}"


class DocumentoLote(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        VALIDO = "VALIDO", "Valido"
        ADVERTENCIA = "ADVERTENCIA", "Advertencia"
        ERROR = "ERROR", "Error"
        DUPLICADO = "DUPLICADO", "Duplicado"
        EXPORTADO = "EXPORTADO", "Exportado"

    lote = models.ForeignKey(LoteCarga, on_delete=models.CASCADE, related_name="documentos")
    nombre_archivo_original = models.CharField(max_length=255)
    hash_sha256 = models.CharField(max_length=64)
    empresa_detectada = models.CharField(max_length=255, blank=True)
    proyecto_detectado = models.CharField(max_length=100, blank=True)
    proveedor = models.CharField(max_length=255, blank=True)
    ruc_proveedor = models.CharField(max_length=30, blank=True)
    numero_interno_erp = models.CharField(max_length=100, blank=True)
    numero_factura_proveedor = models.CharField(max_length=100, blank=True)
    numero_factura_normalizado = models.CharField(max_length=100, blank=True)
    clave_proveedor = models.CharField(max_length=400, blank=True)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_debito = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_credito = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    diferencia = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    mensajes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["lote", "id"]
        indexes = [
            models.Index(fields=["lote", "hash_sha256"], name="idx_documento_lote_hash"),
            models.Index(fields=["clave_proveedor", "estado"], name="idx_documento_clave_estado"),
        ]

    def __str__(self) -> str:
        return f"{self.lote_id} / {self.numero_factura_normalizado or self.nombre_archivo_original}"


class LineaReclasificacion(models.Model):
    class TipoMovimiento(models.TextChoices):
        DEBITO = "1", "Debito"
        CREDITO = "2", "Credito"

    documento = models.ForeignKey(DocumentoLote, on_delete=models.CASCADE, related_name="lineas")
    orden = models.PositiveIntegerField()
    cuenta = models.CharField(max_length=100)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    tipo_movimiento = models.CharField(max_length=1, choices=TipoMovimiento.choices)
    detalle = models.CharField(max_length=500)
    codigo_rubro = models.CharField(max_length=100, blank=True)
    categoria = models.CharField(max_length=150, blank=True)
    cuenta_general_origen = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["documento", "orden"]
        constraints = [
            models.UniqueConstraint(fields=["documento", "orden"], name="uq_linea_documento_orden"),
            models.CheckConstraint(condition=Q(tipo_movimiento__in=["1", "2"]), name="ck_linea_tipo_1_o_2"),
        ]

    def __str__(self) -> str:
        return f"{self.documento_id} / {self.orden} / {self.cuenta}"
