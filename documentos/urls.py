from django.urls import path

from documentos import views


app_name = 'documentos'

urlpatterns = [
    path('', views.cargar_lote, name='cargar_lote'),
    path('catalogos/', views.configurar_catalogos, name='configurar_catalogos'),
    path('proyectos/', views.configurar_proyectos, name='configurar_proyectos'),
    path('institucion/', views.configurar_institucion, name='configurar_institucion'),
    path('catalogos/confirmar/', views.confirmar_catalogo, name='confirmar_catalogo'),
    path('historial/', views.historial_lotes, name='historial_lotes'),
    path('lotes/<int:lote_id>/', views.previsualizar_lote, name='previsualizar_lote'),
    path('lotes/<int:lote_id>/exportar/', views.exportar_lote, name='exportar_lote'),
]
