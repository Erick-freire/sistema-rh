from django.conf import settings
from django.db import models

from funcionarios.models import Funcionario
from integracao_gov.fields import EncryptedTextField

from .turnos import DURACAO_MAXIMA_TURNO, duracao_do_turno


class Equipamento(models.Model):
    STATUS_CONEXAO_CHOICES = [
        ('desconhecido', 'Desconhecido'),
        ('online', 'Online'),
        ('offline', 'Offline'),
    ]

    nome = models.CharField(max_length=100)
    ip = models.GenericIPAddressField()
    porta = models.PositiveIntegerField(default=443)
    usa_https = models.BooleanField(
        default=True,
        help_text='Os equipamentos Control iD da Sotrigo servem a API em HTTPS (certificado autoassinado).',
    )
    modelo = models.CharField(max_length=100, blank=True)
    local = models.CharField(max_length=150, blank=True)
    numero_serie = models.CharField(max_length=50, blank=True)
    ativo = models.BooleanField(default=True)

    usuario_admin = models.CharField(max_length=100, default='admin')
    senha_admin = EncryptedTextField(
        blank=True,
        help_text='Senha de admin do equipamento. Guardada cifrada, nunca exibida.',
    )

    status_conexao = models.CharField(
        max_length=15,
        choices=STATUS_CONEXAO_CHOICES,
        default='desconhecido',
    )
    ultima_comunicacao = models.DateTimeField(null=True, blank=True)
    ultimo_erro_comunicacao = models.TextField(blank=True)

    ultimo_nsr_coletado = models.PositiveIntegerField(
        default=0,
        help_text='Checkpoint da coleta incremental de marcações: maior id de evento já gravado.',
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.ip}:{self.porta})'


class SincronizacaoFuncionario(models.Model):
    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('sincronizado', 'Sincronizado'),
        ('aguardando_biometria', 'Aguardando biometria'),
        ('erro', 'Erro'),
    ]

    ACAO_CHOICES = [
        ('cadastrar', 'Cadastrar/atualizar'),
        ('desativar', 'Desativar'),
    ]

    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='sincronizacoes_ponto',
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.CASCADE,
        related_name='sincronizacoes',
    )
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='pendente')
    acao_pendente = models.CharField(max_length=15, choices=ACAO_CHOICES, default='cadastrar')

    id_usuario_dispositivo = models.CharField(
        max_length=50, blank=True,
        help_text='CPF usado como chave do usuário no equipamento (a API Control iD REP identifica por CPF, não por um id próprio).',
    )
    hash_dados_sincronizados = models.CharField(max_length=64, blank=True)

    tentativas = models.PositiveIntegerField(default=0)
    ultimo_erro = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['funcionario', 'equipamento'],
                name='unico_funcionario_por_equipamento',
            )
        ]

    def __str__(self):
        return f'{self.funcionario.nome} @ {self.equipamento.nome} ({self.get_status_display()})'


class MarcacaoRelogio(models.Model):
    """
    Marcação bruta coletada de um equipamento. Imutável: nenhuma view deve
    expor edição ou exclusão. Correções entram como AjustePonto, em separado.
    """
    TIPO_CHOICES = [
        ('entrada', 'Entrada'),
        ('saida', 'Saída'),
        ('desconhecido', 'Desconhecido'),
    ]

    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.PROTECT,
        related_name='marcacoes',
    )
    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.PROTECT,
        related_name='marcacoes_ponto',
        null=True,
        blank=True,
        help_text='Nulo quando o identificador enviado pelo equipamento não bate com nenhum funcionário conhecido.',
    )
    id_evento_dispositivo = models.CharField(
        max_length=50,
        help_text='Identificador do evento no equipamento (NSR/id), usado para evitar duplicidade.',
    )
    momento = models.DateTimeField()
    tipo_evento = models.CharField(max_length=15, choices=TIPO_CHOICES, default='desconhecido')
    dados_brutos = models.JSONField(default=dict, blank=True)
    coletado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['equipamento', 'id_evento_dispositivo'],
                name='marcacao_unica_por_equipamento',
            )
        ]
        ordering = ['-momento']

    def __str__(self):
        nome = self.funcionario.nome if self.funcionario else 'desconhecido'
        return f'{nome} - {self.momento}'


