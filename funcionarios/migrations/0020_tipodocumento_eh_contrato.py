from django.db import migrations, models


def marcar_tipo_contrato(apps, schema_editor):
    TipoDocumento = apps.get_model('funcionarios', 'TipoDocumento')
    TipoDocumento.objects.filter(nome='Contrato de Experiência').update(
        eh_contrato=True, obrigatorio=True,
    )


def desmarcar_tipo_contrato(apps, schema_editor):
    TipoDocumento = apps.get_model('funcionarios', 'TipoDocumento')
    TipoDocumento.objects.filter(nome='Contrato de Experiência').update(eh_contrato=False)


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0019_funcionario_salario_setor_usa_contrato_qualidade'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='eh_contrato',
            field=models.BooleanField(default=False, help_text='Marque para o tipo "Contrato de Trabalho": ele é gerado pelo RH em vez de enviado pelo colaborador, e fica de fora da checagem inicial de documentos.'),
        ),
        migrations.RunPython(marcar_tipo_contrato, desmarcar_tipo_contrato),
    ]
