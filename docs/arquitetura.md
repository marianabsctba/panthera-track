# Arquitetura

## Regra da casa

Usar o que o GLPI já faz e customizar só o que ele não tem.

| Recurso | Quem faz |
|---|---|
| Chamado, status, acompanhamento e solução | GLPI |
| Prazo de resolução, pausa e durações | GLPI (SLA, regras e estatísticas do chamado) |
| Categorias e tipo | GLPI |
| Anexos e hash do arquivo | GLPI (documentos ligados ao chamado) |
| Autor real das ações | GLPI (com um usuário-sombra por pessoa) |
| Status de volta ao SIEM, com assinatura e retentativa | GLPI (webhook nativo) |
| Escalonamento automático por prazo e grupos N2 e N3 | GLPI (níveis de SLA e grupos) |
| Tela, login com segundo fator e fila única entre instâncias | Nosso: o GLPI enxerga uma instância só e a tela não pode revelar a ferramenta |
| Contexto do SIEM e contato de quem pediu | Nosso: o GLPI não guarda isso por chamado |
| Eventos com autor e motivo, e SHA-256 dos anexos | Nosso: a API do GLPI não expõe o log de alterações |
| Primeira resposta | Nosso: a API não informa o prazo, e o motor marca a tomada de conhecimento até em escalonamento automático |

Antes de construir qualquer coisa, a pergunta é se o GLPI já faz.

## Decisões

1. **GLPI sem modificação, por baixo.** Sem fork: correção de segurança entra trocando a imagem. A versão mínima é a 12.0.1, que corrige um bypass crítico de autenticação externa.
2. **A fachada é a única porta.** API e tela próprias. O motor fica preso ao endereço local e não é alcançável de fora. Nada da resposta revela a ferramenta por baixo.
3. **Uma instância por cliente.** GLPI e banco próprios para cada cliente, criados pela provisão. As entidades do GLPI são isolamento lógico num banco único, o que não basta aqui.
4. **Banco da fachada com RLS forçado.** Sem cliente na transação, as tabelas de chamados não devolvem nada. O papel da API não apaga nem reescreve o histórico.
5. **O SLA é do motor.** Resolução e primeira resposta nascem de regras dele. A fachada só soma a pausa em curso, porque o motor adia o prazo apenas quando o chamado sai de Pendente.
6. **O SLA nasce de regra de negócio do motor, por criticidade.** Assim vale para qualquer canal, inclusive e-mail, e a conta da fachada não precisa do direito de atribuir SLA.
7. **Regras que precisam valer em todo canal ficam num plugin do motor.** E-mail e tarefas automáticas não passam pela fachada. O plugin ainda não existe: nesta fatia as regras estão na fachada.
8. **Cada pessoa tem um usuário-sombra no motor**, com senha gerada e cifrada, e o login fica na fachada. Assim o histórico nativo registra o autor real. Quem escreve é o usuário-sombra; a leitura usa a conta da fachada, depois da permissão conferida aqui.
9. **Login próprio com segundo fator obrigatório.** Senha com Argon2, troca da senha provisória na primeira entrada, aplicativo autenticador e códigos de recuperação. SSO pode entrar depois, sem mudar o resto.
10. **Fila única para o analista.** Um login alcança os clientes vinculados. A fachada consulta cada instância em separado, indica o cliente em cada linha e põe na frente o que está mais perto de estourar. Um cliente fora do ar vira um aviso, sem tirar os outros da vista.
11. **Criar pessoa é administração.** Exige acesso ao Docker e o papel dono do banco, que a API não tem.
12. **O código é sigla + número, como `ALFA-0001`.** Cada instância conta do 1, então o número sozinho repete entre clientes. Uma sequência global mostraria a um cliente o volume dos outros.
13. **Anexos ficam no motor.** A API nova ignora o arquivo enviado, então só o envio usa a API antiga, numa sessão curta, liberada apenas para a rede interna do Docker. A fachada confere extensão e tamanho, entrega sempre como download, confere se o documento é daquele chamado e grava o SHA-256 no histórico.
14. **O status volta ao SIEM pelo webhook nativo**, que também pega mudanças feitas fora da fachada. Por isso o contrato é um aviso curto seguido de consulta, sem ordem garantida.
15. **Escalonar muda quem atende, nunca a criticidade nem o prazo.** N1 é o atendimento normal, sem grupo, porque atribuir grupo na abertura tiraria o chamado de Aberto. Vencido o prazo, o motor manda para N2; passada mais metade do prazo, para N3. O analista também escala à mão, com motivo, sempre para cima. O nível é o grupo mais alto da equipe do chamado.
16. **Telefone e e-mail de contato não saem do chamado.** Não vão ao SIEM, à fila nem aos eventos, que são imutáveis: assim dá para apagar o contato depois sem mexer no histórico. O contexto do SIEM recusa campo desconhecido.

