# Sincronizador de Rede

Aplicativo para Windows que sincroniza uma pasta de origem com até três destinos locais ou de rede. Funciona pela bandeja do sistema e permite executar a sincronização manualmente ou em intervalos definidos pelo usuário.

**Versão atual: 2.9.0 · Windows 10/11 de 64 bits · Interface em português.**

![Janela principal do Sincronizador de Rede, com campos de origem, destinos e lixeira](docs/imagens/tela-principal.png)

*Interface da versão 2.8, com os campos vazios para a configuração inicial.*

## Recursos

- Origem, até três destinos e caminho da lixeira editáveis pela interface.
- Primeira cópia completa; execuções seguintes copiam arquivos novos ou diferentes e ignoram os iguais.
- Criação da pasta de destino quando a unidade ou o compartilhamento estiver acessível e permitir a operação.
- Botões para iniciar e parar a sincronização.
- Progresso estimado na janela e no ícone da bandeja, som de conclusão e aviso de erro.
- Agendamento pelo Windows, com intervalos em minutos, horas ou dias.
- Arquivos apagados diretamente na lixeira escolhida, sem criar subpastas, com proteção contra nomes repetidos e retenção desde o arquivamento.
- Logs locais e atualização do motor sem apagar os caminhos salvos.
- Suporte a caminhos longos locais e de rede na análise e na lixeira.
- Comparação antes da sincronização, com arquivos a copiar, atualizar ou enviar à lixeira, pastas a criar e resumo por destino.
- Lista paginada, filtros por destino e ação e exportação completa em CSV.

## Instalação e primeiro uso

1. Execute `Instalar_Sincronizador_de_Rede.exe`, disponibilizado com a versão do programa.
2. Na janela inicial, informe **Origem**, **Lixeira** e pelo menos um **Destino**.
3. Clique em **Salvar caminhos**.
4. Clique em **Comparar** para revisar as diferenças e depois em **Sincronizar agora**. Também é possível iniciar diretamente ou configurar o agendamento na bandeja.

O instalador não exige Python e instala o aplicativo para o usuário atual em `%LOCALAPPDATA%\SincronizadorRede`. Origem, destinos e lixeira não vêm preenchidos.

Depois de configurar os caminhos, o aplicativo abre na bandeja. Use o menu do ícone para abrir a janela, iniciar ou parar uma sincronização, configurar a execução automática, abrir os logs ou sair.

## Comparar antes de sincronizar

O botão **Comparar** e o menu **Comparar pastas** da bandeja analisam a origem e os destinos sem copiar, mover ou excluir arquivos. A prévia mostra as ações previstas e as quantidades e tamanhos por destino.

![Prévia da sincronização com ações de copiar, atualizar e enviar à lixeira](docs/imagens/comparacao.png)

*Tela do aplicativo com dados de exemplo.*

Use os filtros para consultar um destino ou uma ação. **Exportar CSV completo** salva todas as alterações, mesmo que a lista esteja filtrada. **Sincronizar agora** processa todos os destinos salvos. Se os caminhos ou o motor forem alterados depois da análise, faça uma nova comparação.

A comparação considera tamanho e data de modificação. A prévia é um retrato da análise: os arquivos podem mudar antes da execução, e o motor verifica os arquivos novamente. Links e junções não são analisados na prévia.

## Sincronização automática

No ícone da bandeja, escolha **Sincronização automática...**, marque **Executar sincronização automaticamente**, escolha o intervalo e clique em **Salvar agendamento**.

![Tela de configuração da sincronização automática](docs/imagens/sincronizacao-automatica.png)

O intervalo vai de **1 minuto a 31 dias**. A primeira execução ocorre após o intervalo escolhido. A tarefa funciona enquanto o usuário estiver conectado ao Windows e usa os caminhos salvos. Disparos durante outra sincronização são ignorados.

**Iniciar com o Windows** abre o aplicativo na bandeja; **Sincronização automática** configura as execuções periódicas. As duas opções são independentes.

## Como os arquivos são tratados

A origem é a referência. Arquivos existentes somente no destino são enviados à lixeira configurada. Arquivos diferentes são substituídos pela versão da origem; essa substituição não guarda a versão anterior na lixeira.

A comparação usa tamanho e data de modificação, sem comparação de conteúdo por hash. Cada sincronização termina depois de processar os destinos; as próximas atualizações dependem de nova execução manual ou agendada.

A partir da versão 2.9, os arquivos apagados ficam diretamente na lixeira escolhida. O nome original é preservado quando disponível; nomes repetidos recebem um sufixo com data, destino e identificador. O caminho original e a data de arquivamento ficam no índice `%LOCALAPPDATA%\SincronizadorRede\lixeira.sqlite3`, fora da lixeira.

A retenção padrão é de cinco dias desde o arquivamento. A limpeza reconhece apenas arquivos registrados e ainda correspondentes ao índice. Os lotes das versões 2.7/2.8 continuam sendo tratados pela retenção antiga; arquivos sem registro confiável de exclusão são preservados.

## Documentação

- [Guia do usuário](docs/GUIA_DO_USUARIO.md): configuração, bandeja, lixeira, logs e solução de problemas.
- [Desenvolvimento e publicação](docs/DESENVOLVIMENTO.md): estrutura, compilação e preparação de uma versão no GitHub.
- [Histórico de versões](CHANGELOG.md).

## Código-fonte

Os fontes estão em [`src/`](src/). Para executar ou compilar, use Windows de 64 bits, Python 3.12 ou posterior com Tkinter e as dependências fixadas em `src/requirements-build.txt`. A geração do instalador usa Inno Setup 6.

Consulte o [guia de desenvolvimento](docs/DESENVOLVIMENTO.md) para os comandos e os arquivos gerados.
