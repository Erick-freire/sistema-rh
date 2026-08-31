from django.db import migrations, models


def criar_tipo_atestado_medico(apps, schema_editor):
    TipoDocumento = apps.get_model('funcionarios', 'TipoDocumento')
    TipoDocumento.objects.get_or_create(
        nome='Atestado Médico',
        defaults={
            'obrigatorio': False,
            'eh_contrato': False,
            'condicao': 'sempre',
            'permite_varios': True,
        },
    )


def remover_tipo_atestado_medico(apps, schema_editor):
    TipoDocumento = apps.get_model('funcionarios', 'TipoDocumento')
    TipoDocumento.objects.filter(nome='Atestado Médico', documento__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0022_funcionario_status_onboarding'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='permite_varios',
            field=models.BooleanField(default=False, help_text='Marque para tipos que podem ser enviados mais de uma vez pelo mesmo funcionário (ex.: Atestado Médico): cada envio fica guardado no histórico em vez de substituir o anterior.'),
        ),
        migrations.RunPython(criar_tipo_atestado_medico, remover_tipo_atestado_medico),
    ]
