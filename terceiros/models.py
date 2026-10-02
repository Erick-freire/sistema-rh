import os
import re

from django.contrib.auth.models import User
from django.db import models


def so_digitos(valor):
    return re.sub(r'\D', '', valor or '')


class EmpresaTerceira(models.Model):
    """
    Empresa prestadora de serviço à Sotrigo.

    O ciclo de vida segue o mesmo desenho do onboarding de CLT
    (`funcionarios.Funcionario.STATUS_CHOICES`): etapas sequenciais, cada
    transição disparada por uma ação concreta do RH ou do responsável, nunca por
    edição solta do campo.
    """

    STATUS_CHOICES = [
        ('pendente_cadastro', 'Aguardando cadastro do responsável'),
        ('aguardando_contrato', 'Cadastro completo, aguardando RH gerar contrato'),
        ('aguardando_assinatura', 'Contrato gerado, aguardando upload de documentos'),
        ('em_validacao', 'Documentos enviados, aguardando validação do RH'),
        ('ativo', 'Ativo'),
        ('inativo', 'Inativo'),
    ]

    razao_social = models.CharField(max_length=200)
    nome_fantasia = models.CharField(max_length=200, blank=True)
    cnpj = models.CharField(max_length=18, unique=True)
    logradouro = models.CharField(max_length=200)
    numero = models.CharField(max_length=20, blank=True)
    bairro = models.CharField(max_length=100)
    cidade = models.CharField(max_length=100)
    estado = models.CharField(max_length=2)
    cep = models.CharField(max_length=9)

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='pendente_cadastro')

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['razao_social']

    def __str__(self):
        return self.razao_social

    @property
    def nome_curto(self):
        return self.nome_fantasia or self.razao_social

    @property
    def iniciais(self):
        partes = self.nome_curto.split()
        if not partes:
            return ''
        if len(partes) == 1:
            return partes[0][:2].upper()
        return (partes[0][0] + partes[1][0]).upper()

    @property
    def cnpj_formatado(self):
        digitos = so_digitos(self.cnpj)
        if len(digitos) != 14:
            return self.cnpj
        return f'{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}'

    @property
    def cep_formatado(self):
        digitos = so_digitos(self.cep)
        if len(digitos) != 8:
            return self.cep
        return f'{digitos[:5]}-{digitos[5:]}'

    @property
    def endereco_completo(self):
        logradouro = self.logradouro
        if self.numero:
            logradouro = f'{logradouro}, n. {self.numero}'
        return f'{logradouro}, {self.bairro}, {self.cidade}/{self.estado}, CEP: {self.cep_formatado}'

    def contrato_atual(self):
        return self.contratos.order_by('-gerado_em').first()

    def documentos_todos_aprovados(self):
        """
        Pré-requisito para o RH ativar a empresa: pelo menos um colaborador
        cadastrado e nenhum documento pendente ou rejeitado -- equivalente a
        `Funcionario.documentos_todos_aprovados`.
        """
        colaboradores = list(self.colaboradores.all())
        if not colaboradores:
            return False
        return all(colaborador.documentos_todos_aprovados() for colaborador in colaboradores)

    def pode_enviar_para_validacao(self):
        """
        O sistema não sabe quantos colaboradores a prestadora vai alocar, então
        não há como inferir "terminou de enviar" como se faz no fluxo CLT (lá a
        lista de tipos obrigatórios é fechada). O responsável fecha a etapa
        explicitamente, e a regra mínima é: pelo menos um colaborador, todos com
        a documentação completa.
        """
        colaboradores = list(self.colaboradores.all())
        if not colaboradores:
            return False
        return all(colaborador.documentacao_completa() for colaborador in colaboradores)

    def competencias_mensais(self):
        """
        Histórico mensal da prestadora agrupado por competência, do mês mais
        recente para o mais antigo. Junta o que é da empresa (FGTS, DCTFWeb) com
        o holerite de cada colaborador, porque na tela isso é lido como um bloco
        só: "o que foi entregue no mês tal".

        Devolve uma lista de dicionários prontos para o template -- agrupar no
        template exigiria `regroup` sobre duas origens diferentes, o que não dá.
        """
        grupos = {}

        for documento in self.documentos_mensais.all():
            chave = (documento.competencia_ano, documento.competencia_mes)
            grupos.setdefault(chave, {'empresa': [], 'colaboradores': []})
            grupos[chave]['empresa'].append(documento)

        holerites = (
            DocumentoColaborador.objects
            .filter(colaborador__empresa=self, tipo='holerite')
            .select_related('colaborador')
        )
        for documento in holerites:
            chave = (documento.competencia_ano, documento.competencia_mes)
            grupos.setdefault(chave, {'empresa': [], 'colaboradores': []})
            grupos[chave]['colaboradores'].append(documento)

        return [
            {
                'ano': ano,
                'mes': mes,
                'rotulo': f'{NOME_DOS_MESES.get(mes, mes)}/{ano}',
                'documentos_empresa': conteudo['empresa'],
                'holerites': conteudo['colaboradores'],
            }
            for (ano, mes), conteudo in sorted(grupos.items(), reverse=True)
        ]


