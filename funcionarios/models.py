import os
from datetime import timedelta

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify


def proximo_login_gestor(nome, ocupados):
    """Login por nome do gestor: o primeiro nome, minúsculo e sem acento.

    Se já estiver em uso, numera: "daniel", "daniel2", "daniel3"...
    `ocupados` é o conjunto de logins que não podem ser repetidos.
    """
    primeiro_nome = slugify(((nome or '').split() or [''])[0]).replace('-', '') or 'gestor'
    login = primeiro_nome
    sufixo = 2
    while login in ocupados:
        login = f'{primeiro_nome}{sufixo}'
        sufixo += 1
    return login


class Setor(models.Model):
    nome = models.CharField(max_length=100)
    gestor = models.ForeignKey(
        'Funcionario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='setores_gerenciados'
    )
    usa_contrato_qualidade = models.BooleanField(
        default=False,
        help_text='Se marcado, o contrato de experiência gerado para funcionários deste setor '
                   'inclui a cláusula extra sobre participação em análise sensorial/degustação.'
    )

    def __str__(self):
        return self.nome
    
class Funcionario(models.Model):
    STATUS_CHOICES = [
        ('pendente_documentos', 'Pendente de documentos'),
        ('aguardando_contrato', 'Aguardando contrato'),
        ('aguardando_assinatura', 'Aguardando assinatura'),
        ('em_validacao', 'Em validação'),
        ('ativo', 'Ativo'),
        ('inativo', 'Inativo'),
    ]
    
    SEXO_CHOICES = [
        ('masculino', 'masculino'),
        ('feminino', 'feminino'),
        ('não-binário', 'não-binário')
    ]

    ESTADO_CIVIL_CHOICES = [
        ('solteiro', 'Solteiro(a)'),
        ('casado', 'Casado(a)'),
        ('uniao_estavel', 'União Estável'),
        ('divorciado', 'Divorciado(a)'),
        ('viuvo', 'Viúvo(a)'),
    ]

    CAMPOS_PESSOAIS_OBRIGATORIOS = [
        'cpf', 'pis', 'email', 'banco', 'agencia', 'numero_conta', 'sexo', 'estado_civil',
    ]

    nome = models.CharField(max_length=150)
    matricula = models.CharField(max_length=20, unique=True, null=True, blank=True)
    sexo = models.CharField(
        max_length=15,
        choices=SEXO_CHOICES,
        blank=True
    )
    estado_civil = models.CharField(
        max_length=15,
        choices=ESTADO_CIVIL_CHOICES,
        blank=True
    )
    tem_filho_menor_idade = models.BooleanField(
        default=False,
        help_text='Marque se o funcionário possui filho(a) menor de 18 anos.'
    )
    cargo = models.CharField(max_length=100)
    salario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Usado para preencher o contrato de experiência gerado em PDF.'
    )
    data_admissao = models.DateField(null=True, blank=True)
    data_base_ferias = models.DateField(
        null=True,
        blank=True,
        help_text='Preencha apenas se o ciclo de férias não seguir a data de admissão '
                   '(ex.: reinício de contagem após afastamento prolongado). '
                   'Quando vazio, o ciclo é calculado a partir da data de admissão.'
    )
    setor = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        related_name='funcionarios'
    )
    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default='ativo'
    )
    cpf = models.CharField(
        max_length=11,
        unique=True,
        help_text='Usado como login do colaborador.'
    )
    pis = models.CharField(
        max_length=11,
        unique=True,
        null=True,
        blank=True,
        help_text='Usado pra bater ponto em relógios que identificam por PIS em vez de CPF (ex.: relógio ADM).',
    )
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='Usado para envio do holerite.'
    )
    banco = models.CharField(max_length=100, blank=True)
    agencia = models.CharField(max_length=10, blank=True)
    numero_conta = models.CharField(max_length=20, blank=True)
    foto = models.ImageField(upload_to='fotos_perfil/', blank=True, null=True)

    def __str__(self):
        return f'{self.nome} ({self.matricula})'

    @property
    def eh_gestor(self):
        return self.setores_gerenciados.exists()

    def aviso_ferias_pendente_acao(self):
        return (
            self.avisos_ferias
            .filter(visualizado=True, solicitacoes_geradas__isnull=True)
            .order_by('criado_em')
            .first()
        )

    @property
    def iniciais(self):
        partes = self.nome.split()
        if not partes:
            return ''
        if len(partes) == 1:
            return partes[0][:2].upper()
        return (partes[0][0] + partes[-1][0]).upper()

    @property
    def primeiro_nome(self):
        partes = self.nome.split()
        return partes[0] if partes else self.nome

    def dados_pessoais_completos(self):
        return all(getattr(self, campo) for campo in self.CAMPOS_PESSOAIS_OBRIGATORIOS)

    def dados_contrato_completos(self):
        return bool(self.cargo and self.salario is not None and self.endereco)

    def tipos_documentos_obrigatorios(self):
        return [
            tipo for tipo in TipoDocumento.objects.filter(obrigatorio=True)
            if tipo.aplica_a(self)
        ]

    def tipos_documentos_iniciais(self):
        return [
            tipo for tipo in TipoDocumento.objects.filter(obrigatorio=True, eh_contrato=False)
            if tipo.aplica_a(self)
        ]

    def tipos_documentos_opcionais(self):
        return [
            tipo for tipo in TipoDocumento.objects.filter(obrigatorio=False, eh_contrato=False)
            if tipo.aplica_a(self)
        ]

    def tipos_documentos_nunca_enviados(self):
        """
        Tipos obrigatórios sem nenhum envio ainda (nem sequer rejeitado) --
        usado nas telas pra listar "Faltam N documentos" separado dos que já
        foram enviados uma vez e só precisam de reenvio (esses aparecem em
        "Enviados", com o badge de rejeitado).
        """
        tipos_com_documento = set(self.documentos.values_list('tipo_documento_id', flat=True))
        return [tipo for tipo in self.tipos_documentos_iniciais() if tipo.id not in tipos_com_documento]

    def documentos_iniciais_completos(self):
        tipos_enviados = set(
            self.documentos.exclude(status='rejeitado').values_list('tipo_documento_id', flat=True)
        )
        return all(tipo.id in tipos_enviados for tipo in self.tipos_documentos_iniciais())

    def todos_documentos_enviados(self):
        tipos_enviados = set(
            self.documentos.exclude(status='rejeitado').values_list('tipo_documento_id', flat=True)
        )
        return all(tipo.id in tipos_enviados for tipo in self.tipos_documentos_obrigatorios())

    def documentos_todos_aprovados(self):
        tipos_aprovados = set(
            self.documentos.filter(status='aprovado').values_list('tipo_documento_id', flat=True)
        )
        return all(tipo.id in tipos_aprovados for tipo in self.tipos_documentos_obrigatorios())

    def _somar_anos(self, data, anos):
        try:
            return data.replace(year=data.year + anos)
        except ValueError:
            return data.replace(year=data.year + anos, day=28)

    def _periodo_ferias_atual(self):
        if self.data_admissao is None:
            return None

        ancora = self.data_base_ferias or self.data_admissao
        hoje = timezone.now().date()
        solicitacoes = list(
            self.solicitacoes_ferias.filter(status='aprovada')
            .order_by('data_solicitada')
            .values_list('data_solicitada', 'dias_solicitados', 'dias_abono')
        )
        indice = 0
        n = 0

        while True:
            aquisitivo_inicio = ancora if n == 0 else self._somar_anos(ancora, n)
            aquisitivo_fim = self._somar_anos(ancora, n + 1) - timedelta(days=1)
            concessivo_fim = self._somar_anos(aquisitivo_fim, 1)

            dias_gozados = 0
            while indice < len(solicitacoes) and solicitacoes[indice][0] <= concessivo_fim:
                dias_gozados += solicitacoes[indice][1] + solicitacoes[indice][2]
                indice += 1
                if dias_gozados >= 30:
                    break

            if dias_gozados >= 30:
                n += 1
                continue

            return {
                'aquisitivo_inicio': aquisitivo_inicio,
                'aquisitivo_fim': aquisitivo_fim,
                'concessivo_fim': concessivo_fim,
                'dias_gozados': dias_gozados,
                'dias_pendentes': max(0, 30 - dias_gozados),
                'vencida': hoje > concessivo_fim,
            }

    def proxima_data_vencimento(self):
        periodo = self._periodo_ferias_atual()
        return periodo['concessivo_fim'] if periodo else None

    def pode_solicitar_ferias(self):
        if self.data_admissao is None:
            return False
        hoje = timezone.now().date()
        um_ano_depois = self._somar_anos(self.data_admissao, 1)
        return hoje >= um_ano_depois

    def dias_ate_direito_ferias(self):
        if self.data_admissao is None:
            return None
        hoje = timezone.now().date()
        um_ano_depois = self._somar_anos(self.data_admissao, 1)
        if hoje >= um_ano_depois:
            return 0
        return (um_ano_depois - hoje).days

    def dias_para_vencer_ferias(self):
        periodo = self._periodo_ferias_atual()
        if periodo is None:
            return None
        return (periodo['concessivo_fim'] - timezone.now().date()).days

    def ferias_vencidas(self):
        periodo = self._periodo_ferias_atual()
        return periodo is not None and periodo['vencida']

    def dias_pendentes_ferias(self):
        periodo = self._periodo_ferias_atual()
        return periodo['dias_pendentes'] if periodo else None
    
