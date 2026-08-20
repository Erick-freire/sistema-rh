from django.db import migrations, models


def migrar_status_pendente(apps, schema_editor):
    Funcionario = apps.get_model('funcionarios', 'Funcionario')
    # 'pendente' não existe mais nos choices -- os pré-cadastros em andamento voltam
    # pro início do novo fluxo. 'ativo' e 'inativo' não são tocados (mantém os 279
    # funcionários já importados como estão).
    Funcionario.objects.filter(status='pendente').update(status='pendente_documentos')


def reverter_status_pendente(apps, schema_editor):
    Funcionario = apps.get_model('funcionarios', 'Funcionario')
    Funcionario.objects.filter(
        status__in=['pendente_documentos', 'aguardando_contrato', 'aguardando_assinatura', 'em_validacao']
    ).update(status='pendente')


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0021_documento_status_analise'),
    ]

    operations = [
        migrations.AlterField(
            model_name='funcionario',
            name='status',
            field=models.CharField(choices=[('pendente_documentos', 'Pendente de documentos'), ('aguardando_contrato', 'Aguardando contrato'), ('aguardando_assinatura', 'Aguardando assinatura'), ('em_validacao', 'Em validação'), ('ativo', 'Ativo'), ('inativo', 'Inativo')], default='ativo', max_length=25),
        ),
        migrations.RunPython(migrar_status_pendente, reverter_status_pendente),
    ]