## Componentes

```
SIEM  -- pedido assinado -->  fachada (FastAPI)  -- OAuth -->  GLPI do cliente A + MariaDB
                              fachada (FastAPI)  -- OAuth -->  GLPI do cliente B + MariaDB
                              fachada (FastAPI)  --> Postgres com RLS: pessoas, contexto, eventos, credenciais cifradas
SIEM  <-- webhook nativo assinado --  GLPI de cada cliente
```

## O que o motor mostrou na prática

Testado na versão 12.0.1:

- 65 s em Pendente adiaram o prazo em 65 s, mas só depois de sair de Pendente.
- Sem calendário, a contagem é contínua, 24x7.
- O usuário criado pelo console entra com o perfil de autoatendimento. É preciso mandar o cabeçalho `GLPI-Profile`, senão o PATCH devolve 500 e o SLA é ignorado sem erro.
- Client credentials não serve para chamados: a API exige usuário real, com grant de senha.
- Criar o cliente OAuth exige rodar código dentro do motor, porque o segredo é cifrado com a chave dele.
- Usuário com perfil de autoatendimento abre chamado e informa a prioridade; a regra de SLA dispara normalmente, e o motor não o deixa ler chamado alheio.
- Trocar a prioridade recalcula o SLA pela regra.
- Comentário do cliente em Pendente não tira o chamado da pausa: quem retoma é o analista.
- A API nova aceita criar documento, mas ignora o arquivo enviado. Pela API antiga, o arquivo entra, liga ao chamado, aparece na linha do tempo e baixa pela API nova.
- O webhook nativo recusa destino em rede privada. A liberação é por contexto, num arquivo em `/var/www/glpi/config`, e não na pasta de dados.
- A assinatura do webhook é HMAC-SHA256 de corpo + hora, nos cabeçalhos `X-GLPI-signature` e `X-GLPI-timestamp`.
- Categoria e SLA de resposta valem também para o perfil de autoatendimento.
- Atribuir grupo passa o chamado para Em andamento e marca a tomada de conhecimento, mesmo sem ninguém assumir. Por isso, assumir vale nos dois status, se ainda não houver atendente, e a primeira resposta é contada na fachada, quando uma pessoa assume.
- O técnico não pode atribuir grupo; o supervisor pode. A fachada e os analistas usam o perfil de supervisor.
- A API cria o nível de SLA, mas não as ações dele, então isso roda como código dentro do motor.
- A tarefa de escalonamento vem em modo interno, a cada 5 minutos, e só roda com acesso à tela do GLPI. A provisão passa para externa, a cada minuto.
- A API não tem rota para o log de alterações do chamado. Mudanças feitas fora da fachada precisarão de webhook do motor ou do plugin.

## Mapeamentos

| Criticidade | Urgência, impacto e prioridade |
|---|---|
| Crítica | 5 |
| Alta | 4 |
| Média | 3 |
| Baixa | 2 |
| Consulta | 1 |

| Status | No motor |
|---|---|
| aberto | 1 |
| em_andamento | 2 ou 3 |
| aguardando_cliente | 4, com pausa do SLA |
| resolvido | 5 |
| encerrado | 6 |

