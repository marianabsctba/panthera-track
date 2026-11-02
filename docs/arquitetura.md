# Arquitetura

## Decisões

1. **GLPI sem modificação, por baixo.** Sem fork: correção de segurança entra trocando a imagem. A versão mínima é a 12.0.1, que corrige um bypass crítico de autenticação externa.
2. **A fachada é a única porta.** API e tela próprias. O motor fica preso ao endereço local e não é alcançável de fora. Nada da resposta revela a ferramenta por baixo.
3. **Uma instância por cliente.** GLPI e banco próprios para cada cliente, criados pela provisão. As entidades do GLPI são isolamento lógico num banco único, o que não basta aqui.
4. **Banco da fachada com RLS forçado.** Sem cliente na transação, as tabelas de chamados não devolvem nada. O papel da API não apaga nem reescreve o histórico.
5. **O SLA é do motor.** A fachada só soma a pausa em curso, porque o motor adia o prazo apenas quando o chamado sai de Pendente.
6. **O SLA nasce de regra de negócio do motor, por criticidade.** Assim vale para qualquer canal, inclusive e-mail, e a conta da fachada não precisa do direito de atribuir SLA.
7. **Regras que precisam valer em todo canal ficam num plugin do motor.** E-mail e tarefas automáticas não passam pela fachada. O plugin ainda não existe: nesta fatia as regras estão na fachada.
8. **Cada pessoa tem um usuário-sombra no motor**, com senha gerada e cifrada, e o login fica na fachada. Assim o histórico nativo registra o autor real. Quem escreve é o usuário-sombra; a leitura usa a conta da fachada, depois da permissão conferida aqui.
9. **Login próprio com segundo fator obrigatório.** Senha com Argon2, troca da senha provisória na primeira entrada, aplicativo autenticador e códigos de recuperação. SSO pode entrar depois, sem mudar o resto.
10. **Fila única para o analista.** Um login alcança os clientes vinculados. A fachada consulta cada instância em separado, indica o cliente em cada linha e põe na frente o que está mais perto de estourar. Um cliente fora do ar vira um aviso, sem tirar os outros da vista.
11. **Criar pessoa é administração.** Exige acesso ao Docker e o papel dono do banco, que a API não tem.

## Componentes

```
SIEM  -- pedido assinado -->  fachada (FastAPI)  -- OAuth -->  GLPI do cliente A + MariaDB
SIEM  <-- webhook assinado -- fachada (FastAPI)  -- OAuth -->  GLPI do cliente B + MariaDB
                              fachada (FastAPI)  --> Postgres com RLS: chamados, eventos, credenciais cifradas
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
| Status de volta ao SIEM | Pronto, assinado, em ordem e com retentativa |
| Isolamento entre clientes | Pronto |
| Histórico | Pronto para o que passa pela fachada |
| Encerramento sem resolução por decisão do cliente | Falta |
| Mudança de criticidade só com aceite do cliente | Falta |
| Tela, login com segundo fator e pessoas do cliente abrindo chamados, testado com 15 pessoas ao mesmo tempo | Pronto |
| Autor real no histórico, com usuários-sombra | Pronto para o que vem da tela; a API assinada segue com a conta da fachada |
| Registro de chamado por telefone | Pronto |
| Aviso de SLA em risco na fila, quando resta um quarto do prazo | Pronto |
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
