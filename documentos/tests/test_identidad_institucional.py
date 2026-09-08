from io import BytesIO
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from documentos.models import ConfiguracionInstitucional


class IdentidadInstitucionalTests(TestCase):
    def png(self):
        stream = BytesIO()
        Image.new("RGB", (20, 20), "navy").save(stream, "PNG")
        return SimpleUploadedFile("logo.png", stream.getvalue(), content_type="image/png")

    def test_default_identity_uses_text_fallback(self):
        response = self.client.get(reverse("documentos:cargar_lote"))
        self.assertContains(response, "Meneses Constructores")
        self.assertContains(response, "MC")

    def test_valid_logo_and_configurable_name_are_saved(self):
        with TemporaryDirectory(dir=settings.BASE_DIR) as directory, override_settings(MEDIA_ROOT=directory):
            response = self.client.post(reverse("documentos:configurar_institucion"), {
                "nombre_organizacion": "Constructora Ejemplo", "subtitulo": "Control", "logotipo": self.png(),
            })
            config = ConfiguracionInstitucional.objects.get(pk=1)
            self.assertTrue(config.logotipo.url.startswith("/media/institucion/"))
        self.assertEqual(response.status_code, 302)
        config = ConfiguracionInstitucional.objects.get(pk=1)
        self.assertEqual(config.nombre_organizacion, "Constructora Ejemplo")
        self.assertTrue(config.logotipo.name.startswith("institucion/"))

    def test_invalid_extension_and_large_logo_are_rejected(self):
        invalid = SimpleUploadedFile("logo.svg", b"<svg></svg>")
        response = self.client.post(reverse("documentos:configurar_institucion"), {"nombre_organizacion": "X", "subtitulo": "Y", "logotipo": invalid})
        self.assertContains(response, "PNG, JPG, JPEG o WebP")
        with override_settings(INSTITUTION_LOGO_MAX_SIZE=2):
            response = self.client.post(reverse("documentos:configurar_institucion"), {"nombre_organizacion": "X", "subtitulo": "Y", "logotipo": self.png()})
        self.assertContains(response, "supera el tamaño máximo")
