from django.contrib import admin
from .models import Setor, Funcionario, Usuario, TipoDocumento, Documento

admin.site.register(Setor)
admin.site.register(Funcionario)
admin.site.register(Usuario)
admin.site.register(TipoDocumento)
admin.site.register(Documento)

# Register your models here.