class AjustePonto(models.Model):
    """
    Lançamento manual de uma marcação: uma batida que faltou (esqueceu de
    bater) ou a correção de uma batida que o relógio registrou errada. Nunca
    edita/apaga a MarcacaoRelogio original -- ao corrigir uma batida
    existente, `marcacao_original` aponta pra ela e o cálculo passa a usar só
    o horário lançado aqui no lugar dela; ao lançar uma batida que faltou por
    completo, `marcacao_original` fica em branco.

    Pode nascer de dois jeitos: o RH lança direto (fluxo original, já sai
    `aprovado`) ou o colaborador solicita e o RH aprova/rejeita depois. O
    motor de cálculo (`calculos.py`) só considera os `status='aprovado'` --
    pendente/rejeitado não pode afetar o espelho de ponto.
    """
    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('aprovado', 'Aprovado'),
        ('rejeitado', 'Rejeitado'),
    ]

    TIPO_EVENTO_CHOICES = [
        ('entrada', 'Entrada'),
        ('saida', 'Saída'),
    ]

    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.PROTECT,
        related_name='ajustes_ponto',
    )
    data = models.DateField()
    tipo_evento = models.CharField(max_length=10, choices=TIPO_EVENTO_CHOICES)
    horario_ajustado = models.TimeField()
    marcacao_original = models.ForeignKey(
        MarcacaoRelogio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ajustes',
        help_text='Preencha só ao corrigir uma batida que o relógio já registrou errada. Deixe em branco para lançar uma batida que faltou.',
    )
    motivo = models.TextField()
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pendente')
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ajustes_solicitados',
    )
    analisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ajustes_analisados',
    )
    analisado_em = models.DateTimeField(null=True, blank=True)
    observacao_rh = models.TextField(blank=True)

    class Meta:
        ordering = ['-data', '-criado_em']

    def __str__(self):
        return f'{self.funcionario.nome} - {self.data} {self.get_tipo_evento_display()} {self.horario_ajustado}'


class Horario(models.Model):
    """
    Jornada de trabalho: até 3 pares de entrada/saída por dia da semana.
    Modelo enxuto de propósito -- o `horarios` do Secullum tem quase 100
    colunas, mas na prática só ~15 variam de fato entre os horários reais da
    empresa; o resto é configuração fixa que não faz sentido replicar aqui.
    """
    numero = models.PositiveIntegerField(
        unique=True,
        help_text='Mantém a numeração do Secullum quando importado, para facilitar a conferência cruzada.',
    )
    nome = models.CharField(max_length=100, blank=True)
    considerar_feriados = models.BooleanField(default=False)
    separar_normais_noturnas = models.BooleanField(
        default=False,
        help_text='Separa horas normais trabalhadas em horário noturno para cálculo de adicional noturno.',
    )
    eh_12x36 = models.BooleanField(
        default=False,
        help_text=(
            'Rotação 12x36: quem trabalha e quem folga em cada dia é definido por funcionário '
            '(time par ou ímpar do mês), não pelo dia da semana -- os campos de entrada/saída '
            'abaixo valem só para os dias em que ele efetivamente trabalha.'
        ),
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['numero']

    def __str__(self):
        return f'{self.numero} - {self.nome}' if self.nome else str(self.numero)

    @property
    def tem_turno_longo_demais(self):
        return any(dia.turno_longo_demais for dia in self.dias.all())


class HorarioDia(models.Model):
    DIA_SEMANA_CHOICES = [
        (1, 'Segunda-feira'),
        (2, 'Terça-feira'),
        (3, 'Quarta-feira'),
        (4, 'Quinta-feira'),
        (5, 'Sexta-feira'),
        (6, 'Sábado'),
        (7, 'Domingo'),
    ]

    horario = models.ForeignKey(Horario, on_delete=models.CASCADE, related_name='dias')
    dia_semana = models.PositiveSmallIntegerField(choices=DIA_SEMANA_CHOICES)

    entrada1 = models.TimeField(null=True, blank=True)
    saida1 = models.TimeField(null=True, blank=True)
    entrada2 = models.TimeField(null=True, blank=True)
    saida2 = models.TimeField(null=True, blank=True)
    entrada3 = models.TimeField(null=True, blank=True)
    saida3 = models.TimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['horario', 'dia_semana'], name='um_registro_por_dia_semana'),
        ]
        ordering = ['dia_semana']

    def __str__(self):
        return f'{self.horario} - {self.get_dia_semana_display()}'

    @property
    def folga(self):
        return not any([self.entrada1, self.saida1, self.entrada2, self.saida2, self.entrada3, self.saida3])

    @property
    def turno_longo_demais(self):
        """Saída digitada antes da entrada (ver `turnos.DURACAO_MAXIMA_TURNO`): o cálculo esperaria um dia de 20h, 30h..."""
        horas = [self.entrada1, self.saida1, self.entrada2, self.saida2, self.entrada3, self.saida3]
        return duracao_do_turno([hora for hora in horas if hora]) > DURACAO_MAXIMA_TURNO