class ResponsavelTerceiro(models.Model):
    """
    Representa ao mesmo tempo o usuário do sistema da prestadora e os dados do
    representante legal exigidos pelo contrato.

    É um tipo de conta paralelo a `funcionarios.Usuario`, não um papel dele: o
    responsável não tem `Funcionario`, não aparece em relatórios de CLT e não
    usa o sistema de papéis. Quem resolve qual dos dois perfis está por trás de
    um `User` é `funcionarios/perfis.py`.
    """

    ESTADO_CIVIL_CHOICES = [
        ('solteiro', 'Solteiro(a)'),
        ('casado', 'Casado(a)'),
        ('divorciado', 'Divorciado(a)'),
        ('viuvo', 'Viúvo(a)'),
    ]

    CAMPOS_OBRIGATORIOS_CONTRATO = ['cpf', 'rg', 'estado_civil', 'profissao', 'endereco']

    empresa = models.OneToOneField(EmpresaTerceira, on_delete=models.CASCADE, related_name='responsavel')
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='responsavel_terceiro')

    nome = models.CharField(max_length=200)
    cpf = models.CharField(max_length=14, unique=True)
    # Os quatro campos abaixo são exigidos pelo contrato, mas quem preenche é o
    # próprio responsável no primeiro acesso (etapa 2 do fluxo) -- por isso
    # entram vazios no cadastro feito pelo RH.
    rg = models.CharField(max_length=20, blank=True)
    estado_civil = models.CharField(max_length=15, choices=ESTADO_CIVIL_CHOICES, blank=True)
    profissao = models.CharField(max_length=100, blank=True)
    endereco = models.CharField(max_length=300, blank=True)

    senha_provisoria = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.nome} ({self.empresa.razao_social})'

    @property
    def cpf_formatado(self):
        digitos = so_digitos(self.cpf)
        if len(digitos) != 11:
            return self.cpf
        return f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}'

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

    def dados_completos(self):
        return all(getattr(self, campo) for campo in self.CAMPOS_OBRIGATORIOS_CONTRATO)


class ClausulaContratual(models.Model):
    """
    Biblioteca de cláusulas reaproveitáveis entre contratos de prestação de
    serviço. O RH marca quais entram em cada contrato; o que é específico de um
    prestador vai para `ContratoTerceiro.clausulas_avulsas`.
    """

    MARCADORES = {
        '{{razao_social}}': 'Razão social da contratada',
        '{{nome_fantasia}}': 'Nome fantasia (cai na razão social se vazio)',
        '{{cnpj}}': 'CNPJ da contratada, formatado',
        '{{endereco_contratada}}': 'Endereço completo da contratada',
        '{{nome_prestador}}': 'Nome do representante legal da contratada',
        '{{cpf_prestador}}': 'CPF do representante legal, formatado',
        '{{prazo_meses}}': 'Prazo do contrato, em meses',
        '{{cidade}}': 'Cidade da contratada',
    }

    titulo = models.CharField(max_length=200)
    texto = models.TextField(
        help_text='Pode conter marcadores como {{nome_prestador}}, preenchidos automaticamente na geração. '
                  'Cada linha do texto vira um parágrafo no PDF.'
    )
    ordem_sugerida = models.PositiveIntegerField(default=0)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['ordem_sugerida', 'titulo']

    def __str__(self):
        return self.titulo

    def paragrafos_renderizados(self, valores_marcadores):
        """
        Troca os marcadores conhecidos e devolve os parágrafos do texto.

        A substituição é `str.replace` sobre uma lista fechada de marcadores, e
        não o engine de template, de propósito: o texto vem do banco e não pode
        virar template arbitrário.
        """
        texto = self.texto
        for marcador, valor in valores_marcadores.items():
            texto = texto.replace(marcador, str(valor))
        return [linha.strip() for linha in texto.splitlines() if linha.strip()]


