import unicodedata
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from funcionarios.models import Funcionario, Setor
from funcionarios.utils import MESES

# De onde vem cada lançamento ao abrir uma competência nova (decisões do
# usuário em 26/10/2026):
# - copiados do mês anterior: só o que é fixo da pessoa (VT e periculosidade);
# - das regras de prêmios: programa (`RegraPremioPrograma`), incentivo
#   (`RegraPremio`) e safra (`RegraPremioSafra`);
# - do cálculo do ponto: horas, faltas, atestados e assiduidade;
# - em branco: o resto (bônus, descontos, reembolso, comissões, 13º), que o
#   RH lança mês a mês.
CAMPOS_COPIADOS = ['vt_seis_porcento', 'vt_valor', 'periculosidade']
CAMPOS_DAS_REGRAS = ['premio_programa', 'premio_incentivo', 'premio_safra']
CAMPOS_DO_PONTO = [
    'adicional_noturno_minutos', 'he_50_minutos', 'he_100_minutos', 'faltas_minutos',
    'faltas_dias', 'atestados_dias', 'assiduidade',
]


class Fechamento(models.Model):
    """
    Fechamento de folha de um mês (competência = mês fechado, do dia 1 ao
    último dia). É a planilha que vai para o escritório de contabilidade: uma
    `LinhaFechamento` por funcionário. Aberto, o RH edita e recalcula; fechado,
    fica travado como foi enviado.
    """
    STATUS_CHOICES = [
        ('aberto', 'Aberto'),
        ('fechado', 'Fechado'),
    ]

    competencia = models.DateField(unique=True, help_text='Sempre o dia 1 do mês.')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='aberto')
    observacao = models.CharField(max_length=255, blank=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    fechado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    fechado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-competencia']

    def __str__(self):
        return f'Fechamento {self.competencia_extenso}'

    @property
    def competencia_extenso(self):
        return f'{MESES[self.competencia.month - 1]}/{self.competencia.year}'

    @property
    def aberto(self):
        return self.status == 'aberto'

    def linhas_pendentes(self):
        return self.linhas.filter(ponto_pendente=True)


class LinhaFechamento(models.Model):
    """
    Um funcionário no fechamento. EMP, matrícula e nome são copiados do
    cadastro (atualizados a cada recálculo enquanto o fechamento está aberto),
    para o arquivo enviado não mudar depois se o cadastro mudar.

    Horas ficam em minutos; o arquivo da contabilidade usa horas.minutos
    (6h20 = 6.20) -- a conversão fica em `colunas.py`.
    """
    fechamento = models.ForeignKey(Fechamento, on_delete=models.CASCADE, related_name='linhas')
    funcionario = models.ForeignKey(Funcionario, on_delete=models.PROTECT, related_name='linhas_fechamento')

    codigo_empresa = models.PositiveIntegerField(null=True, blank=True)
    matricula = models.CharField(max_length=20, blank=True)
    nome = models.CharField(max_length=150)

    # Lançados pelo RH
    descontos = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # O que a coluna "13 SA0" significa ainda vai ser definido com o DP: por
    # enquanto é só a marcação SIM, sem regra nenhuma.
    decimo_terceiro = models.BooleanField(default=False)
    premio_programa = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    premio_bonus = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    premio_incentivo = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Percentual do salário (10 = 10%). Vai assim para a contabilidade, que aplica sobre o salário.
    premio_safra = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text='Percentual (10 = 10%).',
    )
    reembolso_despesas = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comissao = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    adiantamento_comissao = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Desconto do vale-transporte: o menor entre 6% do salário e o valor que a
    # empresa creditou. Ou um, ou outro -- ver `colunas.ler_vale_transporte`.
    vt_seis_porcento = models.BooleanField(default=False)
    vt_valor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    periculosidade = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text='Percentual (30 = 30%).',
    )

    # Vindos do cálculo do ponto (o RH pode corrigir antes de fechar)
    adicional_noturno_minutos = models.PositiveIntegerField(default=0)
    he_50_minutos = models.PositiveIntegerField(default=0)
    he_100_minutos = models.PositiveIntegerField(default=0)
    faltas_minutos = models.PositiveIntegerField(default=0)
    faltas_dias = models.PositiveSmallIntegerField(default=0)
    atestados_dias = models.PositiveSmallIntegerField(default=0)
    assiduidade = models.BooleanField(default=True)

    # O último resultado do cálculo do ponto: {'valores': {campo: valor}, ...}.
    # Campo cujo valor atual difere do calculado foi corrigido pelo RH, e o
    # recálculo não mexe nele (ver `services.aplicar_ponto`). Vazio = nunca
    # calculado.
    ponto_calculado = models.JSONField(default=dict, blank=True)
    # O mesmo para as regras de prêmios: {'valores': {campo: '300.00' ou None}}.
    premios_aplicados = models.JSONField(default=dict, blank=True)
    # O ponto de ~300 pessoas leva mais de 1 minuto, mais que o proxy de
    # produção espera por uma resposta. Por isso abrir/recalcular só marca as
    # linhas, e a tela calcula em lotes (ver `services.calcular_pendentes`).
    ponto_pendente = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['fechamento', 'funcionario'], name='um_funcionario_por_fechamento'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.fechamento.competencia_extenso})'

    def valores_automaticos(self, campo):
        """
        {campo: valor} do último preenchimento automático do grupo de `campo`
        (cálculo do ponto ou regras de prêmios); None se nunca foi preenchido.
        Linha importada da planilha tem {}: nada nela é automático.
        """
        if campo in CAMPOS_DO_PONTO:
            return self.ponto_calculado.get('valores')
        if campo in CAMPOS_DAS_REGRAS:
            return self.premios_aplicados.get('valores')
        return None

    def valor_automatico(self, campo):
        valor = self.valores_automaticos(campo)[campo]
        # Das regras, o JSON guarda texto ('300.00'); None = sem regra (em branco).
        return Decimal(valor) if campo in CAMPOS_DAS_REGRAS and valor is not None else valor

    def editado_pelo_rh(self, campo):
        """O valor atual difere do automático: o RH ajustou à mão, e o recálculo não mexe nele."""
        automaticos = self.valores_automaticos(campo)
        return bool(automaticos) and campo in automaticos and getattr(self, campo) != self.valor_automatico(campo)


