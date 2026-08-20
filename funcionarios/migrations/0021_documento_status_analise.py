import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def aprovar_documentos_existentes(apps, schema_editor):
    Documento = apps.get_model('funcionarios', 'Documento')
    Documento.objects.all().update(status='aprovado')


def reverter_aprovacao(apps, schema_editor):
    Documento = apps.get_model('funcionarios', 'Documento')
    Documento.objects.all().update(status='pendente')


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0020_tipodocumento_eh_contrato'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='documento',
            name='status',
            field=models.CharField(choices=[('pendente', 'Pendente de análise'), ('aprovado', 'Aprovado'), ('rejeitado', 'Rejeitado')], default='pendente', max_length=10),
        ),
        migrations.AddField(
            model_name='documento',
            name='motivo_rejeicao',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='documento',
            name='analisado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='documentos_analisados', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='documento',
            name='analisado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Documentos enviados antes de existir revisão do RH: tratamos como já aprovados,
        # senão os 279 funcionários já ativos passariam a exibir documentos "pendentes de
        # análise" que nunca foram de fato revisados por ninguém.
        migrations.RunPython(aprovar_documentos_existentes, reverter_aprovacao),
    ]
