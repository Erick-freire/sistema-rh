"""
Aplica as regras de prêmios (`RegraPremio`) a quem está na folha.

As regras são carregadas uma vez por competência e casadas em memória: num
fechamento de ~300 pessoas, uma consulta por pessoa e prêmio seria à toa.
"""
from collections import Counter, defaultdict

from .models import CAMPOS_DAS_REGRAS, RegraPremio, normalizar_cargo


class RegrasDoMes:
    def __init__(self, competencia):
        self.competencia = competencia
        self.por_tipo = defaultdict(list)
        for regra in RegraPremio.objects.select_related('setor', 'funcionario'):
            if regra.vale_em(competencia):
                self.por_tipo[regra.tipo].append(regra)

    def regra_para(self, funcionario, tipo):
        """A regra mais específica que atinge a pessoa (pessoa > setor e cargo > setor), ou None."""
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
