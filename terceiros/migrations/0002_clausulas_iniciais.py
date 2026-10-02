"""
Popula a biblioteca inicial de cláusulas de contrato de prestação de serviços.

A estrutura (agrupamento e ordem) reproduz os grupos de cláusulas fixas do
modelo em papel: Declarações, Condições Gerais, Obrigações da Contratante,
Obrigações da Contratada, Garantia de Qualidade, Permissões/Uso de Imagem,
Comunicações, Rescisão, Penalidades, Ausência de Vínculo, Independência
Operacional, Exclusividade, Disposições Finais e Foro.

ATENÇÃO -- os TEXTOS precisam ser conferidos pelo jurídico contra o modelo em
papel antes do primeiro contrato gerado em produção. Foram redigidos a partir do
escopo de cada grupo, não copiados do documento original. Como a biblioteca é
editável pela tela de Cláusulas (RH), a conferência pode ser feita por lá, sem
nova migration.

Os textos não trazem número de cláusula escrito ("Cláusula Sexta:"): a numeração
é sequencial e aplicada na renderização do PDF, então um número fixo aqui
furaria a sequência assim que o RH desmarcasse alguma cláusula.

`ordem_sugerida` vai de 10 em 10 para caber inserção manual entre duas
cláusulas sem precisar renumerar as vizinhas.
"""

from django.db import migrations

