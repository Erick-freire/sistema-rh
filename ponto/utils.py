from datetime import datetime, time, timedelta

from django.utils import timezone


def intervalo_do_dia(data):
    """
    (início, fim) aware do dia local, pra filtrar DateTimeField por range em
    vez de usar o lookup `__date` -- esse lookup usa CONVERT_TZ() no MySQL,
    que retorna NULL (e não bate nada) se as tabelas de fuso horário do
    servidor não estiverem carregadas (comum em instalações Windows).
    """
    inicio = timezone.make_aware(datetime.combine(data, time.min))
    return inicio, inicio + timedelta(days=1)