class ContratoTerceiro(models.Model):
    empresa = models.ForeignKey(EmpresaTerceira, on_delete=models.PROTECT, related_name='contratos')

    # Os quatro campos abaixo são os que mudam de prestador para prestador e têm
    # lugar fixo no texto (Cláusulas 3ª/4ª, 5ª, 13ª e 14ª do modelo). Ficam
    # estruturados, e não como cláusula avulsa, para o RH ter um formulário
    # guiado em vez de um campo de texto livre.
    objeto_servico = models.TextField(help_text='Descrição do serviço prestado (Cláusula do Objeto).')
    composicao_equipe = models.TextField(help_text='Descrição da equipe disponibilizada (Cláusula da Equipe).')
    condicoes_remuneracao = models.TextField(help_text='Valor/hora, forma de pagamento, condições específicas.')
    prazo_meses = models.PositiveIntegerField(default=12)

    clausulas_biblioteca = models.ManyToManyField(ClausulaContratual, blank=True, related_name='contratos')
    clausulas_avulsas = models.TextField(
        blank=True,
        help_text='Cláusulas específicas digitadas na hora, não pertencentes à biblioteca. Uma por parágrafo.'
    )

    arquivo_gerado = models.FileField(upload_to='contratos_terceiros/', null=True, blank=True)
    gerado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='contratos_terceiros_gerados'
    )
    gerado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-gerado_em']

    def __str__(self):
        return f'Contrato — {self.empresa.razao_social} ({self.gerado_em.date()})'

    @property
    def nome_arquivo(self):
        return os.path.basename(self.arquivo_gerado.name) if self.arquivo_gerado else ''

    def paragrafos_avulsos(self):
        return [linha.strip() for linha in self.clausulas_avulsas.splitlines() if linha.strip()]


class ColaboradorTerceiro(models.Model):
    empresa = models.ForeignKey(EmpresaTerceira, on_delete=models.CASCADE, related_name='colaboradores')
    nome = models.CharField(max_length=200)
    cpf = models.CharField(max_length=14)
    funcao = models.CharField(max_length=100, blank=True)

    # Reservado para a futura integração com a catraca: "ao aprovar a selfie,
    # cadastrar o rosto e guardar aqui o id devolvido pela API". Nenhuma chamada
    # externa é feita hoje.
    #
    # É reconhecimento facial, para liberar o acesso -- e só. Digital é outra
    # coisa: vive em `ponto.SincronizacaoFuncionario` (status
    # `aguardando_biometria`) e existe só para CLT que bate ponto. Terceiro NÃO
    # bate ponto: nada aqui deve virar digital cadastrada,
    # `ponto.MarcacaoRelogio`, `ponto.FuncionarioJornada` ou horário de
    # trabalho -- esses modelos apontam para `funcionarios.Funcionario`, que o
    # colaborador terceiro não tem.
    id_catraca = models.CharField(max_length=50, blank=True, null=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'cpf'], name='colaborador_terceiro_cpf_unico_por_empresa'),
        ]

    def __str__(self):
        return f'{self.nome} ({self.empresa.razao_social})'

    @property
    def cpf_formatado(self):
        digitos = so_digitos(self.cpf)
        if len(digitos) != 11:
            return self.cpf
        return f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}'

    @property
    def iniciais(self):
        partes = self.nome.split()
        if not partes:
            return ''
        if len(partes) == 1:
            return partes[0][:2].upper()
        return (partes[0][0] + partes[-1][0]).upper()

    def tipos_pendentes(self):
        """
        Tipos obrigatórios sem nenhum envio válido -- nunca enviado ou rejeitado.

        Só entram aqui os documentos do onboarding (únicos + ordem de serviço). Os
        mensais (holerite) são rotina contínua depois da empresa ativa e nunca
        contam como pendência de cadastro.
        """
        enviados = set(self.documentos.exclude(status='rejeitado').values_list('tipo', flat=True))
        return [
            (tipo, rotulo)
            for tipo, rotulo in DocumentoColaborador.TIPO_CHOICES
            if tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING and tipo not in enviados
        ]

    def documentacao_completa(self):
        return not self.tipos_pendentes()

    def documentos_todos_aprovados(self):
        """
        Critério de onboarding: todo tipo único aprovado, mais pelo menos uma
        ordem de serviço aprovada (quantas existirem além dessa não importa).
        """
        aprovados = set(self.documentos.filter(status='aprovado').values_list('tipo', flat=True))
        return all(tipo in aprovados for tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING)

    def ordens_servico(self):
        """Histórico completo, do mais recente para o mais antigo."""
        return self.documentos.filter(tipo='ordem_servico').order_by('-enviado_em')

    def holerites(self):
        return self.documentos.filter(tipo='holerite').order_by('-competencia_ano', '-competencia_mes')

    def documentos_de_onboarding(self):
        return self.documentos.filter(
            tipo__in=DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING
        ).order_by('tipo', '-enviado_em')