class Usuario(models.Model):
    PAPEL_CHOICES = [
        ('colaborador', 'Colaborador'),
        ('gestor', 'Gestor'),
        ('dp', 'Departamento Pessoal'),
        ('rh', 'Recursos Humanos'),
        ('seguranca_trabalho', 'Segurança do Trabalho'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )
    funcionario = models.OneToOneField(
        Funcionario,
        on_delete=models.CASCADE
    )
    papel = models.CharField(
        max_length=25,
        choices=PAPEL_CHOICES
    )
    acesso_rh = models.BooleanField(
        default=False,
        help_text='Concede o acesso completo de RH além do papel principal, incluindo '
                   'os módulos exclusivos de RH (que o DP não vê).'
    )
    senha_provisoria = models.BooleanField(default=False)
    # Só gestor entra pelo primeiro nome (os demais, pelo CPF -- que o gestor
    # também continua podendo usar). Preenchido/limpo sozinho no save().
    login_gestor = models.CharField(
        max_length=40, unique=True, null=True, blank=True,
        help_text='Login pelo primeiro nome, só enquanto o papel for Gestor.',
    )

    def __str__(self):
        return f'{self.user.username} ({self.papel})'

    def save(self, *args, **kwargs):
        if self.papel != 'gestor':
            self.login_gestor = None
        elif not self.login_gestor:
            ocupados = set(
                Usuario.objects.exclude(pk=self.pk).exclude(login_gestor=None)
                .values_list('login_gestor', flat=True)
            )
            self.login_gestor = proximo_login_gestor(self.funcionario.nome, ocupados)
        else:
            self.login_gestor = self.login_gestor.strip().lower()
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = {*kwargs['update_fields'], 'login_gestor'}
        super().save(*args, **kwargs)

    def eh_rh(self):
        """RH de fato: vê tudo, inclusive os módulos exclusivos de RH."""
        return self.papel == 'rh' or self.acesso_rh

    def eh_dp_ou_rh(self):
        """Quem opera os módulos do dia a dia (funcionários, férias, ponto, comodatos...)."""
        return self.papel == 'dp' or self.eh_rh()

    def eh_seguranca_ou_rh(self):
        """Quem opera o módulo de EPI. O DP não entra: não é rotina dele."""
        return self.papel == 'seguranca_trabalho' or self.eh_rh()
    
class TipoDocumento(models.Model):
    CONDICAO_CHOICES = [
        ('sempre', 'Sempre obrigatório'),
        ('sexo_masculino', 'Apenas para funcionários do sexo masculino'),
        ('sexo_feminino', 'Apenas para funcionários do sexo feminino'),
        ('casado', 'Apenas para funcionários casados(as)'),
        ('filho_menor_idade', 'Apenas para funcionários com filho(a) menor de idade'),
    ]

    nome = models.CharField(max_length=100)
    obrigatorio = models.BooleanField(default=False)
    eh_contrato = models.BooleanField(
        default=False,
        help_text='Marque para o tipo "Contrato de Trabalho": ele é gerado pelo RH em vez de '
                   'enviado pelo colaborador, e fica de fora da checagem inicial de documentos.'
    )
    condicao = models.CharField(
        max_length=20,
        choices=CONDICAO_CHOICES,
        default='sempre',
        help_text='Define quando este documento é exigido, além do campo "obrigatório" estar marcado.'
    )
    permite_varios = models.BooleanField(
        default=False,
        help_text='Marque para tipos que podem ser enviados mais de uma vez pelo mesmo funcionário '
                   '(ex.: Atestado Médico): cada envio fica guardado no histórico em vez de substituir o anterior.'
    )

    def __str__(self):
        return self.nome

    def aplica_a(self, funcionario):
        if self.condicao == 'sexo_masculino':
            return funcionario.sexo == 'masculino'
        if self.condicao == 'sexo_feminino':
            return funcionario.sexo == 'feminino'
        if self.condicao == 'casado':
            return funcionario.estado_civil == 'casado'
        if self.condicao == 'filho_menor_idade':
            return funcionario.tem_filho_menor_idade
        return True


class Documento(models.Model):
    STATUS_CHOICES = [
        ('pendente', 'Pendente de análise'),
        ('aprovado', 'Aprovado'),
        ('rejeitado', 'Rejeitado'),
    ]

    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='documentos'
    )
    tipo_documento = models.ForeignKey(
        TipoDocumento,
        on_delete=models.PROTECT
    )
    arquivo = models.FileField(upload_to='documentos/%Y/%m/')
    data_upload = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pendente')
    motivo_rejeicao = models.TextField(blank=True)
    analisado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documentos_analisados'
    )
    analisado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.tipo_documento} - {self.funcionario}'

    @property
    def nome_arquivo(self):
        return os.path.basename(self.arquivo.name)

    @classmethod
    def substituir_ou_criar(cls, funcionario, tipo_documento, arquivo, enviado_por):
        documento = None
        if not tipo_documento.permite_varios:
            documento = cls.objects.filter(funcionario=funcionario, tipo_documento=tipo_documento).first()
        if documento is None:
            return cls.objects.create(
                funcionario=funcionario,
                tipo_documento=tipo_documento,
                arquivo=arquivo,
                enviado_por=enviado_por,
            )

        documento.arquivo.delete(save=False)
        documento.arquivo = arquivo
        documento.enviado_por = enviado_por
        documento.data_upload = timezone.now()
        documento.status = 'pendente'
        documento.motivo_rejeicao = ''
        documento.analisado_por = None
        documento.analisado_em = None
        documento.save()
        return documento


