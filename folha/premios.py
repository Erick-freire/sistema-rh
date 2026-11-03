"""
Aplica as regras de prêmios a quem está na folha: programa e safra pela lista
de cada regra (`RegraPremioPrograma`, `RegraPremioSafra`); incentivo pelas
`RegraPremio` de setor, cargo ou pessoa.

As regras são carregadas uma vez por competência e casadas em memória: num
fechamento de ~300 pessoas, uma consulta por pessoa e prêmio seria à toa.
"""
from collections import Counter, defaultdict

from django.db import transaction

from .models import (
    CAMPOS_DAS_REGRAS, MembroPremioPrograma, MembroPremioSafra, RegraPremio, RegraPremioPrograma, normalizar_cargo,
)


class RegrasDoMes:
    def __init__(self, competencia):
        self.competencia = competencia
        self.por_tipo = defaultdict(list)
        for regra in RegraPremio.objects.select_related('setor', 'funcionario'):
            if regra.vale_em(competencia):
                self.por_tipo[regra.tipo].append(regra)
        # O programa não tem vigência: vale a regra em que a pessoa está agora.
        self.programa = {
            membro.funcionario_id: membro.regra for membro in MembroPremioPrograma.objects.select_related('regra')
        }
        # O safra só nos meses da safra de cada regra.
        self.safra = {
            membro.funcionario_id: membro.regra
            for membro in MembroPremioSafra.objects.select_related('regra') if membro.regra.vale_em(competencia)
        }

    def regra_para(self, funcionario, tipo):
        """A regra que dá o prêmio à pessoa, ou None. Incentivo: a mais específica (pessoa > setor e cargo > setor)."""
        if tipo == 'premio_programa':
            return self.programa.get(funcionario.id)
        if tipo == 'premio_safra':
            return self.safra.get(funcionario.id)
        candidatas = [regra for regra in self.por_tipo[tipo] if regra.atinge(funcionario)]
        return max(candidatas, key=lambda regra: regra.especificidade, default=None)

    def valores_para(self, funcionario):
        """{campo: Decimal ou None}. None quando não há regra ou a regra é "não recebe" (zero)."""
        valores = {}
        for campo in CAMPOS_DAS_REGRAS:
            regra = self.regra_para(funcionario, campo)
            valores[campo] = regra.valor if regra and regra.valor else None
        return valores


def sugestoes_por_setor_e_cargo(fechamento_base, tipo):
    """
    Regras de setor e cargo que reproduzem `fechamento_base` para `tipo`: em
    cada grupo (setor, cargo), o valor que a maioria (mais da metade) recebeu,
    se não for zero. Quem fugiu disso (redução do mês, exceção) não vira regra:
    aparece na conferência para o RH decidir. Grupos que já têm regra ficam de fora.
    """
    grupos = defaultdict(list)
    for linha in fechamento_base.linhas.select_related('funcionario__setor'):
        funcionario = linha.funcionario
        grupos[(funcionario.setor, normalizar_cargo(funcionario.cargo))].append(linha)

    ja_existem = {
        (regra.setor_id, normalizar_cargo(regra.cargo))
        for regra in RegraPremio.objects.filter(tipo=tipo, funcionario=None)
    }
    sugestoes = []
    for (setor, cargo_normalizado), linhas in grupos.items():
        if (setor.id, cargo_normalizado) in ja_existem:
            continue
        valor, quantos = Counter(getattr(linha, tipo) or 0 for linha in linhas).most_common(1)[0]
        if valor and quantos * 2 > len(linhas):
            sugestoes.append({'setor': setor, 'cargo': linhas[0].funcionario.cargo.strip(), 'valor': valor})
    return sorted(sugestoes, key=lambda s: (s['setor'].nome, s['cargo']))


def diferencas(fechamento_base, tipo):
    """Quem recebeu em `fechamento_base` um valor diferente do que as regras dariam para aquele mês."""
    regras = RegrasDoMes(fechamento_base.competencia)
    resultado = []
    for linha in fechamento_base.linhas.select_related('funcionario__setor'):
        no_fechamento = getattr(linha, tipo) or 0
        regra = regras.regra_para(linha.funcionario, tipo)
        pela_regra = regra.valor if regra else 0
        if no_fechamento != pela_regra:
            resultado.append({'linha': linha, 'no_fechamento': no_fechamento, 'pela_regra': pela_regra, 'regra': regra})
    return resultado


# ---------- Regras de programa a partir de um fechamento ----------

def _mes_completo(linha):
    """Sem atestado, falta nem perda de assiduidade: o prêmio do mês veio cheio."""
    return not (linha.atestados_dias or linha.faltas_dias or linha.faltas_minutos) and linha.assiduidade