class AlteracaoCelula(models.Model):
    """
    Histórico da grade da folha: quem mudou qual valor de uma linha, quando, e
    o que havia antes (decisão do usuário em 13/11/2026). Só o que alguém
    digita na grade -- o recálculo automático não entra. Os valores ficam como
    o texto exibido na célula ("1.234,56", "6%", "SIM"), igual ao que a pessoa viu.
    """

    linha = models.ForeignKey(LinhaFechamento, on_delete=models.CASCADE, related_name='alteracoes')
    campo = models.CharField(max_length=40)
    valor_anterior = models.CharField(max_length=60, blank=True)
    valor_novo = models.CharField(max_length=60, blank=True)
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+',
    )
    alterado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-alterado_em', '-id']
        verbose_name = 'alteração da folha'
        verbose_name_plural = 'alterações da folha'

    def __str__(self):
        return f'{self.linha.nome} · {self.campo}: {self.valor_anterior or "—"} → {self.valor_novo or "—"}'


def normalizar_cargo(cargo):
    """'Auxiliar de Produção' e 'AUXILIAR DE PRODUCAO ' são o mesmo cargo para as regras."""
    sem_acento = unicodedata.normalize('NFKD', cargo or '').encode('ascii', 'ignore').decode()
    return ' '.join(sem_acento.casefold().split())


