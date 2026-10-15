"""
Testes da associação de batidas a turnos. Não usam banco.

Coloque em: ponto/test_turnos.py
Rode com:   python manage.py test ponto.test_turnos
"""
import unittest
from datetime import date, datetime, time, timedelta

from .turnos import agrupar_batidas_em_turnos, turno_previsto_do_dia


def dt(dia, hora, minuto=0):
    """Atalho: datetime em julho/2026 (dia, hora, minuto)."""
    return datetime(2026, 7, dia, hora, minuto)


def turno(dia, entrada, saida):
    return turno_previsto_do_dia(date(2026, 7, dia), [time(*entrada), time(*saida)])


class TurnoPrevistoDoDiaTests(unittest.TestCase):
    def test_turno_diurno_termina_no_mesmo_dia(self):
        t = turno_previsto_do_dia(date(2026, 7, 6), [time(8), time(12), time(13), time(17)])
        self.assertEqual(t.inicio, dt(6, 8))
        self.assertEqual(t.fim, dt(6, 17))

    def test_turno_noturno_termina_no_dia_seguinte(self):
        t = turno_previsto_do_dia(date(2026, 7, 6), [time(22), time(6)])
        self.assertEqual(t.inicio, dt(6, 22))
        self.assertEqual(t.fim, dt(7, 6))

    def test_noturno_com_intervalo_depois_da_meia_noite(self):
        t = turno_previsto_do_dia(date(2026, 7, 6), [time(22), time(2), time(3), time(6)])
        self.assertEqual(t.fim, dt(7, 6))

    def test_dia_sem_horarios_nao_tem_turno(self):
        self.assertIsNone(turno_previsto_do_dia(date(2026, 7, 6), []))


