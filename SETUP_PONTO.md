# Módulo de Ponto — configurar após puxar

O banco é por ambiente: as migrations trazem só a **estrutura** das tabelas
novas. Dados de referência (horários) e cadastros (equipamentos, fila de
sincronização) precisam ser recriados em cada banco.

## 1. Dependências

```
pip install -r requirements.txt
```

Novidade: `pymssql` (usado só pelos comandos de importação do Secullum).

## 2. Migrations

```
python manage.py migrate
```

Cria as tabelas do app `ponto`: `Equipamento`, `SincronizacaoFuncionario`,
`MarcacaoRelogio`, `AjustePonto`, `Horario`, `HorarioDia`, `Feriado`,
`FuncionarioJornada`.

## 3. Variáveis do Secullum (`.env` local, não versionado)

```
SECULLUM_SERVER=10.4.23.6
SECULLUM_INSTANCE=SQLEXPRESS
SECULLUM_DATABASE=PontoSecullum4
SECULLUM_USER=<usuario leitura>
SECULLUM_PASSWORD=<senha>
```

Só é necessário se for rodar os comandos de importação abaixo. O sistema
nunca escreve nesse banco (somente leitura).

## 4. Repovoar horários

Não vêm no Git — são reimportados direto do Secullum:

```
python manage.py importar_horarios_secullum 1001 1048
```

Horário 12x36: marque "É 12x36" ao cadastrar/editar o horário (tela
**Ponto > Horários**) -- os dias trabalhados/de folga são decididos por
funcionário (time par ou ímpar do mês) na hora de atribuir a jornada a ele,
não existe mais um cadastro de "Escala" separado.

## 5. Cadastrar os equipamentos reais

Pela tela **Ponto > Equipamentos**, cadastrar os 4 relógios com usuário e
senha de admin (fica cifrado no banco, não versionado):

| Nome             | IP             |
|------------------|----------------|
| PONTO ADM        | 192.168.0.187  |
| PONTO FARINHA    | 192.168.0.163  |
| PONTO MACARRAO   | 192.168.0.27   |
| PONTO SALGADINHO | 192.168.0.208  |

Usam HTTPS com certificado autoassinado (o formulário já marca "Usa HTTPS"
por padrão) e login `admin`/`admin`, salvo se tiver sido trocado.

## 6. Enfileirar funcionários que já existiam

**Importante**: o signal que enfileira sincronização só dispara quando um
`Funcionario` é salvo (criado ou editado) *depois* do módulo de Ponto já
estar instalado. Funcionários que já existiam no banco antes disso não
entram na fila sozinhos — rode uma vez:

```
python manage.py enfileirar_funcionarios_existentes
```

## 7. Sincronizar de verdade

Pela tela de Equipamentos, clicar em **"Sincronizar"** em cada relógio (ou
"Sincronizar todos") — isso envia os cadastros pendentes e puxa o
histórico completo de marcações de cada equipamento (idempotente, pode
rodar quantas vezes quiser).

Além do botão, os dois comandos abaixo fazem a mesma coisa pelo terminal:

```
python manage.py sincronizar_pendencias_control_id
python manage.py coletar_marcacoes_control_id
```

### Sincronização automática (06:00 e 18:00)

`scripts\sincronizar_ponto.bat` roda os dois comandos acima em sequência
(envia cadastros, depois recebe marcações) e grava a saída em
`logs\sincronizar_ponto.log`. Para agendar no servidor, abrir um prompt
**como administrador** na pasta do projeto e rodar (troque o caminho se o
projeto estiver em outro lugar):

```
schtasks /create /tn "SistemaRH\Sincronizar ponto 06h" /sc daily /st 06:00 /ru SYSTEM /rl HIGHEST /tr "\"C:\caminho\sistema-rh\scripts\sincronizar_ponto.bat\""
schtasks /create /tn "SistemaRH\Sincronizar ponto 18h" /sc daily /st 18:00 /ru SYSTEM /rl HIGHEST /tr "\"C:\caminho\sistema-rh\scripts\sincronizar_ponto.bat\""
```

`/ru SYSTEM` faz rodar mesmo sem ninguém logado no servidor. Para testar na
hora: `schtasks /run /tn "SistemaRH\Sincronizar ponto 06h"` e conferir o
log. Para remover: `schtasks /delete /tn "SistemaRH\Sincronizar ponto 06h" /f`.