class ComVigencia:
    """Regra que vale de um mês a outro (`vigente_de` e `vigente_ate`, sempre no dia 1; vazio = sem limite)."""

    @property
    def descricao_vigencia(self):
        def mes(data):
            return f'{data.month:02d}/{data.year}'
        if self.vigente_de and self.vigente_ate:
            return f'{mes(self.vigente_de)} a {mes(self.vigente_ate)}'
        if self.vigente_de:
            return f'desde {mes(self.vigente_de)}'
        if self.vigente_ate:
            return f'até {mes(self.vigente_ate)}'
        return 'sempre'

    def vale_em(self, competencia):
        return (self.vigente_de or date.min) <= competencia <= (self.vigente_ate or date.max)

    def _validar_vigencia(self):
        if self.vigente_de and self.vigente_ate and self.vigente_ate < self.vigente_de:
            raise ValidationError({'vigente_ate': 'O último mês não pode ser antes do primeiro.'})


class RegraPremio(ComVigencia, models.Model):
    """
    Quem recebe prêmio incentivo, e quanto. A regra vale para um setor
    inteiro, para um cargo de um setor, ou para uma pessoa; a mais específica
    vence (pessoa > setor e cargo > setor). Valor zero numa regra de pessoa é
    a exceção "não recebe". Programa e safra saíram daqui em 29/10/2026 para
    regras com a lista de quem recebe: ver `RegraPremioPrograma` e
    `RegraPremioSafra`.

    Ao abrir um fechamento, as regras vigentes na competência preenchem a
    coluna, e o RH ainda ajusta caso a caso na grade.
    """
    TIPO_CHOICES = [
        ('premio_incentivo', 'Prêmio incentivo'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    setor = models.ForeignKey(Setor, on_delete=models.CASCADE, null=True, blank=True, related_name='regras_premio')
    cargo = models.CharField(max_length=100, blank=True, help_text='Vazio = todos os cargos do setor.')
    funcionario = models.ForeignKey(
        Funcionario, on_delete=models.CASCADE, null=True, blank=True, related_name='regras_premio',
    )
    valor = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
        help_text='Em R$. Zero = não recebe.',
    )
    vigente_de = models.DateField(null=True, blank=True, help_text='Primeiro mês em que vale. Vazio = desde sempre.')
    vigente_ate = models.DateField(null=True, blank=True, help_text='Último mês em que vale. Vazio = sem fim.')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['tipo', 'setor__nome', 'cargo', 'funcionario__nome']

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.descricao_alvo} = {self.valor}'

    @property
    def especificidade(self):
        if self.funcionario_id:
            return 3
        return 2 if self.cargo else 1

    @property
    def descricao_alvo(self):
        if self.funcionario_id:
            return self.funcionario.nome
        return f'{self.setor.nome} · {self.cargo}' if self.cargo else f'{self.setor.nome} (todos os cargos)'

    def atinge(self, funcionario):
        if self.funcionario_id:
            return self.funcionario_id == funcionario.id
        if self.setor_id != funcionario.setor_id:
            return False
        return not self.cargo or normalizar_cargo(self.cargo) == normalizar_cargo(funcionario.cargo)

    def _mesmo_alvo(self, outra):
        return (
            self.tipo == outra.tipo and self.funcionario_id == outra.funcionario_id
            and self.setor_id == outra.setor_id and normalizar_cargo(self.cargo) == normalizar_cargo(outra.cargo)
        )

    def _vigencias_se_cruzam(self, outra):
        comeca = max(self.vigente_de or date.min, outra.vigente_de or date.min)
        termina = min(self.vigente_ate or date.max, outra.vigente_ate or date.max)
        return comeca <= termina

    def clean(self):
        if self.funcionario_id and (self.setor_id or self.cargo):
            raise ValidationError('A regra vale para uma pessoa ou para um setor, não para os dois.')
        if not self.funcionario_id and not self.setor_id:
            raise ValidationError('Escolha o setor ou a pessoa da regra.')
        self._validar_vigencia()
        # Duas regras com o mesmo alvo no mesmo mês: não daria para saber qual vale.
        for outra in RegraPremio.objects.filter(tipo=self.tipo).exclude(pk=self.pk).select_related('setor', 'funcionario'):
            if self._mesmo_alvo(outra) and self._vigencias_se_cruzam(outra):
                raise ValidationError(
                    f'Já existe uma regra para {outra.descricao_alvo} nesse período ({outra.descricao_vigencia}). '
                    'Edite a que existe ou ajuste as vigências.'
                )


