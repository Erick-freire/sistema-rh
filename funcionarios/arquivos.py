"""
Arquivos enviados pelos usuários: o que o sistema aceita e como devolve.

O tipo do arquivo sai sempre do conteúdo (os primeiros bytes), nunca do nome.
Um HTML renomeado para "rg.pdf" continua sendo HTML; se ele voltasse ao
navegador como página, no mesmo domínio do sistema, o script dentro dele
rodaria com a sessão de quem abriu -- por exemplo, o RH validando documentos
de uma prestadora.

Duas pontas, de propósito independentes:

- `validar_upload`, na entrada: só PDF, JPG e PNG de verdade, até 10 MB;
- `resposta_de_arquivo` / `blindar_resposta`, na saída: PDF e imagem abrem no
  navegador com o tipo certo; qualquer outra coisa vira download e não
  executa nada. Isso cobre os arquivos que entraram antes da validação e
  qualquer caminho de upload que um dia esqueça de chamar `validar_upload`.
"""
import os

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, Http404
from django.utils.cache import get_conditional_response
from django.utils.http import content_disposition_header, http_date

TAMANHO_MAXIMO_MB = 10
TAMANHO_MAXIMO = TAMANHO_MAXIMO_MB * 1024 * 1024

PDF = 'application/pdf'
JPEG = 'image/jpeg'
PNG = 'image/png'

# extensão aceita -> o tipo que o conteúdo precisa ter
EXTENSOES_DOCUMENTO = {'pdf': PDF, 'jpg': JPEG, 'jpeg': JPEG, 'png': PNG}
EXTENSOES_IMAGEM = {'jpg': JPEG, 'jpeg': JPEG, 'png': PNG}

# `accept` do <input type="file">: o seletor do celular já mostra só o que
# serve. Tipos e extensões juntos porque cada navegador respeita um deles.
ACCEPT_DOCUMENTO = 'application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png'
ACCEPT_IMAGEM = 'image/jpeg,image/png,.jpg,.jpeg,.png'

BYTES_INSPECIONADOS = 1024


def tipo_pelo_conteudo(inicio):
    """Content-Type real a partir dos primeiros bytes; None se não for PDF, JPG nem PNG."""
    if inicio.startswith(b'\xff\xd8\xff'):
        return JPEG
    if inicio.startswith(b'\x89PNG\r\n\x1a\n'):
        return PNG
    # O cabeçalho do PDF pode vir depois de alguns bytes (o Acrobat aceita
    # até 1024; há scanners que geram assim). Não abre brecha: o que for
    # reconhecido como PDF sai como application/pdf, que o navegador nunca
    # trata como página.
    if b'%PDF-' in inicio[:BYTES_INSPECIONADOS]:
        return PDF
    return None


def _formatos_legiveis(extensoes):
    nomes = [extensao.upper() for extensao in extensoes if extensao != 'jpeg']
    if len(nomes) == 1:
        return nomes[0]
    return f'{", ".join(nomes[:-1])} ou {nomes[-1]}'


def _ler_inicio(arquivo):
    arquivo.seek(0)
    inicio = arquivo.read(BYTES_INSPECIONADOS)
    arquivo.seek(0)
    return inicio


def validar_upload(arquivo, extensoes=EXTENSOES_DOCUMENTO):
    """
    Confere um arquivo recém-enviado: extensão aceita, tamanho até o limite e
    conteúdo que bate com a extensão. Devolve o próprio arquivo ou levanta
    `ValidationError` com a mensagem para quem enviou.

    Só o que acabou de chegar do navegador é conferido: o arquivo já salvo
    (ex.: o form da foto de perfil reaberto sem foto nova) passa direto.
    """
    if not isinstance(arquivo, UploadedFile):
        return arquivo

    formatos = _formatos_legiveis(extensoes)
    extensao = os.path.splitext(arquivo.name or '')[1].lower().lstrip('.')
    if extensao not in extensoes:
        raise ValidationError(f'Formato não aceito: envie o arquivo em {formatos}.', code='formato')

    if arquivo.size > TAMANHO_MAXIMO:
        tamanho = f'{arquivo.size / 1024 / 1024:.1f}'.replace('.', ',')
        raise ValidationError(
            f'O arquivo tem {tamanho} MB e o limite é {TAMANHO_MAXIMO_MB} MB. '
            'Diminua o tamanho e envie de novo.',
            code='tamanho',
        )

    if tipo_pelo_conteudo(_ler_inicio(arquivo)) != extensoes[extensao]:
        raise ValidationError(
            f'O arquivo não é um {extensao.upper()} válido, apesar do nome. Gere o arquivo de novo e envie.',
            code='conteudo',
        )

    return arquivo


def blindar_resposta(resposta, inicio, nome):
    """
    Ajusta uma resposta de arquivo para o navegador nunca executá-lo: o tipo
    vem de `inicio` (os primeiros bytes); o que não for PDF, JPG ou PNG sai
    como download genérico.

    O `sandbox` (CSP) bloqueia script mesmo que um navegador insista em
    renderizar. Fica fora do PDF porque o leitor de PDF do Chrome se recusa a
    abrir PDF em sandbox -- e o popup de validação do RH depende dele.
    """
    tipo = tipo_pelo_conteudo(inicio)
    if tipo:
        resposta.headers['Content-Type'] = tipo
    else:
        resposta.headers['Content-Type'] = 'application/octet-stream'
        resposta.headers['Content-Disposition'] = content_disposition_header(True, nome)
    if tipo != PDF:
        resposta.headers['Content-Security-Policy'] = 'sandbox'
    return resposta


def resposta_de_arquivo(arquivo, nome=None):
    """`FileResponse` de um FileField, já blindada (ver `blindar_resposta`)."""
    nome = nome or os.path.basename(arquivo.name)
    arquivo.open('rb')
    inicio = _ler_inicio(arquivo)
    return blindar_resposta(FileResponse(arquivo, filename=nome), inicio, nome)


def resposta_de_arquivo_revalidada(request, arquivo):
    """
    `resposta_de_arquivo` para o que aparece em toda página (a foto de perfil
    no topo): o navegador guarda a cópia, confere a cada uso se o arquivo
    mudou e, se não mudou, recebe um 304 sem corpo em vez da foto inteira.

    `private`: nenhum cache no meio do caminho guarda o arquivo. O Cloudflare
    guarda sozinho, por 2 horas, endereço terminado em .jpg/.png/.pdf que
    chega sem Cache-Control, e ignora o `Vary: Cookie` -- entregaria a foto
    de um a qualquer um, mesmo sem login.
    """
    try:
        alterado_em = int(arquivo.storage.get_modified_time(arquivo.name).timestamp())
    except OSError:
        raise Http404('Arquivo não encontrado.')
    resposta = get_conditional_response(request, last_modified=alterado_em)
    if resposta is None:
        resposta = resposta_de_arquivo(arquivo)
    resposta.headers['Last-Modified'] = http_date(alterado_em)
    resposta.headers['Cache-Control'] = 'private, no-cache'
    return resposta
