import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Troca o `DocumentoColaboradorTerceiro` (3 tipos genéricos) pela modelagem que
    separa documento único, múltiplo livre e mensal com competência.

    É `RenameModel` e não delete/create de propósito: os três tipos antigos
    (contrato assinado, documento pessoal e selfie) continuam existindo na nova
    lista, então os envios já feitos seguem válidos e não precisam ser reenviados.
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('terceiros', '0002_clausulas_iniciais'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='DocumentoColaboradorTerceiro',
            new_name='DocumentoColaborador',
        ),
        migrations.AddField(
            model_name='documentocolaborador',
            name='competencia_mes',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                choices=[
                    (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
                    (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
                    (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
                ],
                help_text='Preencher apenas para documentos mensais.',
            ),
        ),
        migrations.AddField(
            model_name='documentocolaborador',
            name='competencia_ano',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='documentocolaborador',
            name='tipo',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('contrato_assinado', 'Contrato Assinado'),
                    ('documento_pessoal', 'Documento Pessoal'),
                    ('selfie', 'Selfie'),
                    ('aso', 'ASO'),
                    ('ordem_servico', 'Ordem de Serviço'),
                    ('epi', 'Comprovante de EPI'),
                    ('holerite', 'Holerite'),
                ],
            ),
        ),
        migrations.AlterField(
            model_name='documentocolaborador',
            name='arquivo',
            field=models.FileField(upload_to='terceiros_documentos/colaboradores/'),
        ),
        migrations.AlterField(
            model_name='documentocolaborador',
            name='analisado_por',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name='documentocolaborador',
            constraint=models.UniqueConstraint(
                fields=('colaborador', 'tipo', 'competencia_mes', 'competencia_ano'),
                name='unico_documento_colaborador_por_competencia',
            ),
        ),
        migrations.CreateModel(
            name='DocumentoEmpresaTerceira',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('arquivo', models.FileField(upload_to='terceiros_documentos/empresas/')),
                ('competencia_mes', models.PositiveSmallIntegerField(
                    choices=[
                        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
                        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
                        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
                    ],
                )),
                ('competencia_ano', models.PositiveSmallIntegerField()),
                ('enviado_em', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(
                    default='pendente', max_length=10,
                    choices=[
                        ('pendente', 'Pendente de análise'),
                        ('aprovado', 'Aprovado'),
                        ('rejeitado', 'Rejeitado'),
                    ],
                )),
                ('motivo_rejeicao', models.TextField(blank=True)),
                ('analisado_em', models.DateTimeField(blank=True, null=True)),
                ('tipo', models.CharField(
                    max_length=10,
                    choices=[('fgts', 'Comprovante de FGTS'), ('dctfweb', 'DCTFWeb')],
                )),
                ('analisado_por', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                )),
                ('enviado_por', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
                ('empresa', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='documentos_mensais', to='terceiros.empresaterceira',
                )),
            ],
            options={
                'ordering': ['-enviado_em'],
            },
        ),
        migrations.AddConstraint(
            model_name='documentoempresaterceira',
            constraint=models.UniqueConstraint(
                fields=('empresa', 'tipo', 'competencia_mes', 'competencia_ano'),
                name='unico_documento_empresa_por_competencia',
            ),
        ),
    ]
