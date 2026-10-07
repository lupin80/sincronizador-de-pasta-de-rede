# Desenvolvimento e publicação

[Voltar ao README](../README.md) · Versão 2.9.0

## Estrutura do projeto

```text
README.md
CHANGELOG.md
.gitignore
docs/
  GUIA_DO_USUARIO.md
  DESENVOLVIMENTO.md
  imagens/
    tela-principal.png
    sincronizacao-automatica.png
    comparacao.png
src/
  app.py
  paths_config.py
  sync_runtime.py
  trash_runtime.py
  trash_index.py
  windows_schedule.py
  windows_paths.py
  comparison_runtime.py
  comparison_ui.py
  sincronizador.bat
  config.ini
  app.ico
  requirements-build.txt
  gerar.ps1
  instalador.iss
```

Os arquivos de configuração usados pelo aplicativo ficam em AppData, fora do repositório. O `config.ini` de `src/` é um modelo distribuído com o programa. `caminhos.json`, logs, executáveis compilados, ambientes virtuais e backups de configuração são ignorados pelo Git.

## Responsabilidade dos módulos

- `app.py`: interface Tkinter, bandeja com pystray, controles, notificações e argumentos do EXE.
- `paths_config.py`: validação e gravação dos caminhos em JSON, sem exigir que uma rede offline esteja acessível ao salvar.
- `sync_runtime.py`: atualização do BAT, inventário da origem, estimativa de progresso e execução com Windows Job Object para interromper os processos filhos.
- `sincronizador.bat`: parâmetros editáveis, coordenação dos destinos, chamadas ao Robocopy e às operações de lixeira.
- `trash_runtime.py`: arquivamento sem subpastas, nomes sem colisão, transferência entre unidades, retenção por arquivo e compatibilidade com lotes antigos.
- `trash_index.py`: índice SQLite em AppData, caminho original, identidade do arquivo, estados pendente/concluído e data de arquivamento.
- `windows_schedule.py`: consulta, criação, atualização e desativação da tarefa do usuário no Agendador do Windows.
- `windows_paths.py`: formato estendido para operações de arquivo, normalização das chaves de progresso e limite UTF-16 das notificações.
- `comparison_runtime.py`: comparação somente leitura por tamanho e data, ações previstas, resumo por destino e exportação CSV.
- `comparison_ui.py`: janela de prévia, filtros e paginação, sem alterar a seleção de destinos da sincronização.
- `instalador.iss`: instalação por usuário, atalhos e atualização do motor após instalar o EXE.

O BAT usa `SincronizadorRede.exe --trash-operation` para acessar a rotina de lixeira. Isso mantém a instalação independente de Python. Os comandos internos retornam antes de criar a janela ou adquirir o controle da instância da bandeja.

## Preparar o ambiente

Use Windows de 64 bits, Python 3.12 ou posterior com Tkinter e Inno Setup 6. A versão 2.9.0 foi compilada e validada com Python 3.12, PyInstaller 6.22.3, pystray 0.19.5 e Pillow 12.3.0. O índice usa o SQLite incluído no Python e no EXE.

Os comandos abaixo devem ser executados no PowerShell, na raiz deste projeto:

```powershell
python -m venv .venv
$pythonCompilacao = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $pythonCompilacao -m pip install -r '.\src\requirements-build.txt'
```

Para abrir o programa a partir dos fontes:

Feche primeiro uma instância já aberta pelo menu **Sair** da bandeja, para que o comando abra a versão dos fontes.

```powershell
& $pythonCompilacao '.\src\app.py' --window
```

Essa execução usa a pasta de dados do usuário em AppData. Configure pastas temporárias pela tela quando estiver validando o comportamento de cópia.

## Gerar EXE e instalador

Na raiz do projeto:

```powershell
$pythonCompilacao = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$compiladorInno = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
& '.\src\gerar.ps1' -Python $pythonCompilacao -InnoCompiler $compiladorInno
```

Se o Inno Setup estiver em outro local, ajuste `$compiladorInno` para o caminho completo do `ISCC.exe`.

O script instala as dependências fixadas, gera o EXE em arquivo único com o BAT e o INI incorporados e compila o instalador. Os resultados ficam na raiz:

- `SincronizadorRede.exe`.
- `Instalar_Sincronizador_de_Rede.exe`.

O instalador executa `--upgrade-engine` após instalar o EXE e verifica o retorno. A atualização troca o motor antigo pelo modelo 2.9.0, preserva os parâmetros editáveis e guarda o BAT anterior nos logs. `caminhos.json` não é regravado nessa operação.

## Parâmetros do motor

Os parâmetros usados na execução ficam no BAT instalado:

- `RETENCAO_DIAS=5`: prazo dos arquivos na lixeira, desde o arquivamento.
- `DRYRUN=0`: execução normal; `1` simula as operações sem copiar, mover ou limpar arquivos.
- `TENTATIVAS=3`: repetições do Robocopy quando há falha de cópia.
- `ESPERA=5`: segundos entre tentativas.

Os caminhos salvos na interface são enviados ao BAT por variáveis `SC_*` e prevalecem sobre os valores de origem, destinos e lixeira no texto do motor.

Para executar o BAT diretamente, é necessário fornecer a configuração de caminhos que ele espera. Ele não lê `caminhos.json` sozinho; o uso normal é pelo aplicativo.

## Validação antes de uma versão

Use pastas temporárias e confira estes cenários:

1. Origem com arquivos, subpastas, pasta vazia e um nome com `!`; destinos ainda não criados.
2. Primeira execução copiando todos os arquivos e segunda execução sem recopiar os iguais.
3. Arquivo novo, arquivo modificado e mudança de mesmo tamanho com diferença de um segundo na data.
4. Exclusão na origem de arquivo em subpasta e de arquivo antigo, verificando o conteúdo dos lotes na lixeira.
5. Falha de movimentação retornando erro, preservando o arquivo no destino e suspendendo a cópia para esse destino.
6. Atualização do BAT antigo preservando caminhos, parâmetros e backup; tentativa com execução ativa sendo recusada.
7. Bandeja, botão de parada, abertura da janela, progresso e notificações no EXE compilado.
8. Caminhos acima de 450 caracteres na análise, primeira cópia, atualização, arquivamento e retenção; chaves de progresso com e sem prefixo estendido.
9. Notificações com mais de 255 unidades UTF-16, incluindo caracteres fora do plano básico Unicode; detalhes completos na janela de erro.
10. Comparação preservando conteúdo e datas dos arquivos, sem criar destinos nem lixeira; classificações e CSV com nomes Unicode.
11. Parada da comparação, falha de acesso, conflito entre arquivo e pasta, filtros, paginação e exportação completa.
12. Bloqueio de sincronização pela prévia quando os caminhos ou o motor mudarem; disparos automáticos ignorados durante comparação.
13. Lixeira sem novas subpastas; nomes repetidos em subpastas/destinos diferentes, nomes longos e extensões preservadas.
14. Retenção por arquivo antigo arquivado recentemente, arquivo somente leitura, arquivos manuais e arquivo arquivado alterado.
15. Falhas de cópia e do índice preservando o original; recuperação de registro pendente com prazo completo e compatibilidade com lotes antigos.

A versão 2.7.1 passou por testes locais de cópia, lixeira, retenção, atualização e interface com o EXE compilado, incluindo arquivos com caminhos acima de 450 caracteres. Também foram conferidos nomes especiais, falhas de movimentação e a preservação dos caminhos e parâmetros na atualização. Os testes não comprovam acesso aos compartilhamentos de uma instalação específica. A criação real de uma tarefa deve ser conferida no Windows de destino; o ambiente de validação não permitiu registrá-la.

A versão 2.8.0 acrescentou validação da comparação somente leitura, classificação por tamanho e data, pastas vazias, caminhos longos e nomes Unicode, paginação, filtros, CSV completo, cancelamento e bloqueio de prévia desatualizada. O teste do EXE compilado também confere a janela de comparação e a preservação dos arquivos durante a análise.

A versão 2.9.0 validou a lixeira sem subpastas, colisões entre destinos, nomes longos, retenção por arquivo, falhas de cópia e do índice, recuperação de movimentação pendente e compatibilidade com lotes antigos. O fluxo de primeira cópia, atualização e exclusão foi conferido em dois destinos locais com Robocopy e o helper compilado.

## Preparar a publicação no GitHub

1. Crie ou abra o repositório desejado.
2. Envie o **conteúdo interno** desta pasta: `README.md`, `CHANGELOG.md`, `.gitignore`, `docs/` e `src/`. O README deve ficar na raiz do repositório.
3. Mantenha a estrutura de `docs/imagens/`; os links das imagens são relativos e funcionam com os arquivos no repositório.
4. Use **Adicionar arquivo → Carregar arquivos**, ou envie os arquivos com Git. [Guia oficial de upload](https://docs.github.com/pt/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).
5. Em **Releases / Versões**, prepare uma versão com a tag `v2.9.0`, use o histórico como notas e anexe `Instalar_Sincronizador_de_Rede.exe`. [Guia oficial de releases](https://docs.github.com/pt/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

O ZIP preparado contém os fontes e a documentação. Extraia-o antes de enviar o conteúdo ao repositório. O instalador pode ser distribuído como arquivo da release.
