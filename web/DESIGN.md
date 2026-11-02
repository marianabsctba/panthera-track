# Padrão da console

A console segue o padrão da console do Panthera Roar: React com Mantine, preto com rosa metálico. O tema (`src/tema.ts`) e o estilo (`src/estilo.css`) são os mesmos. Estas regras valem para toda tela.

## Texto

- Pouco texto na tela. Nada de parágrafo explicando o que a tela faz: isso vai no `ajuda` do `Cabecalho`, em uma ou duas frases.
- Português do Brasil, com acentos. Sem travessão.
- A tela não cita nome de ferramenta por baixo nem conta bastidor.

## Controles

- Três estilos de botão: `variant="filled"` para a ação principal, o padrão para as secundárias, `variant="subtle" color="gray"` para Cancelar.
- No máximo uma ação principal por área da tela.
- Busca é `Busca`: compacta, aplica sozinha, sem botão Buscar.
- Filtros ficam em `Select` pequeno, na mesma linha da busca.
- Formulários abrem em `Modal`. Detalhes abrem ao lado da lista, ou em `Drawer` em tela estreita. Página longa vira `Tabs`.
- Erro da API aparece num `Alert` curto.

## Tudo leva a algum lugar

Todo número filtra exatamente o que ele conta.

## Cores

- Rosa metálico é a marca: ação principal e seleção.
- Criticidade nunca é rosa: cada uma tem a própria cor e sempre leva ícone e texto (`Criticidade`).
- Data é `Data`, que não quebra linha.

## Permissões

Esconda o que a pessoa não pode fazer, mas quem decide é a API.

## Construção

Na pasta `web`, `npm run construir` confere os tipos e gera os arquivos em `fachada/track_api/estatico/novo`.