class SolicitacaoFerias(models.Model):
    STATUS_CHOICES = [
        ('pendente_gestor', 'Aguardando Gestor'),
        ('sugestao_gestor', 'Gestor sugeriu alteração'),
        ('pendente_rh', 'Aguardando RH'),
        ('aprovada', 'Aprovada'),
        ('adiada', 'Adiada pelo colaborador'),
    ]

    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='solicitacoes_ferias'
    )
    data_solicitada = models.DateField()
    dias_solicitados = models.PositiveIntegerField(default=30)
    dias_abono = models.PositiveIntegerField(
        default=0,
        help_text='Dias convertidos em abono pecuniário nesta solicitação.'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pendente_gestor')

    data_sugerida = models.DateField(null=True, blank=True)
    dias_sugeridos = models.PositiveIntegerField(null=True, blank=True)
    observacao_gestor = models.TextField(blank=True)

    analisado_por_gestor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_analisadas_gestor'
    )
    concluido_por_rh = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_concluidas_rh'
    )
    aviso_origem = models.ForeignKey(
        'AvisoFerias', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitacoes_geradas',
        help_text='Aviso de vencimento (se algum) que originou esta solicitação.'
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.funcionario.nome} - {self.data_solicitada} ({self.get_status_display()})'

    @property
    def data_fim(self):
        return self.data_solicitada + timedelta(days=self.dias_solicitados - 1)