CLAUSULAS = [
    (
        10,
        'Das Declarações das Partes',
        'A CONTRATADA declara, sob as penas da lei, que é empresa regularmente constituída e '
        'em situação regular perante os órgãos fiscais, previdenciários e trabalhistas, '
        'possuindo capacidade técnica, estrutura operacional e pessoal qualificado para a '
        'execução dos serviços objeto deste contrato.\n'
        'Parágrafo Único – A CONTRATADA declara ainda que tomou pleno conhecimento das '
        'condições, do local e da natureza dos serviços contratados, não podendo alegar '
        'desconhecimento ou dificuldade superveniente para escusar-se do cumprimento de '
        'suas obrigações.',
    ),
    (
        20,
        'Das Declarações da Contratada quanto a Encargos',
        'A CONTRATADA declara e assume, com exclusividade, a responsabilidade por todos os '
        'encargos trabalhistas, previdenciários, fiscais, tributários e securitários '
        'incidentes sobre a execução dos serviços e sobre o pessoal por ela alocado, '
        'inexistindo qualquer responsabilidade da CONTRATANTE a esse título.',
    ),
    (
        30,
        'Das Condições Gerais de Execução',
        'Os serviços serão executados pela CONTRATADA com autonomia técnica e '
        'organizacional, mediante seus próprios meios, equipamentos e métodos, observadas as '
        'normas internas, os padrões de qualidade e as regras de segurança do trabalho '
        'vigentes nas dependências da CONTRATANTE.\n'
        '§1º. A CONTRATADA deverá observar os horários e as rotinas de acesso das '
        'dependências da CONTRATANTE, submetendo previamente a relação do pessoal alocado, '
        'acompanhada da documentação exigida para liberação de acesso.\n'
        '§2º. A CONTRATANTE poderá recusar, de forma justificada, a permanência de qualquer '
        'integrante da equipe da CONTRATADA que descumpra normas internas, regras de '
        'segurança ou padrões de conduta, cabendo à CONTRATADA promover a substituição sem '
        'ônus adicional.',
    ),
    (
        40,
        'Das Condições Gerais quanto a Materiais e Equipamentos',
        'Correrão por conta exclusiva da CONTRATADA o fornecimento, a manutenção e a '
        'reposição dos equipamentos, ferramentas, insumos, uniformes e equipamentos de '
        'proteção individual necessários à execução dos serviços, salvo o que estiver '
        'expressamente ajustado em sentido diverso neste contrato.',
    ),
    (
        50,
        'Das Obrigações da Contratante',
        'Obriga-se a CONTRATANTE a permitir o acesso do pessoal previamente autorizado da '
        'CONTRATADA aos locais de execução dos serviços, a prestar as informações e '
        'esclarecimentos necessários à correta execução do objeto e a efetuar os pagamentos '
        'devidos nas condições e prazos ajustados.\n'
        'Parágrafo Único – A CONTRATANTE indicará preposto responsável pelo acompanhamento '
        'e pela fiscalização da execução dos serviços, sem que tal acompanhamento implique '
        'ingerência na organização do trabalho da CONTRATADA ou subordinação de seu pessoal.',
    ),
    (
        60,
        'Das Obrigações da Contratada',
        'Obriga-se a CONTRATADA a executar os serviços com zelo, diligência e observância '
        'das melhores práticas técnicas aplicáveis, empregando pessoal devidamente '
        'qualificado, treinado, registrado e submetido aos exames de saúde ocupacional '
        'exigidos pela legislação.\n'
        '§1º. A CONTRATADA responderá integralmente pelos danos que ela, seus empregados, '
        'prepostos ou subcontratados causarem à CONTRATANTE, a seus bens, a seus empregados '
        'ou a terceiros, em razão da execução dos serviços.\n'
        '§2º. A CONTRATADA obriga-se a apresentar, sempre que solicitada, a documentação '
        'comprobatória do recolhimento dos encargos trabalhistas e previdenciários '
        'relativos ao pessoal alocado na execução deste contrato.\n'
        '§3º. É vedada à CONTRATADA a subcontratação total ou parcial do objeto sem '
        'autorização prévia e escrita da CONTRATANTE.',
    ),
    (
        70,
        'Da Garantia de Qualidade dos Serviços',
        'A CONTRATADA garante a qualidade dos serviços prestados, obrigando-se a refazer, '
        'sem qualquer ônus adicional para a CONTRATANTE, os serviços executados em desacordo '
        'com as especificações ajustadas ou com os padrões técnicos aplicáveis.\n'
        'Parágrafo Único – A CONTRATADA deverá comunicar imediatamente à CONTRATANTE '
        'qualquer inconformidade, desvio de padrão ou risco à integridade e à segurança dos '
        'produtos, das instalações ou das pessoas que identifique no curso dos serviços.',
    ),
    (
        80,
        'Da Garantia quanto a Segurança Alimentar e Boas Práticas',
        'Em razão da atividade alimentícia desenvolvida pela CONTRATANTE, a CONTRATADA '
        'obriga-se a fazer com que todo o seu pessoal observe rigorosamente as boas práticas '
        'de fabricação, as regras de higiene pessoal e as normas de segurança alimentar '
        'vigentes nas dependências da CONTRATANTE, respondendo por qualquer prejuízo '
        'decorrente de sua inobservância.',
    ),
    (
        90,
        'Das Permissões e do Uso de Imagem',
        'A CONTRATADA e seu pessoal ficam expressamente proibidos de captar imagens, vídeos '
        'ou sons nas dependências da CONTRATANTE, bem como de divulgar em qualquer meio, '
        'inclusive em redes sociais, conteúdo obtido em razão deste contrato, salvo '
        'autorização prévia e escrita da CONTRATANTE.\n'
        'Parágrafo Único – A CONTRATADA autoriza a CONTRATANTE a registrar e utilizar, para '
        'fins exclusivos de controle de acesso, segurança patrimonial e cumprimento de '
        'obrigações legais, a imagem e os dados de identificação do pessoal por ela alocado, '
        'obrigando-se a colher de cada integrante da equipe a ciência correspondente.',
    ),
    (
        100,
        'Das Comunicações entre as Partes',
        'Todas as comunicações entre as partes relativas a este contrato serão feitas por '
        'escrito e dirigidas aos endereços e contatos indicados no preâmbulo, admitindo-se o '
        'correio eletrônico como meio válido de comunicação.\n'
        'Parágrafo Único – Qualquer alteração de endereço, telefone ou endereço eletrônico '
        'deverá ser comunicada à outra parte no prazo de 05 (cinco) dias, sob pena de se '
        'considerarem válidas as comunicações enviadas aos dados anteriormente informados.',
    ),
    (
        110,
        'Da Rescisão por Conveniência',
        'Este contrato poderá ser rescindido por qualquer das partes, sem ônus ou multa, '
        'mediante comunicação escrita com antecedência mínima de 30 (trinta) dias, '
        'permanecendo devidos os valores correspondentes aos serviços efetivamente prestados '
        'até a data do efetivo encerramento.',
    ),
    (
        120,
        'Da Rescisão por Justa Causa',
        'Este contrato poderá ser rescindido de imediato, independentemente de aviso prévio '
        'ou indenização, pela parte prejudicada, em caso de descumprimento de qualquer '
        'obrigação contratual, violação do dever de sigilo, prática de ato ilícito, '
        'decretação de falência ou recuperação judicial, ou inatividade injustificada da '
        'CONTRATADA na execução dos serviços.',
    ),
    (
        130,
        'Da Rescisão e seus Efeitos',
        'A rescisão, a qualquer título, não exime as partes das obrigações vencidas até a '
        'data de seu efeito, nem afasta a responsabilidade pelas obrigações de sigilo, que '
        'subsistem por prazo indeterminado, e pelos danos apurados.',
    ),
    (
        140,
        'Das Penalidades',
        'O descumprimento de qualquer obrigação prevista neste contrato sujeitará a parte '
        'infratora ao pagamento de multa correspondente a 10% (dez por cento) do valor '
        'total estimado do contrato, sem prejuízo da apuração e da recomposição integral das '
        'perdas e danos efetivamente suportados pela parte prejudicada.',
    ),
    (
        150,
        'Das Penalidades por Atraso',
        'O atraso no pagamento de valores devidos sujeitará a parte devedora à correção '
        'monetária pelo IPCA, a juros de mora de 1% (um por cento) ao mês, calculados pro '
        'rata die, e a multa de 2% (dois por cento) sobre o valor em atraso.',
    ),
    (
        160,
        'Da Ausência de Vínculo Empregatício',
        'Este contrato tem natureza exclusivamente civil, não gerando vínculo empregatício '
        'entre a CONTRATANTE e a CONTRATADA, seus sócios, empregados, prepostos ou '
        'subcontratados, ausentes os requisitos da relação de emprego previstos nos artigos '
        '2º e 3º da Consolidação das Leis do Trabalho.',
    ),
    (
        170,
        'Da Ausência de Vínculo e do Ressarcimento',
        'Na hipótese de a CONTRATANTE ser demandada, judicial ou administrativamente, por '
        'empregado, preposto ou subcontratado da CONTRATADA, esta obriga-se a assumir o polo '
        'passivo da demanda e a ressarcir a CONTRATANTE de todo valor que esta venha a '
        'despender a esse título, incluindo custas, honorários e despesas processuais, '
        'autorizada desde já a retenção dos valores correspondentes de créditos existentes.',
    ),
    (
        180,
        'Da Independência Operacional',
        'A CONTRATADA organiza livremente a execução dos serviços, dirigindo e fiscalizando '
        'seu próprio pessoal, definindo escalas, substituições e métodos de trabalho, sem '
        'qualquer subordinação hierárquica à CONTRATANTE, cuja atuação limita-se à '
        'fiscalização do resultado contratado.',
    ),
    (
        190,
        'Da Exclusividade',
        'Este contrato não estabelece exclusividade entre as partes, podendo a CONTRATADA '
        'prestar serviços a terceiros e a CONTRATANTE contratar serviços de mesma natureza '
        'de outros prestadores, ressalvado o dever de sigilo e a vedação de uso de '
        'informação confidencial em benefício de concorrentes.',
    ),
    (
        200,
        'Das Disposições Finais quanto a Alterações',
        'Qualquer alteração deste contrato somente terá validade se formalizada por escrito, '
        'mediante termo aditivo assinado por ambas as partes.',
    ),
    (
        210,
        'Das Disposições Finais quanto a Tolerância',
        'A tolerância de qualquer das partes quanto ao descumprimento de obrigação da outra '
        'constitui mera liberalidade, não implicando novação, renúncia a direito ou alteração '
        'do que foi contratado.',
    ),
    (
        220,
        'Das Disposições Finais quanto a Cessão',
        'É vedada a cessão ou transferência, total ou parcial, dos direitos e obrigações '
        'decorrentes deste contrato sem autorização prévia e escrita da outra parte.',
    ),
    (
        230,
        'Das Disposições Finais quanto a Nulidades',
        'A eventual nulidade ou ineficácia de qualquer disposição deste contrato não '
        'prejudica a validade das demais, que permanecem em pleno vigor, obrigando-se as '
        'partes a substituir a disposição afetada por outra de efeito equivalente.',
    ),
    (
        240,
        'Das Disposições Finais quanto ao Sigilo',
        'Integra este contrato, para todos os fins, o Termo de Compromisso de '
        'Confidencialidade firmado entre as partes, cujas obrigações a CONTRATADA declara '
        'conhecer e aceitar, respondendo por sua observância por parte de todo o pessoal que '
        'alocar na execução dos serviços.',
    ),
    (
        250,
        'Das Disposições Finais quanto à Sucessão',
        'Este contrato obriga as partes, seus herdeiros e sucessores a qualquer título.',
    ),
    (
        260,
        'Das Disposições Finais quanto ao Título Executivo',
        'As partes reconhecem que este contrato, firmado na presença de duas testemunhas, '
        'constitui título executivo extrajudicial, nos termos do artigo 784, inciso III, do '
        'Código de Processo Civil.',
    ),
    (
        270,
        'Do Foro',
        'As partes elegem o foro da Comarca de Anápolis, Estado de Goiás, para dirimir '
        'quaisquer controvérsias oriundas deste contrato, com renúncia expressa a qualquer '
        'outro, por mais privilegiado que seja.',
    ),
]


def criar_clausulas(apps, schema_editor):
    ClausulaContratual = apps.get_model('terceiros', 'ClausulaContratual')

    for ordem, titulo, texto in CLAUSULAS:
        # get_or_create pelo título: se a migration for reaplicada num banco que
        # já tem a biblioteca ajustada pelo RH, o texto revisado não é sobrescrito.
        ClausulaContratual.objects.get_or_create(
            titulo=titulo,
            defaults={'texto': texto, 'ordem_sugerida': ordem, 'ativa': True},
        )


def remover_clausulas(apps, schema_editor):
    ClausulaContratual = apps.get_model('terceiros', 'ClausulaContratual')
    titulos = [titulo for _, titulo, _ in CLAUSULAS]
    # Só remove as que não foram usadas em nenhum contrato -- apagar uma cláusula
    # já vinculada mudaria o histórico de um contrato gerado.
    ClausulaContratual.objects.filter(titulo__in=titulos, contratos__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('terceiros', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(criar_clausulas, remover_clausulas),
    ]
