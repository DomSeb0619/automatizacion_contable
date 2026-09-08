from documentos.models import ConfiguracionInstitucional


def institucion(request):
    config, _ = ConfiguracionInstitucional.objects.get_or_create(pk=1)
    return {"institucion": config}