class Atestado(models.Model):
    """
    Período de afastamento por atestado médico. Usado pelo espelho de ponto
    (`ponto.calculos`) pra mostrar "Atestado" em vez de falta/folga nesses
    dias e não descontar horas -- ver `Funcionario.atestados`.
    """
    funcionario = models.ForeignKey(Funcionario, on_delete=models.CASCADE, related_name='atestados')
    data_inicio = models.DateField()
    data_fim = models.DateField()
    motivo = models.CharField(max_length=200, blank=True, help_text='CID ou motivo, se informado no atestado.')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_inicio']

    def __str__(self):
        return f'{self.funcionario.nome} ({self.data_inicio:%d/%m/%Y} a {self.data_fim:%d/%m/%Y})'

class AvisoFerias(models.Model):
    funcionario = models.ForeignKey(
        Funcionario, on_delete=models.CASCADE, related_name='avisos_ferias'
    )
    data_prevista = models.DateField()
    mensagem = models.TextField(blank=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    visualizado = models.BooleanField(default=False)
    visualizado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.funcionario.nome} - {self.data_prevista}'

    @property
    def foi_atendido(self):
        return self.solicitacoes_geradas.exists()
    
class TermoComodato(models.Model):
    MODALIDADE_CHOICES = [
        ('uniforme', 'Uniforme'),
        ('equipamento', 'Equipamento / Aparelho'),
    ]

    STATUS_CHOICES = [
        ('aguardando_assinatura', 'Aguardando assinatura'),
        ('em_validacao', 'Assinado, aguardando validação do RH'),
        ('ativo', 'Ativo (item(ns) emprestado(s))'),
        ('devolvido_total', 'Devolvido integralmente'),
        ('devolvido_parcial', 'Devolvido parcialmente'),
        ('extraviado', 'Extraviado / Danificado'),
    ]

    funcionario = models.ForeignKey(Funcionario, on_delete=models.PROTECT, related_name='comodatos')
    modalidade = models.CharField(max_length=15, choices=MODALIDADE_CHOICES)

    data_emprestimo = models.DateField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='aguardando_assinatura')

    data_devolucao = models.DateField(null=True, blank=True)
    observacoes_devolucao = models.TextField(blank=True)
    arquivo_gerado = models.FileField(upload_to='comodatos/', null=True, blank=True)
    arquivo_assinado = models.FileField(upload_to='comodatos/assinados/', null=True, blank=True)

    validado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='comodatos_validados'
    )
    validado_em = models.DateTimeField(null=True, blank=True)
    motivo_rejeicao = models.TextField(blank=True)

    gerado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='comodatos_gerados')
    gerado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Comodato ({self.get_modalidade_display()}) — {self.funcionario.nome}'
    