def _valores_cheios(linhas):
    """
    Os valores que são prêmio cheio num grupo do mesmo cargo: os recebidos
    num mês completo. Menos quando só uma pessoa recebeu aquele valor e
    vários colegas receberam mais num mês completo -- aí foi redução sem falta
    registrada (em set/2026: 157,50 contra os 450 de quatro colegas). Num
    empate de um contra um não dá para saber quem é a exceção: os dois valem.
    """
    quantos = Counter(linha.premio_programa for linha in linhas)
    completos = {linha.premio_programa for linha in linhas if _mes_completo(linha)}
    confirmados = {valor for valor in completos if quantos[valor] > 1}
    return {
        valor for valor in completos
        if quantos[valor] > 1 or not any(confirmado > valor for confirmado in confirmados)
    }


def planejar_regras_programa(fechamento_base):
    """
    As regras de prêmio programa que reproduzem `fechamento_base` (feito para
    a planilha de set/2026, pedido do usuário em 29/10/2026): uma regra por
    cargo e valor cheio, com as pessoas que recebem.

    Quem recebeu menos que o cheio teve a redução do mês (que é manual na
    grade): entra na regra do menor valor cheio acima do dele, olhando o mesmo
    cargo no mesmo setor ou, se o setor não tiver referência, o mesmo cargo
    em qualquer setor. Devolve um dict por pessoa:
    {'linha', 'cargo' (normalizado), 'valor', 'reduzido', 'confira'}, em que
    `confira` diz por que a escolha da regra cheia é incerta (ou é vazio).
    """
    linhas = [
        linha for linha in fechamento_base.linhas.select_related('funcionario__setor').order_by('nome')
        if linha.premio_programa
    ]
    por_grupo, por_cargo = defaultdict(list), defaultdict(list)
    for linha in linhas:
        cargo = normalizar_cargo(linha.funcionario.cargo)
        por_grupo[(cargo, linha.funcionario.setor_id)].append(linha)
        por_cargo[cargo].append(linha)

    plano = []
    for linha in linhas:
        cargo = normalizar_cargo(linha.funcionario.cargo)
        cheios = _valores_cheios(por_grupo[(cargo, linha.funcionario.setor_id)])
        de_outro_setor = not cheios
        if de_outro_setor:
            cheios = _valores_cheios(por_cargo[cargo])
        valor = linha.premio_programa
        acima = [cheio for cheio in cheios if cheio > valor]
        reduzido = valor not in cheios and bool(acima)
        confira = ''
        if reduzido and de_outro_setor:
            confira = 'o valor cheio veio do mesmo cargo em outro setor'
        elif reduzido and any(cheio < valor for cheio in cheios):
            confira = 'o valor recebido fica entre dois valores cheios do cargo'
        plano.append({
            'linha': linha,
            'cargo': cargo,
            'valor': min(acima) if reduzido else valor,
            'reduzido': reduzido,
            'confira': confira,
        })
    return plano


@transaction.atomic
def criar_regras_programa(plano, usuario=None):
    """
    Grava o `plano` de `planejar_regras_programa`. Usa a regra que já existir
    com o mesmo cargo e valor, e não mexe em quem já está em alguma regra.
    Devolve (regras criadas, quantas pessoas entraram, linhas de quem já estava numa regra).
    """
    existentes = {(normalizar_cargo(regra.cargo), regra.valor): regra for regra in RegraPremioPrograma.objects.all()}
    ja_em_regra = set(MembroPremioPrograma.objects.values_list('funcionario_id', flat=True))

    grafias = defaultdict(Counter)  # o nome da regra é o cargo como está no cadastro, na grafia mais comum
    for item in plano:
        grafias[item['cargo']][' '.join(item['linha'].funcionario.cargo.split())] += 1

    criadas, incluidas, puladas = [], 0, []
    for item in plano:
        funcionario = item['linha'].funcionario
        if funcionario.id in ja_em_regra:
            puladas.append(item['linha'])
            continue
        chave = (item['cargo'], item['valor'])
        if chave not in existentes:
            existentes[chave] = RegraPremioPrograma.objects.create(
                cargo=grafias[item['cargo']].most_common(1)[0][0], valor=item['valor'], atualizado_por=usuario,
            )
            criadas.append(existentes[chave])
        MembroPremioPrograma.objects.create(regra=existentes[chave], funcionario=funcionario, incluido_por=usuario)
        ja_em_regra.add(funcionario.id)
        incluidas += 1
    return criadas, incluidas, puladas