## Cobertura

| Requisito | Situação |
|---|---|
| Abertura automática pelo SIEM, idempotente | Pronto |
| Acompanhamento e encerramento | Pronto por API |
| Cinco criticidades, cada uma com seu prazo | Pronto |
| Número, datas com hora e minuto, solicitante, atendente, descrição, criticidade, andamento e solução | Pronto |
| SLA em horas corridas, com pausa quando depende do cliente | Pronto |
| Nada encerra sem resolução | Pronto na fachada; falta o plugin para outros canais |
| Status de volta ao SIEM | Pronto, com o webhook nativo: assinado e com retentativa, até um minuto de atraso |
| Isolamento entre clientes | Pronto |
| Histórico | Eventos para o que passa pela fachada; o motor registra tudo, mas a API dele não expõe o log |
| Encerramento sem resolução por decisão do cliente | Falta |
| Mudança de criticidade só com aceite do cliente | Falta |
| Tela, login com segundo fator e pessoas do cliente abrindo chamados, testado com 15 pessoas ao mesmo tempo | Pronto |
| Autor real no histórico, com usuários-sombra | Pronto para o que vem da tela; a API assinada segue com a conta da fachada |
| Registro de chamado por telefone | Pronto |
| Aviso de SLA em risco na fila, quando resta um quarto do prazo | Pronto |
| Código único na fila, com sigla do cliente | Pronto |
| Tipo e categoria, que o analista pode corrigir | Pronto |
| Contexto do incidente vindo do SIEM | Pronto; a mudança no Roar para enviá-lo vem depois |
| Primeira resposta, barra dos prazos, durações do motor e motivo de cada pausa | Pronto |
| Contato de quem pediu e anexos | Pronto |
| Escalonamento manual e automático, com N2 e N3 | Pronto; o aviso por e-mail em 75% do prazo vem com a fatia de avisos, e a fila já marca Em risco |
| Abertura por e-mail | Falta |
| Aviso eletrônico a cada movimentação | Falta |
| Busca por período, chamado, criticidade, situação e solicitante | Falta |
| Exportação do histórico em JSON e relatório mensal | Falta |

## Licenças

O GLPI é GPL-3.0. Código que roda dentro dele, como o plugin, herda a GPL. A fachada só fala por API e é AGPL-3.0, como o Panthera Roar. Em instalação entregue ao cliente, é preciso fornecer o fonte do GLPI e do plugin, junto com o pacote.

## Limites conhecidos

- A assinatura vale por 5 minutos e não há controle de repetição. Reenviar uma ação é inofensivo porque a transição seguinte é recusada, exceto `andamento`, que registraria o texto de novo.
- A chamada ao motor acontece dentro da transação da fachada. Se o commit falhar depois dela, o motor fica à frente até a próxima leitura. Na abertura, o reenvio reencontra o chamado pelo id externo.
- A fachada ainda não tem TLS próprio nem proxy: roda em endereço local.
- As tabelas de pessoas não têm RLS, porque o login precisa achar a pessoa antes de saber o cliente. O alcance é conferido na aplicação; chamados e eventos seguem com RLS.
- O bloqueio por tentativas é por conta: alguém pode travar uma conta de propósito errando a senha, por 15 minutos.
- A fila lista até 200 chamados por cliente, até existir a busca.
- O escalonamento automático aparece no nível do chamado, mas não gera evento, porque acontece dentro do motor. Entra quando o histórico completo for resolvido.
- Sem antivírus nos anexos por enquanto: o download nunca abre na tela, mas quem baixa precisa de cuidado.
- A API antiga fica ligada só para o envio de anexos, restrita à rede interna do Docker. Se a API nova passar a receber arquivo, ela sai.
- O motivo de pausa ainda é texto livre. O motor tem motivos de pendência nativos, com lembrete e resolução automáticos, que podem entrar quando o processo de atendimento for definido.