class ItemCatalogoComodato(models.Model):
    nome = models.CharField(max_length=100)
    modalidade = models.CharField(max_length=15, choices=TermoComodato.MODALIDADE_CHOICES)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome

class ItemComodato(models.Model):
    termo = models.ForeignKey(TermoComodato, on_delete=models.CASCADE, related_name='itens')

    # Opcional no banco só para não quebrar itens antigos; o form exige.
    item_catalogo = models.ForeignKey(
        ItemCatalogoComodato, on_delete=models.PROTECT, null=True, blank=True, related_name='itens',
    )
    # Só em equipamento: identifica qual aparelho foi entregue (nº de série / IMEI).
    numero_serie = models.CharField(max_length=50, blank=True)
    descricao = models.CharField(max_length=200, blank=True)
    tamanho = models.CharField(max_length=10, blank=True)
    quantidade = models.PositiveIntegerField(default=1)
    valor_unitario = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    devolvido = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.descricao} ({self.quantidade}x)'

# ---------- EPI (Segurança do Trabalho) ----------

class ItemCatalogoEPI(models.Model):
    nome = models.CharField(max_length=100)
    numero_ca = models.CharField(
        max_length=20, help_text='Número do Certificado de Aprovação (CA), exigido pela NR-6.'
    )
    validade_ca = models.DateField(
        null=True, blank=True, help_text='Data de validade do CA deste modelo de EPI.'
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} (CA {self.numero_ca})'


class FichaEPI(models.Model):
    # Mesmo ciclo e mesmos nomes de campo do TermoComodato.
    STATUS_CHOICES = [
        ('aguardando_assinatura', 'Aguardando assinatura'),
        ('em_validacao', 'Assinado, aguardando validação'),
        ('ativo', 'Ativo (EPI(s) em uso)'),
        ('devolvido_total', 'Devolvido integralmente'),
        ('devolvido_parcial', 'Devolvido parcialmente'),
    ]

    funcionario = models.ForeignKey(Funcionario, on_delete=models.PROTECT, related_name='fichas_epi')
    data_entrega = models.DateField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='aguardando_assinatura')

    data_devolucao = models.DateField(null=True, blank=True)
    observacoes_devolucao = models.TextField(blank=True)

    arquivo_gerado = models.FileField(upload_to='epi/', null=True, blank=True)
    arquivo_assinado = models.FileField(upload_to='epi/assinados/', null=True, blank=True)

    gerado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='fichas_epi_geradas')
    gerado_em = models.DateTimeField(auto_now_add=True)
    validado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='fichas_epi_validadas'
    )
    validado_em = models.DateTimeField(null=True, blank=True)
    motivo_rejeicao = models.TextField(blank=True)

    def __str__(self):
        return f'Ficha EPI — {self.funcionario.nome}'

    @classmethod
    def com_devolucao_pendente(cls):
        """Fichas de colaboradores desligados (inativos) que ainda têm EPI a devolver."""
        return cls.objects.filter(funcionario__status='inativo', itens__devolvido=False).distinct()

    def registrar_devolucao(self, itens_devolvidos, observacoes=''):
        """Marca os itens como devolvidos e fecha a ficha como devolvida total ou parcialmente."""
        self.itens.filter(id__in=[item.id for item in itens_devolvidos]).update(devolvido=True)
        self.data_devolucao = timezone.now().date()
        self.observacoes_devolucao = observacoes
        self.status = 'devolvido_parcial' if self.itens.filter(devolvido=False).exists() else 'devolvido_total'
        self.save()