class Feriado(models.Model):
    data = models.DateField(unique=True)
    descricao = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['data']

    def __str__(self):
        return f'{self.data} - {self.descricao}' if self.descricao else str(self.data)


class FuncionarioJornada(models.Model):
    """
    Histórico de qual Horario vale para um funcionário a partir de uma data.
    Nunca editar/apagar um registro antigo ao trocar de turno -- cria um novo
    com `data_inicio` a partir de quando passa a valer, preservando o cálculo
    de períodos passados intacto.

    Quando `horario.eh_12x36`, `data_inicio` também serve como a data-base do
    ciclo: é sempre um dia de trabalho, e o ciclo alterna a cada dia a partir
    dela (ver `trabalha_no_dia_12x36`). Isso substituiu uma primeira versão
    baseada em "dia par/ímpar do mês", que quebrava na virada de mês em meses
    de 31 dias (dia 31 e dia 1 do mês seguinte caem os dois em ímpar) -- usar
    diferença de dias em vez do número do dia no calendário nunca tem esse
    problema. Pra mudar o dia de folga de alguém, basta atribuir uma nova
    jornada com `data_inicio` na data que deve passar a ser dia de trabalho.
    """
    funcionario = models.ForeignKey(Funcionario, on_delete=models.CASCADE, related_name='jornadas')
    data_inicio = models.DateField(
        help_text='A partir desta data este horário passa a valer. Se for 12x36, é sempre um dia de trabalho.',
    )
    horario = models.ForeignKey(Horario, on_delete=models.PROTECT, related_name='+')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['funcionario', '-data_inicio']
        constraints = [
            models.UniqueConstraint(fields=['funcionario', 'data_inicio'], name='uma_jornada_por_funcionario_por_data'),
        ]

    def __str__(self):
        return f'{self.funcionario.nome} -> {self.horario} (desde {self.data_inicio})'

    @classmethod
    def vigente_em(cls, funcionario, data):
        """A jornada com data_inicio mais recente que ainda seja <= data, ou None se nenhuma."""
        return cls.objects.filter(funcionario=funcionario, data_inicio__lte=data).order_by('-data_inicio').first()

    def trabalha_no_dia_12x36(self, data):
        """Só chamar quando `horario.eh_12x36` -- `data_inicio` é sempre um dia de trabalho, o ciclo alterna dia sim, dia não a partir dele."""
        return (data - self.data_inicio).days % 2 == 0
