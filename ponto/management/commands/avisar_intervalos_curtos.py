from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from funcionarios import notificacoes
from funcionarios.models import Funcionario
from ponto.calculos import calcular_periodo, intervalo_curto, intervalo_minimo


class Command(BaseCommand):
    help = (
        'Avisa o RH e o gestor da equipe, por notificação push, de cada colaborador que tirou menos '
        'intervalo que o mínimo no dia (padrão: ontem): 1h acima de 6h trabalhadas, 15 min acima de 4h '
        'até 6h. Rodado todo dia às 09:00 pelo Agendador '
        'de Tarefas (scripts/avisar_intervalos_curtos.bat) -- depois da coleta das 06:00, que já traz '
        'a saída dos turnos noturnos.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--data', help='Dia a verificar, no formato AAAA-MM-DD. Padrão: ontem.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Só lista os casos, sem mandar nenhum aviso.',
        )

    def handle(self, *args, **options):
        if options['data']:
            try:
                dia = date.fromisoformat(options['data'])
            except ValueError:
                raise CommandError('Data inválida. Use o formato AAAA-MM-DD.')
        else:
            dia = timezone.localdate() - timedelta(days=1)

        enviar = not options['dry_run']
        if enviar and not notificacoes.push_configurado():
            self.stdout.write('Push desligado (VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY vazias no .env). Nada a fazer.')
            return

        casos = 0
        for funcionario in Funcionario.objects.filter(status='ativo').select_related('setor').order_by('nome'):
            dia_calculado = calcular_periodo(funcionario, dia, dia)['dias'][0]
            curto = intervalo_curto(dia_calculado)
            if curto is None:
                continue
            saida, volta = curto
            minimo = intervalo_minimo(dia_calculado['segundos_trabalhados'])
            casos += 1
            minutos = int((volta.momento - saida.momento).total_seconds() // 60)
            self.stdout.write(
                f'{funcionario.nome}: {minutos} min '
                f'({timezone.localtime(saida.momento):%H:%M} às {timezone.localtime(volta.momento):%H:%M}), '
                f'mínimo {minimo // 60} min'
            )
            if enviar:
                # Espera cada envio terminar: o comando encerra logo depois, e o
                # envio em segundo plano seria cortado no meio.
                notificacoes.intervalo_curto(
                    funcionario, dia, saida.momento, volta.momento, minimo_segundos=minimo, em_segundo_plano=False,
                )

        acao = 'listado(s), nenhum aviso enviado (--dry-run)' if not enviar else 'avisado(s)'
        self.stdout.write(f'{dia:%d/%m/%Y}: {casos} caso(s) de intervalo abaixo do mínimo {acao}.')
