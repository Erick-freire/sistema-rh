"""
Resolve qual dos dois tipos de conta do sistema está por trás de um `User`.

São dois models paralelos, de propósito (ver `terceiros/models.py`):

- `funcionarios.Usuario` -- colaborador CLT da Sotrigo, com papel (RH, gestor,
  colaborador) e um `Funcionario` associado;
- `terceiros.ResponsavelTerceiro` -- responsável por uma empresa prestadora,
  sem `Funcionario` e fora do sistema de papéis.

Tudo que precisa valer para os dois (troca de senha provisória, para onde ir
depois do login) passa por aqui, em vez de cada ponto do código repetir os dois
`try/except`.
"""

from .models import Usuario

TIPO_COLABORADOR = 'colaborador'
TIPO_TERCEIRO = 'terceiro'


def usuario_de(user):
    """`Usuario` do colaborador CLT, ou None se a conta não for de um."""
    if not user.is_authenticated:
        return None
    try:
        return user.usuario
    except Usuario.DoesNotExist:
        return None


def responsavel_terceiro_de(user):
    """`ResponsavelTerceiro` da conta, ou None se não for de um terceiro."""
    if not user.is_authenticated:
        return None
    # Import local: `terceiros` importa `funcionarios` nos decorators, e o
    # import no topo fecharia o ciclo.
    from terceiros.models import ResponsavelTerceiro
    try:
        return user.responsavel_terceiro
    except ResponsavelTerceiro.DoesNotExist:
        return None


def perfil_de_acesso(user):
    """
    Devolve `(tipo, perfil)` -- `('colaborador', Usuario)`,
    `('terceiro', ResponsavelTerceiro)` ou `(None, None)` para um `User` sem
    nenhum dos dois perfis (ex.: superusuário criado pelo `createsuperuser`).
    """
    usuario = usuario_de(user)
    if usuario is not None:
        return TIPO_COLABORADOR, usuario

    responsavel = responsavel_terceiro_de(user)
    if responsavel is not None:
        return TIPO_TERCEIRO, responsavel

    return None, None
