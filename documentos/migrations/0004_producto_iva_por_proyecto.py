from django.db import migrations, models
import django.db.models.deletion


def asignar_productos_a_proyecto(apps, schema_editor):
    Empresa = apps.get_model("documentos", "Empresa")
    MapeoProductoIVA = apps.get_model("documentos", "MapeoProductoIVA")
    Proyecto = apps.get_model("documentos", "Proyecto")

    for empresa in Empresa.objects.all().iterator():
        proyectos = list(Proyecto.objects.filter(empresa_id=empresa.pk).order_by("pk")[:2])
        if len(proyectos) != 1:
            raise RuntimeError(
                "No se puede migrar el maestro producto-IVA de una empresa con cero o varios proyectos. "
                "Asigne cada producto manualmente antes de ejecutar esta migracion."
            )
        MapeoProductoIVA.objects.filter(empresa_id=empresa.pk).update(proyecto_id=proyectos[0].pk)


class Migration(migrations.Migration):

    dependencies = [
        ("documentos", "0003_alter_mapeocategoriacuenta_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="mapeoproductoiva",
            name="proyecto",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="mapeos_producto_iva",
                to="documentos.proyecto",
            ),
        ),
        migrations.RunPython(asignar_productos_a_proyecto, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mapeoproductoiva",
            name="proyecto",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="mapeos_producto_iva",
                to="documentos.proyecto",
            ),
        ),
        migrations.AlterModelOptions(
            name="mapeoproductoiva",
            options={"ordering": ["proyecto", "codigo_producto"]},
        ),
        migrations.RemoveConstraint(
            model_name="mapeoproductoiva",
            name="uq_producto_empresa_codigo",
        ),
        migrations.RemoveField(
            model_name="mapeoproductoiva",
            name="empresa",
        ),
        migrations.AddConstraint(
            model_name="mapeoproductoiva",
            constraint=models.UniqueConstraint(
                fields=("proyecto", "codigo_producto"),
                name="uq_producto_proyecto_codigo",
            ),
        ),
        migrations.CreateModel(
            name="ActualizacionCatalogo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("PRODUCTOS", "Productos e IVA"), ("RUBROS", "Rubros y cuentas")], max_length=20)),
                ("nombre_archivo", models.CharField(max_length=255)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                ("creados", models.PositiveIntegerField(default=0)),
                ("actualizados", models.PositiveIntegerField(default=0)),
                ("omitidos", models.PositiveIntegerField(default=0)),
                ("rechazados", models.PositiveIntegerField(default=0)),
                ("proyecto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="actualizaciones_catalogo", to="documentos.proyecto")),
            ],
            options={"ordering": ["proyecto", "tipo"]},
        ),
        migrations.AddConstraint(
            model_name="actualizacioncatalogo",
            constraint=models.UniqueConstraint(
                fields=("proyecto", "tipo"),
                name="uq_actualizacion_catalogo_proyecto_tipo",
            ),
        ),
    ]
