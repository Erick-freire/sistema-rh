from django.db.models.signals import post_save
from django.dispatch import receiver

from funcionarios.models import Funcionario

from . import services


@receiver(post_save, sender=Funcionario)
def enfileirar_sincronizacao_ponto(sender, instance, **kwargs):
    """
    Só grava estado local (SincronizacaoFuncionario pendente) — nunca chama a
    API da Control iD aqui. Isso garante que salvar um Funcionario nunca falha
    ou trava por causa de um equipamento offline/lento.
    """
    services.enfileirar_sincronizacao(instance)
