from django.core.management.base import BaseCommand
from django.urls import reverse

from funcionarios import notificacoes
from funcionarios.models import Usuario


class Command(BaseCommand):
    help = (
        'Manda uma notificação push para quem tem documento a enviar, contrato ou termo de '
        'comodato a assinar. Rodado todo dia pelo Agendador de Tarefas (scripts/lembrar_pendencias.bat).'
    )

    def handle(self, *args, **options):
        if not notificacoes.push_configurado():
            self.stdout.write('Push desligado (VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY vazias no .env). Nada a fazer.')
            return

        # Só quem tem aparelho inscrito: calcular pendência de quem não vai
        # receber é trabalho à toa.
        usuarios = (
            Usuario.objects
            .filter(user__is_active=True, user__inscricoes_push__isnull=False)
            .exclude(funcionario__status='inativo')
            .select_related('user', 'funcionario')
            .distinct()
        )

        avisados = 0
        for usuario in usuarios:
            itens = self._pendencias(usuario.funcionario)
            if not itens:
                continue
            notificacoes.notificar(
                [usuario.user],
                'Você tem pendências no RH',
                _juntar(itens).capitalize() + '.',
                reverse('meus_documentos'),
                tag='lembrete-pendencias',
            )
            avisados += 1

        self.stdout.write(f'{avisados} colaborador(es) avisado(s).')

    def _pendencias(self, funcionario):
        itens = []

        faltando = len(funcionario.tipos_documentos_nunca_enviados())
        recusados = funcionario.documentos.filter(status='rejeitado').count()
        if faltando:
            itens.append(f'{faltando} documento{"s" if faltando > 1 else ""} para enviar')
        if recusados:
            itens.append(f'{recusados} documento{"s" if recusados > 1 else ""} para reenviar')

        if funcionario.status == 'aguardando_assinatura':
            itens.append('o contrato de trabalho para assinar')

        comodatos = funcionario.comodatos.filter(status='aguardando_assinatura').count()
        if comodatos:
            itens.append(f'{comodatos} termo{"s" if comodatos > 1 else ""} de comodato para assinar')

        return itens


def _juntar(itens):
    if len(itens) == 1:
        return itens[0]
    return ', '.join(itens[:-1]) + ' e ' + itens[-1]
