import pymssql
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ponto.models import Escala, EscalaItem, Horario, HorarioDia


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


_CAMPOS_HORARIO = ('entrada1', 'saida1', 'entrada2', 'saida2', 'entrada3', 'saida3')


def _horario_esta_vazio(dias):
    """
    No Secullum, "folga" dentro de uma escala aponta pra um horário sem
    nenhum entrada/saída em nenhum dia (ex.: "FOLGA DIA"/"FOLGA NOITE"), não
    para um valor nulo. Detecta isso para tratar como folga de verdade (sem
    criar um Horario fantasma pra representar "nenhum horário").
    """
    return not any(dia[campo] for dia in dias for campo in _CAMPOS_HORARIO)


class Command(BaseCommand):
    help = (
        'Importa escalas (rotação trabalho/folga, ex.: 12x36) do Secullum Ponto 4 -- somente '
        'leitura lá. Importa automaticamente qualquer horário referenciado que ainda não exista '
        'no RH (ex.: os horários de "folga" usados entre os dias trabalhados).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not settings.SECULLUM_SERVER:
            raise CommandError(
                'Configure SECULLUM_SERVER/SECULLUM_INSTANCE/SECULLUM_DATABASE/SECULLUM_USER/'
                'SECULLUM_PASSWORD no .env antes de importar.'
            )

        dry_run = options['dry_run']
        servidor = settings.SECULLUM_SERVER
        if settings.SECULLUM_INSTANCE:
            servidor = f'{servidor}\\{settings.SECULLUM_INSTANCE}'

        conexao = pymssql.connect(
            server=servidor,
            user=settings.SECULLUM_USER,
            password=settings.SECULLUM_PASSWORD,
            database=settings.SECULLUM_DATABASE,
            timeout=30,
            login_timeout=10,
            as_dict=True,
        )
        try:
            cursor = conexao.cursor()

            cursor.execute("SELECT id, descricao, tamanho, data_base FROM escalas ORDER BY id")
            escalas_secullum = cursor.fetchall()

            cursor.execute("SELECT escala_id, ciclo, horario_num FROM escalas_itens ORDER BY escala_id, ciclo")
            itens_secullum = cursor.fetchall()

            numeros_referenciados = {i['horario_num'] for i in itens_secullum if i['horario_num']}
            numeros_existentes = set(
                Horario.objects.filter(numero__in=numeros_referenciados).values_list('numero', flat=True)
            )
            numeros_faltando = numeros_referenciados - numeros_existentes

            horarios_faltando = {}
            if numeros_faltando:
                cursor.execute(
                    "SELECT numero, nome, dia_semana, entrada1, saida1, entrada2, saida2, entrada3, saida3, "
                    "considerar_feriados, separar_normais_not FROM horarios "
                    "WHERE numero IN %s ORDER BY numero, dia_semana",
                    (tuple(numeros_faltando),),
                )
                for linha in cursor.fetchall():
                    horarios_faltando.setdefault(linha['numero'], []).append(linha)
        finally:
            conexao.close()

        if not escalas_secullum:
            self.stdout.write(self.style.WARNING('Nenhuma escala encontrada no Secullum.'))
            return

        itens_por_escala = {}
        for item in itens_secullum:
            itens_por_escala.setdefault(item['escala_id'], []).append(item)

        numeros_folga = {numero for numero, dias in horarios_faltando.items() if _horario_esta_vazio(dias)}

        escalas_criadas = 0
        escalas_atualizadas = 0
        horarios_importados = 0

        try:
            with transaction.atomic():
                for numero, dias in horarios_faltando.items():
                    if numero in numeros_folga:
                        continue  # marcador de folga -- não cria Horario pra isso

                    horarios_importados += 1
                    cabecalho = dias[0]
                    horario, _ = Horario.objects.update_or_create(
                        numero=numero,
                        defaults={
                            'nome': cabecalho['nome'] or '',
                            'considerar_feriados': bool(cabecalho['considerar_feriados']),
                            'separar_normais_noturnas': bool(cabecalho['separar_normais_not']),
                        },
                    )
                    for dia in dias:
                        HorarioDia.objects.update_or_create(
                            horario=horario,
                            dia_semana=dia['dia_semana'],
                            defaults={
                                'entrada1': dia['entrada1'] or None,
                                'saida1': dia['saida1'] or None,
                                'entrada2': dia['entrada2'] or None,
                                'saida2': dia['saida2'] or None,
                                'entrada3': dia['entrada3'] or None,
                                'saida3': dia['saida3'] or None,
                            },
                        )

                for escala_secullum in escalas_secullum:
                    escala, criada = Escala.objects.update_or_create(
                        numero=escala_secullum['id'],
                        defaults={
                            'descricao': (escala_secullum['descricao'] or '').strip(),
                            'tamanho_ciclo': escala_secullum['tamanho'],
                            'data_base': escala_secullum['data_base'].date(),
                        },
                    )
                    escalas_criadas += 1 if criada else 0
                    escalas_atualizadas += 0 if criada else 1

                    for item in itens_por_escala.get(escala_secullum['id'], []):
                        horario = None
                        if item['horario_num'] and item['horario_num'] not in numeros_folga:
                            horario = Horario.objects.filter(numero=item['horario_num']).first()
                        EscalaItem.objects.update_or_create(
                            escala=escala,
                            posicao=item['ciclo'],
                            defaults={'horario': horario},
                        )

                self.stdout.write(self.style.SUCCESS(
                    f'{len(escalas_secullum)} escala(s) processada(s) '
                    f'({escalas_criadas} criada(s), {escalas_atualizadas} atualizada(s)); '
                    f'{horarios_importados} horário(s) referenciado(s) importado(s) automaticamente '
                    f'({len(numeros_folga)} marcador(es) de folga reconhecido(s) e ignorado(s)).'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