class ItemEPI(models.Model):
    ficha = models.ForeignKey(FichaEPI, on_delete=models.CASCADE, related_name='itens')
    item_catalogo = models.ForeignKey(ItemCatalogoEPI, on_delete=models.PROTECT, related_name='fichas')
    quantidade = models.PositiveIntegerField(default=1)
    data_vencimento = models.DateField(
        null=True, blank=True,
        help_text='Data em que este EPI deve ser revisado/trocado. Opcional — deixe em branco se não aplicável.'
    )
    devolvido = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.item_catalogo.nome} ({self.quantidade}x)'


class OcorrenciaEPI(models.Model):
    funcionario = models.ForeignKey(Funcionario, on_delete=models.PROTECT, related_name='ocorrencias_epi')
    tecnico = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='ocorrencias_epi_registradas'
    )
    data_ocorrencia = models.DateField()
    descricao = models.TextField(help_text='Ex: "Encontrado sem óculos de proteção na linha de produção."')
    item_catalogo = models.ForeignKey(
        ItemCatalogoEPI, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Qual EPI estava em falta ou sendo usado incorretamente, se aplicável.'
    )
    foto = models.ImageField(upload_to='epi/ocorrencias/', null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Ocorrência — {self.funcionario.nome} ({self.data_ocorrencia})'


class MatrizEpiSetor(models.Model):
    """EPIs obrigatórios de um setor: pré-preenchem a ficha de entrega de quem é desse setor."""
    setor = models.ForeignKey(Setor, on_delete=models.CASCADE, related_name='matriz_epi')
    item_catalogo = models.ForeignKey(ItemCatalogoEPI, on_delete=models.PROTECT, related_name='matriz_setores')
    quantidade_padrao = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['setor', 'item_catalogo'], name='unico_item_por_setor_na_matriz')
        ]
        ordering = ['setor__nome', 'item_catalogo__nome']

    def __str__(self):
        return f'{self.setor.nome} — {self.item_catalogo.nome}'


class SolicitacaoSenha(models.Model):
    """Pedido de nova senha feito na tela de login ("Recuperar acesso").

    Não troca senha nenhuma: só avisa o RH/DP, que gera a nova senha provisória
    pela ficha do funcionário. Gerar a senha fecha os pedidos em aberto dele.
    Um pedido em aberto por usuário -- conferido no código, já que o MySQL
    ignora UniqueConstraint condicional.
    """
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='solicitacoes_senha')
    criada_em = models.DateTimeField(auto_now_add=True)
    atendida_em = models.DateTimeField(null=True, blank=True)
    atendida_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-criada_em']

    def __str__(self):
        return f'Nova senha — {self.usuario.funcionario.nome} ({self.criada_em:%d/%m/%Y})'

    @classmethod
    def pendentes(cls):
        return cls.objects.filter(atendida_em__isnull=True)


class InscricaoPush(models.Model):
    """Um aparelho (navegador/PWA) inscrito para receber notificações push.

    O mesmo endpoint é sempre um aparelho só: se outra pessoa entra no
    aparelho compartilhado, a inscrição passa para ela (ver pwa_views) e o
    logout desfaz o vínculo, para ninguém receber aviso do outro.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='inscricoes_push')
    endpoint = models.CharField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    navegador = models.CharField(max_length=300, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Push — {self.user.username} ({self.navegador[:40]})'
