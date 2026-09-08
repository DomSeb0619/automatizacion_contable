"""Clasificacion inicial de productos por prefijo, sin tocar categorias confirmadas."""

from documentos.models import normalize_code


PREFIXES_CATEGORIA = {
    "101": "MATERIALES",
    "201": "MANO DE OBRA",
    "301": "ALQUILERES Y SERVICIOS",
    "401": "SUBCONTRATOS",
}


def categoria_predeterminada(codigo_producto: str) -> str:
    """Devuelve la categoria para 101/201/301/401, con o sin prefijo IZ-."""
    code = normalize_code(codigo_producto)
    compact = code[3:] if code.startswith("IZ-") else code
    return PREFIXES_CATEGORIA.get(compact[:3], "")