class AgruparBatidasTests(unittest.TestCase):
    def test_turno_diurno_recebe_as_quatro_batidas(self):
        previstos = [turno(6, (8, 0), (17, 0))]
        batidas = [dt(6, 8, 3), dt(6, 12), dt(6, 13), dt(6, 17, 5)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].momentos, batidas)

    def test_noturno_junta_entrada_e_saida_de_dias_diferentes(self):
        previstos = [turno_previsto_do_dia(date(2026, 7, 6), [time(22), time(6)])]
        batidas = [dt(6, 21, 55), dt(7, 6, 3)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].data, date(2026, 7, 6))
        self.assertEqual(r[0].momentos, batidas)

    def test_noites_seguidas_nao_misturam_saida_de_uma_com_entrada_da_outra(self):
        previstos = [
            turno_previsto_do_dia(date(2026, 7, 6), [time(22), time(6)]),
            turno_previsto_do_dia(date(2026, 7, 7), [time(22), time(6)]),
        ]
        batidas = [dt(6, 21, 58), dt(7, 6, 2), dt(7, 21, 59), dt(8, 6, 1)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(r[0].momentos, [dt(6, 21, 58), dt(7, 6, 2)])
        self.assertEqual(r[1].momentos, [dt(7, 21, 59), dt(8, 6, 1)])

    def test_dias_seguidos_diurnos_nao_se_misturam(self):
        previstos = [turno(6, (8, 0), (17, 0)), turno(7, (8, 0), (17, 0))]
        batidas = [dt(6, 8), dt(6, 17), dt(7, 7, 55), dt(7, 17, 2)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(r[0].momentos, [dt(6, 8), dt(6, 17)])
        self.assertEqual(r[1].momentos, [dt(7, 7, 55), dt(7, 17, 2)])

    def test_turno_sem_batida_continua_na_lista(self):
        previstos = [turno(6, (8, 0), (17, 0)), turno(7, (8, 0), (17, 0))]
        r = agrupar_batidas_em_turnos(previstos, [dt(7, 8), dt(7, 17)])
        self.assertEqual(r[0].momentos, [])
        self.assertIsNotNone(r[0].previsto)

    def test_marcacao_impar_fica_no_turno_certo(self):
        previstos = [turno(6, (8, 0), (17, 0)), turno(7, (8, 0), (17, 0))]
        batidas = [dt(6, 8), dt(6, 12), dt(6, 13), dt(7, 8), dt(7, 17)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r[0].momentos), 3)
        self.assertEqual(len(r[1].momentos), 2)

    def test_12x36_trabalho_em_dias_alternados(self):
        previstos = [
            turno_previsto_do_dia(date(2026, 7, 1), [time(6), time(18)]),
            turno_previsto_do_dia(date(2026, 7, 3), [time(6), time(18)]),
        ]
        batidas = [dt(1, 6), dt(1, 18, 2), dt(3, 5, 58), dt(3, 18)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual([len(x.momentos) for x in r], [2, 2])

    def test_12x36_dobra_na_folga_vira_turno_fora_de_escala(self):
        previstos = [
            turno_previsto_do_dia(date(2026, 7, 1), [time(6), time(18)]),
            turno_previsto_do_dia(date(2026, 7, 3), [time(6), time(18)]),
        ]
        batidas = [dt(1, 6), dt(1, 18), dt(2, 8), dt(2, 20), dt(3, 6), dt(3, 18)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r), 3)
        fora = [x for x in r if x.previsto is None]
        self.assertEqual(len(fora), 1)
        self.assertEqual(fora[0].data, date(2026, 7, 2))
        self.assertEqual(fora[0].momentos, [dt(2, 8), dt(2, 20)])

    def test_12x36_noturno_nao_vaza_para_o_dia_de_folga(self):
        previstos = [
            turno_previsto_do_dia(date(2026, 7, 1), [time(18), time(6)]),
            turno_previsto_do_dia(date(2026, 7, 3), [time(18), time(6)]),
        ]
        batidas = [dt(1, 17, 55), dt(2, 6, 4), dt(3, 17, 58), dt(4, 6, 1)]
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0].momentos, [dt(1, 17, 55), dt(2, 6, 4)])
        self.assertEqual(r[1].momentos, [dt(3, 17, 58), dt(4, 6, 1)])

    def test_trabalho_em_dia_de_folga_comum_vira_turno_fora_de_escala(self):
        previstos = [turno(6, (8, 0), (17, 0))]  # segunda
        batidas = [dt(11, 9), dt(11, 12)]         # sábado, sem escala
        r = agrupar_batidas_em_turnos(previstos, batidas)
        self.assertEqual(len(r), 2)
        self.assertIsNone(r[1].previsto)
        self.assertEqual(r[1].data, date(2026, 7, 11))

    def test_funcionario_sem_escala_agrupa_por_proximidade(self):
        batidas = [dt(6, 8), dt(6, 17), dt(7, 8), dt(7, 17)]
        r = agrupar_batidas_em_turnos([], batidas)
        self.assertEqual(len(r), 2)
        self.assertEqual([x.data for x in r], [date(2026, 7, 6), date(2026, 7, 7)])

    def test_dois_dias_de_folga_trabalhados_ficam_em_grupos_separados(self):
        batidas = [dt(11, 8), dt(11, 20), dt(13, 8), dt(13, 20)]  # turnos de 12h, com 36h entre eles
        r = agrupar_batidas_em_turnos([], batidas)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0].momentos, [dt(11, 8), dt(11, 20)])
        self.assertEqual(r[1].momentos, [dt(13, 8), dt(13, 20)])

    def test_hora_extra_dentro_da_margem_fica_no_turno(self):
        previstos = [turno(6, (8, 0), (17, 0))]
        r = agrupar_batidas_em_turnos(previstos, [dt(6, 8), dt(6, 20, 30)])
        self.assertEqual(len(r), 1)
        self.assertEqual(len(r[0].momentos), 2)

    def test_batida_alem_da_margem_maxima_fica_fora_do_turno(self):
        # Comportamento documentado: mais de 6h depois do fim do turno não encaixa.
        previstos = [turno(6, (8, 0), (17, 0))]
        r = agrupar_batidas_em_turnos(previstos, [dt(6, 8), dt(6, 23, 30)])
        self.assertEqual(len(r[0].momentos), 1)
        self.assertEqual(len(r), 2)
        self.assertIsNone(r[1].previsto)

    def test_aceita_objetos_de_batida_com_chave(self):
        class Batida:
            def __init__(self, momento, manual=False):
                self.momento, self.manual = momento, manual

        previstos = [turno_previsto_do_dia(date(2026, 7, 6), [time(22), time(6)])]
        batidas = [Batida(dt(6, 21, 58)), Batida(dt(7, 6, 2), manual=True), Batida(dt(9, 8))]
        r = agrupar_batidas_em_turnos(previstos, batidas, chave=lambda b: b.momento)
        self.assertEqual([b.momento for b in r[0].momentos], [dt(6, 21, 58), dt(7, 6, 2)])
        self.assertTrue(r[0].momentos[1].manual)
        self.assertIsNone(r[1].previsto)           # a batida de dia 9 sobrou e virou grupo fora de escala
        self.assertEqual(r[1].data, date(2026, 7, 9))

    def test_ordem_das_batidas_nao_importa(self):
        previstos = [turno(6, (8, 0), (17, 0))]
        r = agrupar_batidas_em_turnos(previstos, [dt(6, 17), dt(6, 8)])
        self.assertEqual(r[0].momentos, [dt(6, 8), dt(6, 17)])


if __name__ == '__main__':
    unittest.main()