STATUS_DOCUMENTO_CHOICES = [
    ('pendente', 'Pendente de análise'),
    ('aprovado', 'Aprovado'),
    ('rejeitado', 'Rejeitado'),
]

MESES = [
    (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
    (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
    (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
]

NOME_DOS_MESES = dict(MESES)


class DocumentoComCompetencia(models.Model):
    """
    Parte comum dos dois models de documento: status de análise, arquivo e a
    competência (mês/ano) dos documentos mensais.

    Existe como base abstrata porque RH aprova/reprova documento de colaborador e
    de empresa exatamente da mesma forma -- sem isso, as duas views de análise
    teriam que duplicar a mesma lógica.
    """

    arquivo = models.FileField(upload_to='terceiros_documentos/')

    competencia_mes = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=MESES,
        help_text='Preencher apenas para documentos mensais.',
    )
    competencia_ano = models.PositiveSmallIntegerField(null=True, blank=True)

    enviado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    enviado_em = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_DOCUMENTO_CHOICES, default='pendente')
    motivo_rejeicao = models.TextField(blank=True)
    analisado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    analisado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ['-enviado_em']

    @property
    def nome_arquivo(self):
        return os.path.basename(self.arquivo.name)

    @property
    def competencia_display(self):
        """"9/2026" vira "Setembro/2026" -- o número cru nunca vai para a tela."""
        if not self.competencia_mes or not self.competencia_ano:
            return ''
        return f'{NOME_DOS_MESES.get(self.competencia_mes, self.competencia_mes)}/{self.competencia_ano}'

    def devolver_para_analise(self, arquivo, enviado_por):
        """Reenvio: troca o arquivo e zera a análise anterior."""
        self.arquivo.delete(save=False)
        self.arquivo = arquivo
        self.enviado_por = enviado_por
        self.status = 'pendente'
        self.motivo_rejeicao = ''
        self.analisado_por = None
        self.analisado_em = None
        self.save()
        return self


class DocumentoColaborador(DocumentoComCompetencia):
    TIPO_CHOICES = [
        ('contrato_assinado', 'Contrato Assinado'),
        ('documento_pessoal', 'Documento Pessoal'),
        ('selfie', 'Selfie'),
        ('aso', 'ASO'),
        ('ordem_servico', 'Ordem de Serviço'),
        ('epi', 'Comprovante de EPI'),
        ('holerite', 'Holerite'),
    ]

    # Reenvio substitui o anterior -- um registro por tipo, por colaborador.
    TIPOS_UNICOS = ['contrato_assinado', 'documento_pessoal', 'selfie', 'aso', 'epi']
    # Vários envios sem competência, todo o histórico preservado.
    TIPOS_MULTIPLOS_LIVRES = ['ordem_servico']
    # Um registro por mês/ano; reenvio da mesma competência substitui.
    TIPOS_MENSAIS = ['holerite']

    # O que a validação inicial da empresa exige de cada colaborador. Os mensais
    # ficam de fora de propósito: são rotina contínua depois da empresa ativa.
    TIPOS_OBRIGATORIOS_ONBOARDING = TIPOS_UNICOS + TIPOS_MULTIPLOS_LIVRES

    colaborador = models.ForeignKey(
        ColaboradorTerceiro, on_delete=models.CASCADE, related_name='documentos'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    arquivo = models.FileField(upload_to='terceiros_documentos/colaboradores/')

    class Meta(DocumentoComCompetencia.Meta):
        abstract = False
        constraints = [
            # Sem `condition=Q(competencia_mes__isnull=False)`: o MySQL não
            # suporta índice único parcial e o Django descartaria a restrição
            # silenciosamente. Aqui ela não é necessária -- o MySQL trata NULLs
            # como distintos, então os tipos sem competência (únicos e ordem de
            # serviço) não são afetados, e só o holerite fica de fato limitado a
            # um registro por mês/ano.
            models.UniqueConstraint(
                fields=['colaborador', 'tipo', 'competencia_mes', 'competencia_ano'],
                name='unico_documento_colaborador_por_competencia',
            )
        ]

    def __str__(self):
        competencia = f' {self.competencia_display}' if self.competencia_mes else ''
        return f'{self.get_tipo_display()}{competencia} - {self.colaborador.nome}'

    @property
    def empresa(self):
        return self.colaborador.empresa

    @classmethod
    def registrar_envio(cls, colaborador, tipo, arquivo, enviado_por,
                        competencia_mes=None, competencia_ano=None):
        """
        Aplica o padrão de recorrência do tipo enviado:

        - único: substitui o registro existente;
        - múltiplo livre (ordem de serviço): sempre cria, preservando o histórico;
        - mensal: substitui dentro da mesma competência, cria em competência nova.
        """
        if tipo in cls.TIPOS_MULTIPLOS_LIVRES:
            return cls.objects.create(
                colaborador=colaborador, tipo=tipo, arquivo=arquivo, enviado_por=enviado_por,
            )

        if tipo in cls.TIPOS_MENSAIS:
            existente = cls.objects.filter(
                colaborador=colaborador, tipo=tipo,
                competencia_mes=competencia_mes, competencia_ano=competencia_ano,
            ).first()
            if existente is not None:
                return existente.devolver_para_analise(arquivo, enviado_por)
            return cls.objects.create(
                colaborador=colaborador, tipo=tipo, arquivo=arquivo, enviado_por=enviado_por,
                competencia_mes=competencia_mes, competencia_ano=competencia_ano,
            )

        existente = cls.objects.filter(colaborador=colaborador, tipo=tipo).first()
        if existente is not None:
            return existente.devolver_para_analise(arquivo, enviado_por)
        return cls.objects.create(
            colaborador=colaborador, tipo=tipo, arquivo=arquivo, enviado_por=enviado_por,
        )


class DocumentoEmpresaTerceira(DocumentoComCompetencia):
    """Obrigações mensais da prestadora -- sempre com competência."""

    TIPO_CHOICES = [
        ('fgts', 'Comprovante de FGTS'),
        ('dctfweb', 'DCTFWeb'),
    ]

    empresa = models.ForeignKey(
        EmpresaTerceira, on_delete=models.CASCADE, related_name='documentos_mensais'
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    arquivo = models.FileField(upload_to='terceiros_documentos/empresas/')

    # Aqui a competência é sempre obrigatória, ao contrário do documento de
    # colaborador, onde só o holerite usa.
    competencia_mes = models.PositiveSmallIntegerField(choices=MESES)
    competencia_ano = models.PositiveSmallIntegerField()

    class Meta(DocumentoComCompetencia.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'tipo', 'competencia_mes', 'competencia_ano'],
                name='unico_documento_empresa_por_competencia',
            )
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.competencia_display} - {self.empresa.razao_social}'

    @classmethod
    def registrar_envio(cls, empresa, tipo, arquivo, enviado_por, competencia_mes, competencia_ano):
        existente = cls.objects.filter(
            empresa=empresa, tipo=tipo,
            competencia_mes=competencia_mes, competencia_ano=competencia_ano,
        ).first()
        if existente is not None:
            return existente.devolver_para_analise(arquivo, enviado_por)
        return cls.objects.create(
            empresa=empresa, tipo=tipo, arquivo=arquivo, enviado_por=enviado_por,
            competencia_mes=competencia_mes, competencia_ano=competencia_ano,
        )