class RegraPremioPrograma(models.Model):
    """
    Prêmio programa: um valor por cargo, com a lista de quem recebe (uma
    regra, muitos colaboradores). Pedido do usuário em 29/10/2026: a regra é
    "cargo X = R$ Y" e o RH coloca nela quem entra, inclusive quem for
    contratado depois. Não há casamento automático por setor ou cargo: só
    recebe quem está na lista, e cada pessoa fica em uma regra só.

    Um cargo pode ter mais de uma regra quando o valor muda de setor para
    setor (em set/2026, Operador de Máquina 2 recebia 900 no Moinho e 450 no
    Macarrão). As reduções do mês (atestado, faltas) continuam à mão na grade.
    """
    cargo = models.CharField(max_length=100)
    valor = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))], help_text='Em R$.',
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['cargo', 'valor']

    def __str__(self):
        return f'{self.cargo} = {self.valor}'

    def clean(self):
        self.cargo = ' '.join((self.cargo or '').split())
        # Duas regras iguais dividiriam o mesmo grupo de pessoas sem motivo.
        chave = normalizar_cargo(self.cargo)
        for outra in RegraPremioPrograma.objects.filter(valor=self.valor).exclude(pk=self.pk):
            if normalizar_cargo(outra.cargo) == chave:
                raise ValidationError(f'Já existe a regra "{outra.cargo}" com esse valor: coloque as pessoas nela.')


class MembroPremioPrograma(models.Model):
    regra = models.ForeignKey(RegraPremioPrograma, on_delete=models.CASCADE, related_name='membros')
    # Único: a pessoa recebe o programa de uma regra só. Mudou de cargo, muda de regra.
    funcionario = models.OneToOneField(Funcionario, on_delete=models.CASCADE, related_name='membro_premio_programa')
    incluido_em = models.DateTimeField(auto_now_add=True)
    incluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['funcionario__nome']

    def __str__(self):
        return f'{self.funcionario.nome} em {self.regra}'


class RegraPremioSafra(ComVigencia, models.Model):
    """
    Bônus safra: um percentual do salário (10%, confirmado pelo usuário em
    29/10/2026) nos meses da safra, com a lista de quem recebe. Como no
    programa, o RH coloca e tira as pessoas, e cada pessoa fica em uma regra só.

    A coluna vai para a contabilidade com o percentual (10), como no modelo de
    set/2026 ("PREMIO BONUS SAFRA (10%)"): o escritório aplica sobre o salário.
    """
    nome = models.CharField(max_length=100, help_text='Ex.: Safra 2026.')
    percentual = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('10'),
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('100'))],
        help_text='Do salário (10 = 10%).',
    )
    vigente_de = models.DateField(null=True, blank=True, help_text='Primeiro mês da safra. Vazio = desde sempre.')
    vigente_ate = models.DateField(null=True, blank=True, help_text='Último mês da safra. Vazio = sem fim.')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.percentual}%)'

    @property
    def valor(self):
        """O que vai para a coluna do fechamento: o percentual."""
        return self.percentual

    def clean(self):
        self.nome = ' '.join((self.nome or '').split())
        self._validar_vigencia()


class MembroPremioSafra(models.Model):
    regra = models.ForeignKey(RegraPremioSafra, on_delete=models.CASCADE, related_name='membros')
    # Único: a pessoa recebe o safra de uma regra só.
    funcionario = models.OneToOneField(Funcionario, on_delete=models.CASCADE, related_name='membro_premio_safra')
    incluido_em = models.DateTimeField(auto_now_add=True)
    incluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['funcionario__nome']

    def __str__(self):
        return f'{self.funcionario.nome} em {self.regra}'
