from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from funcionarios.models import Funcionario, Setor, Usuario

CARGO_PLACEHOLDER = 'Gestor (cadastro pendente)'

# Cada nome truncado que a planilha traz na coluna "Responsável do Setor" e a
# matrícula do funcionário REAL correspondente já cadastrado no sistema.
MAPA_PLACEHOLDER_PARA_REAL = {
    'ANALIA ANDRESSA': '533',
    'DENILSON ORALDO': '869',
    'JERUSA FERREIRA': '795',
    'LARISSA ARAUJO MOURA': '503',
    'MARCELO MOREIRA': '101',
    'NATHALIA JURGAC': '667',
    'ROMARIO RIBEIRO': '1336',
}


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Elimina os funcionários placeholder de gestor ("Gestor (cadastro pendente)"), '
        're-apontando todos os Setor que dependem deles para o funcionário REAL correspondente '
        '(casado por matrícula, nunca por nome). Só apaga um placeholder depois de confirmar que '
        'nenhum Setor aponta mais para ele e que ele não tem outros vínculos além do login próprio.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')
        parser.add_argument(
            '--promover-gestores',
            action='store_true',
            help="Também marca papel='gestor' no Usuario de quem ficar responsável por algum setor.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        promover = options['promover_gestores']

        setores_reapontados = 0
        syncs_descartadas = 0
        placeholders_apagados = []
        logins_apagados = []
        promovidos = []
        sem_alteracao = []
        erros = []

        try:
            with transaction.atomic():
                nomes = sorted({
                    f.nome.strip().upper()
                    for f in Funcionario.objects.filter(cargo=CARGO_PLACEHOLDER)
                })

                for nome in nomes:
                    registros = list(
                        Funcionario.objects
                        .filter(nome__iexact=nome, cargo=CARGO_PLACEHOLDER)
                        .order_by('pk')
                    )
                    if not registros:
                        continue

                    matricula_real = MAPA_PLACEHOLDER_PARA_REAL.get(nome)
                    if not matricula_real:
                        erros.append(
                            f'{nome}: sem matrícula real mapeada -- {len(registros)} placeholder(s) '
                            f'mantidos intactos (pks {[r.pk for r in registros]}).'
                        )
                        continue

                    real = Funcionario.objects.filter(matricula=matricula_real).first()
                    if real is None:
                        erros.append(
                            f'{nome}: funcionário real de matrícula {matricula_real} não existe -- '
                            f'placeholders mantidos intactos (pks {[r.pk for r in registros]}).'
                        )
                        continue
                    if real.cargo == CARGO_PLACEHOLDER:
                        erros.append(
                            f'{nome}: a matrícula {matricula_real} também é um placeholder -- '
                            f'mapeamento suspeito, nada alterado.'
                        )
                        continue

                    for placeholder in registros:
                        setores = list(Setor.objects.filter(gestor=placeholder).order_by('nome'))
                        for setor in setores:
                            setor.gestor = real
                            setor.save(update_fields=['gestor'])
                            setores_reapontados += 1
                            self.stdout.write(
                                f'  setor "{setor.nome}": {placeholder.nome} (pk {placeholder.pk}) '
                                f'-> {real.nome} (matrícula {real.matricula})'
                            )

                        restantes = Setor.objects.filter(gestor=placeholder).count()
                        if restantes:
                            erros.append(
                                f'{nome} (pk {placeholder.pk}): ainda restam {restantes} setor(es) '
                                f'apontando para o placeholder -- NÃO apagado, investigar manualmente.'
                            )
                            continue

                        descartadas = self._descartar_sync_nunca_enviada(placeholder)
                        if descartadas:
                            syncs_descartadas += descartadas
                            self.stdout.write(
                                f'  {placeholder.nome} (pk {placeholder.pk}): descartadas {descartadas} '
                                f'sincronização(ões) de catraca que nunca chegaram a ser enviadas'
                            )

                        pendencias = self._vinculos_bloqueantes(placeholder)
                        if pendencias:
                            erros.append(
                                f'{nome} (pk {placeholder.pk}): tem vínculos que seriam apagados em '
                                f'cascata ({", ".join(pendencias)}) -- NÃO apagado, investigar manualmente.'
                            )
                            continue

                        usuario = Usuario.objects.filter(funcionario=placeholder).select_related('user').first()
                        if usuario:
                            logins_apagados.append(
                                f'{usuario.user.username} (papel={usuario.papel}, '
                                f'último acesso={usuario.user.last_login or "nunca"})'
                            )
                            user_id = usuario.user_id
                            usuario.delete()
                            User.objects.filter(pk=user_id).delete()

                        placeholders_apagados.append(f'{placeholder.nome} (pk {placeholder.pk}, {placeholder.matricula})')
                        placeholder.delete()

                    if promover:
                        if Setor.objects.filter(gestor=real).exists():
                            usuario_real = Usuario.objects.filter(funcionario=real).first()
                            if usuario_real is None:
                                erros.append(f'{real.nome} (matrícula {real.matricula}): sem Usuario, não dá para promover a gestor.')
                            elif usuario_real.papel != 'gestor':
                                usuario_real.papel = 'gestor'
                                usuario_real.save(update_fields=['papel'])
                                promovidos.append(f'{real.nome} (matrícula {real.matricula})')
                            else:
                                sem_alteracao.append(f'{real.nome}: já era papel=gestor')

                self._relatar(
                    setores_reapontados, syncs_descartadas, placeholders_apagados,
                    logins_apagados, promovidos, sem_alteracao, erros, promover,
                )

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()

        except DryRunRollback:
            pass

    def _descartar_sync_nunca_enviada(self, funcionario):
        """Remove as filas de cadastro em catraca que nunca saíram do sistema.

        Um placeholder recém-criado entra na fila de cadastro de todo equipamento. Enquanto
        estiver 'pendente', sem id no dispositivo e sem nenhuma tentativa, nada foi enviado --
        descartar é seguro. Qualquer registro já sincronizado/tentado fica e bloqueia o delete,
        porque aí a pessoa fantasma existe no equipamento físico e precisa ser removida de lá.
        """
        from ponto.models import SincronizacaoFuncionario

        nunca_enviadas = SincronizacaoFuncionario.objects.filter(
            funcionario=funcionario,
            status='pendente',
            id_usuario_dispositivo='',
            tentativas=0,
        )
        quantidade = nunca_enviadas.count()
        if quantidade:
            nunca_enviadas.delete()
        return quantidade

    def _vinculos_bloqueantes(self, funcionario):
        """Relações que apontam para o funcionário e que NÃO devem ser perdidas.

        Setor já foi re-apontado antes desta checagem, e o Usuario do placeholder é
        descartado de propósito -- qualquer outra coisa impede o delete.
        """
        ignorar = {'funcionarios.Setor', 'funcionarios.Usuario'}
        achados = []
        for rel in Funcionario._meta.related_objects:
            if rel.related_model._meta.label in ignorar:
                continue
            quantidade = rel.related_model._default_manager.filter(**{rel.field.name: funcionario}).count()
            if quantidade:
                achados.append(f'{rel.related_model._meta.label}={quantidade}')
        return achados

    def _relatar(self, setores_reapontados, syncs_descartadas, placeholders_apagados,
                 logins_apagados, promovidos, sem_alteracao, erros, promover):
        escrever = self.stdout.write

        escrever('')
        escrever(self.style.MIGRATE_HEADING('=== RESUMO ==='))
        escrever(f'  Setores re-apontados para o funcionário real: {setores_reapontados}')
        escrever(f'  Sincronizações de catraca descartadas (nunca enviadas): {syncs_descartadas}')
        escrever(f'  Placeholders apagados: {len(placeholders_apagados)}')
        for item in placeholders_apagados:
            escrever(f'    - {item}')

        escrever(f'  Logins de placeholder removidos: {len(logins_apagados)}')
        for item in logins_apagados:
            escrever(f'    - {item}')

        if promover:
            escrever(f'  Promovidos a papel=gestor: {len(promovidos)}')
            for item in promovidos:
                escrever(f'    - {item}')
            for item in sem_alteracao:
                escrever(f'    - {item}')

        if erros:
            escrever('')
            escrever(self.style.ERROR(f'=== PENDÊNCIAS / ERROS ({len(erros)}) ==='))
            for item in erros:
                escrever(f'  - {item}')
        else:
            escrever('')
            escrever(self.style.SUCCESS('Nenhuma pendência.'))
        escrever('')
