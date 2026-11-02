import { ActionIcon, Alert, Anchor, Button, FileButton, Group, Menu, Modal, Select, SimpleGrid, Stack, Tabs, Text, Textarea, TextInput, Title } from "@mantine/core";
import { IconDots, IconPaperclip } from "@tabler/icons-react";
import { useState } from "react";

import { detalhe, enviar, enviarArquivo, useAnalista, useDados, type ChamadoCompleto, type Historico } from "../api";
import { BarraDePrazo, Carregando, Criticidade, Data, Fato, Nivel, Situacao, Vazio } from "../componentes";
import { CATEGORIAS, duracao, EVENTOS, opcoes, ORIGENS, quando, semHtml, TIPOS } from "../formato";

type Formulario = { titulo: string; rotulo: string; acao: string; botao: string; campo: string } | null;

function ModalDeTexto({ formulario, aoEnviar, aoFechar }: { formulario: Formulario; aoEnviar: (acao: string, corpo: Record<string, string>) => Promise<string | null>; aoFechar: () => void }) {
  const [texto, setTexto] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const fechar = () => { setTexto(""); setErro(null); aoFechar(); };
  const confirmar = async () => {
    const falha = await aoEnviar(formulario!.acao, { [formulario!.campo]: texto.trim() });
    if (falha) setErro(falha);
    else fechar();
  };
  return (
    <Modal opened={formulario !== null} onClose={fechar} title={formulario?.titulo}>
      <Stack gap="sm">
        <Textarea label={formulario?.rotulo} autosize minRows={3} maxRows={8} value={texto} onChange={(e) => setTexto(e.currentTarget.value)} data-autofocus />
        {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={fechar}>Cancelar</Button>
          <Button variant="filled" onClick={confirmar} disabled={!texto.trim()}>{formulario?.botao}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ModalDeEscalar({ aberto, nivelAtual, aoEnviar, aoFechar }: { aberto: boolean; nivelAtual: string; aoEnviar: (acao: string, corpo: Record<string, string>) => Promise<string | null>; aoFechar: () => void }) {
  const niveis = nivelAtual === "N1" ? ["N2", "N3"] : ["N3"];
  const [nivel, setNivel] = useState<string | null>(niveis[0]);
  const [motivo, setMotivo] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const fechar = () => { setMotivo(""); setErro(null); aoFechar(); };
  const confirmar = async () => {
    const falha = await aoEnviar("escalar", { nivel: nivel ?? niveis[0], motivo: motivo.trim() });
    if (falha) setErro(falha);
    else fechar();
  };
  return (
    <Modal opened={aberto} onClose={fechar} title="Escalar">
      <Stack gap="sm">
        <Select label="Nível" data={niveis} value={niveis.includes(nivel ?? "") ? nivel : niveis[0]} onChange={setNivel} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
        <TextInput label="Motivo" maxLength={1000} value={motivo} onChange={(e) => setMotivo(e.currentTarget.value)} data-autofocus />
        {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={fechar}>Cancelar</Button>
          <Button variant="filled" onClick={confirmar} disabled={!motivo.trim()}>Escalar</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ModalDeClassificar({ aberto, c, aoEnviar, aoFechar }: { aberto: boolean; c: ChamadoCompleto; aoEnviar: (acao: string, corpo: Record<string, string>) => Promise<string | null>; aoFechar: () => void }) {
  const inicial = Object.keys(CATEGORIAS).find((k) => CATEGORIAS[k] === c.categoria) ?? "outro";
  const [tipo, setTipo] = useState<string | null>(c.tipo);
  const [categoria, setCategoria] = useState<string | null>(inicial);
  const [erro, setErro] = useState<string | null>(null);
  const confirmar = async () => {
    const falha = await aoEnviar("classificar", { tipo: tipo ?? c.tipo, categoria: categoria ?? inicial });
    if (falha) setErro(falha);
    else aoFechar();
  };
  return (
    <Modal opened={aberto} onClose={aoFechar} title="Classificar">
      <Stack gap="sm">
        <Select label="Tipo" data={opcoes(TIPOS)} value={tipo} onChange={setTipo} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
        <Select label="Categoria" data={opcoes(CATEGORIAS)} value={categoria} onChange={setCategoria} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
        {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={aoFechar}>Cancelar</Button>
          <Button variant="filled" onClick={confirmar}>Salvar</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function AbaResumo({ c }: { c: ChamadoCompleto }) {
  const x = c.contexto ?? {};
  const contato = [c.contato.telefone, c.contato.email].filter(Boolean).join(", ");
  const temContexto = ["ativo", "ip_origem", "ip_destino", "usuario", "regra", "tecnica_mitre", "url_alerta"].some((k) => x[k]);
  return (
    <Stack gap="md">
      <Text size="sm" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{semHtml(c.descricao)}</Text>
      <SimpleGrid cols={{ base: 2, md: 3 }} spacing="sm" data-testid="dados">
        <Fato rotulo="Tipo" valor={TIPOS[c.tipo] ?? c.tipo} />
        <Fato rotulo="Categoria" valor={c.categoria} />
        <Fato rotulo="Origem" valor={ORIGENS[c.origem] ?? c.origem} />
        <Fato rotulo="Solicitante" valor={c.solicitante} />
        <Fato rotulo="Contato" valor={contato} />
        <Fato rotulo="Atendente" valor={c.atendente} />
        <Fato rotulo="Abertura" valor={<Data valor={c.aberto_em} />} />
        <Fato rotulo="Resolução" valor={c.resolvido_em && <Data valor={c.resolvido_em} />} />
        <Fato rotulo="Encerramento" valor={c.encerrado_em && <Data valor={c.encerrado_em} />} />
      </SimpleGrid>
      {temContexto && (
        <div>
          <Title order={3} mb="xs">Contexto do incidente</Title>
          <SimpleGrid cols={{ base: 2, md: 3 }} spacing="sm">
            {x.ativo && <Fato rotulo="Ativo ou local" valor={x.ativo} />}
            {x.ip_origem && <Fato rotulo="IP de origem" valor={<span className="mono">{x.ip_origem}</span>} />}
            {x.ip_destino && <Fato rotulo="IP de destino" valor={<span className="mono">{x.ip_destino}</span>} />}
            {x.usuario && <Fato rotulo="Usuário" valor={<span className="mono">{x.usuario}</span>} />}
            {x.regra && <Fato rotulo="Regra" valor={x.regra} />}
            {x.tecnica_mitre && <Fato rotulo="MITRE ATT&CK" valor={<Anchor size="sm" c="rosa.2" href={`https://attack.mitre.org/techniques/${x.tecnica_mitre.replace(".", "/")}/`} target="_blank" rel="noopener noreferrer">{x.tecnica_mitre}</Anchor>} />}
            {/* O servidor só aceita http e https; a conferência aqui é a segunda barreira. */}
            {x.url_alerta && /^https?:\/\//.test(x.url_alerta) && <Fato rotulo="Alerta" valor={<Anchor size="sm" c="rosa.2" href={x.url_alerta} target="_blank" rel="noopener noreferrer">Abrir o alerta</Anchor>} />}
          </SimpleGrid>
        </div>
      )}
    </Stack>
  );
}

function AbaPrazos({ c }: { c: ChamadoCompleto }) {
  const s = c.sla;
  const acabou = c.status === "resolvido" || c.status === "encerrado";
  const r = s.resposta;
  const percentualDaResposta = r ? (((r.em ? new Date(r.em).getTime() : Date.now()) - new Date(c.aberto_em).getTime()) * 100) / (new Date(r.prazo).getTime() - new Date(c.aberto_em).getTime()) : null;
  return (
    <Stack gap="md">
      {r && <BarraDePrazo rotulo="Primeira resposta" percentual={percentualDaResposta} estado={r.em && r.cumprida ? "ok" : r.estourada ? "tarde" : undefined} texto={r.em ? `${r.cumprida ? "Cumprida" : "Fora do prazo"} em ${quando(r.em)}` : r.estourada ? `Estourada, era até ${quando(r.prazo)}` : `Até ${quando(r.prazo)}`} />}
      {s.prazo && <BarraDePrazo rotulo="Resolução" percentual={s.percentual} estado={s.estourado ? "tarde" : acabou ? "ok" : s.em_risco || s.pausado ? "risco" : undefined} texto={s.pausado ? `Pausado, prazo em ${quando(s.prazo)}` : acabou ? `${s.estourado ? "Fora do prazo" : "Cumprido"}, prazo era ${quando(s.prazo)}` : `Até ${quando(s.prazo)}`} />}
      <SimpleGrid cols={{ base: 2, md: 3 }} spacing="sm">
        <Fato rotulo="Tempo corrido" valor={duracao(s.segundos_corridos)} />
        <Fato rotulo="Tempo em pausa" valor={duracao(s.segundos_pausados)} />
        <Fato rotulo="Até assumir" valor={c.tempos.ate_assumir === null ? "Ninguém assumiu" : duracao(c.tempos.ate_assumir)} />
        {c.resolvido_em && <Fato rotulo="Até resolver, sem pausas" valor={duracao(c.tempos.ate_resolver)} />}
        {c.encerrado_em && <Fato rotulo="Até encerrar" valor={duracao(c.tempos.ate_encerrar)} />}
      </SimpleGrid>
      {c.pausas.length > 0 && (
        <div>
          <Title order={3} mb="xs">Pausas</Title>
          <Stack gap="xs">
            {c.pausas.map((p) => (
              <div key={p.em} className="registro">
                <Text size="sm">{p.motivo || "Sem motivo registrado"}</Text>
                <Text size="xs" c="dimmed">{quando(p.em)}, {p.autor}, {p.segundos === null ? "encerrada pela resposta do cliente" : `${p.em_curso ? "em curso há" : "durou"} ${duracao(p.segundos)}`}</Text>
              </div>
            ))}
          </Stack>
        </div>
      )}
    </Stack>
  );
}

function AbaAnexos({ c, rota, aoMudar }: { c: ChamadoCompleto; rota: string; aoMudar: () => void }) {
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const anexar = async (arquivo: File | null) => {
    if (!arquivo) return;
    setErro(null);
    setEnviando(true);
    const r = await enviarArquivo(`${rota}/anexos`, arquivo).finally(() => setEnviando(false));
    if (!r.ok) {
      setErro(await detalhe(r, "Não foi possível anexar."));
      return;
    }
    aoMudar();
  };
  return (
    <Stack gap="sm">
      {c.status !== "encerrado" && (
        <Group>
          <FileButton onChange={anexar} resetRef={undefined}>
            {(props) => <Button {...props} loading={enviando} leftSection={<IconPaperclip size={14} />}>Anexar arquivo</Button>}
          </FileButton>
          <Text size="xs" c="dimmed">Até 10 MB</Text>
        </Group>
      )}
      {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}
      {!c.anexos.length ? <Vazio>Nenhum anexo.</Vazio> : c.anexos.map((a) => (
        <div key={a.id} className="registro">
          <Anchor size="sm" c="rosa.2" href={`${rota}/anexos/${a.id}`} download={a.nome}>{a.nome}</Anchor>
          <Text size="xs" c="dimmed">{quando(a.criado_em)}, {a.autor}</Text>
        </div>
      ))}
    </Stack>
  );
}

function AbaHistorico({ rota, versao }: { rota: string; versao: number }) {
  const { dados, erro } = useDados<Historico>(`${rota}/historico?v=${versao}`);
  if (!dados) return <Carregando erro={erro} />;
  return (
    <Stack gap="md">
      <Stack gap="xs">
        {dados.eventos.map((e) => (
          <div key={e.sequencia} className="registro">
            <Text size="sm" fw={500}>{EVENTOS[e.tipo] ?? e.tipo}</Text>
            {e.detalhe && <Text size="sm" style={{ overflowWrap: "anywhere" }}>{e.detalhe}</Text>}
            <Text size="xs" c="dimmed">{quando(e.criado_em)}, {e.autor}</Text>
          </div>
        ))}
      </Stack>
      <div>
        <Title order={3} mb="xs">Registros</Title>
        {!dados.interacoes.length ? <Vazio>Nada registrado ainda.</Vazio> : (
          <Stack gap="xs">
            {dados.interacoes.map((i, n) => (
              <div key={n} className="registro">
                <Text size="sm" fw={500}>{i.tipo === "solucao" ? "Solução" : "Andamento"}</Text>
                <Text size="sm" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{semHtml(i.conteudo)}</Text>
                <Text size="xs" c="dimmed">{quando(i.em)}, {i.autor}</Text>
              </div>
            ))}
          </Stack>
        )}
      </div>
    </Stack>
  );
}

export function DetalheDoChamado({ clienteId, numero, aoMudar }: { clienteId: string; numero: number; aoMudar: () => void }) {
  const analista = useAnalista();
  const rota = `/api/v1/tela/clientes/${clienteId}/chamados/${numero}`;
  const [versao, setVersao] = useState(0);
  const { dados: c, erro: erroDeLeitura, recarregar } = useDados<ChamadoCompleto>(rota);
  const [formulario, setFormulario] = useState<Formulario>(null);
  const [escalando, setEscalando] = useState(false);
  const [classificando, setClassificando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [aba, setAba] = useState<string | null>("resumo");

  if (!c) return <Carregando erro={erroDeLeitura} />;

  const atualizar = () => { recarregar(); setVersao((v) => v + 1); aoMudar(); };
  const executar = async (acao: string, corpo?: Record<string, string>) => {
    const r = await enviar(`${rota}/${acao}`, corpo);
    if (!r.ok) return detalhe(r);
    atualizar();
    return null;
  };
  const direto = async (acao: string) => { setErro(await executar(acao)); };

  const andamento: Formulario = { titulo: "Registrar andamento", rotulo: "O que foi feito", acao: "andamento", botao: "Registrar", campo: "texto" };
  const emAberto = c.status === "aberto" || (c.status === "em_andamento" && !c.atendente);
  const emAtendimento = c.status === "em_andamento" && !!c.atendente;
  const podeEscalar = analista && ["aberto", "em_andamento", "aguardando_cliente"].includes(c.status) && c.nivel !== "N3";
  const podeClassificar = analista && c.status !== "encerrado";

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
        <div style={{ minWidth: 0, flex: "1 1 240px" }}>
          <Text size="xs" c="dimmed">{c.cliente}</Text>
          <Title order={2} style={{ overflowWrap: "anywhere" }} data-testid="titulo-chamado">{c.codigo}: {c.titulo}</Title>
          <Group gap="xs" mt={4} wrap="wrap"><Criticidade valor={c.criticidade} /><Situacao valor={c.status} /><Nivel valor={c.nivel} /></Group>
        </div>
        <Group gap="xs">
          {analista && emAberto && <Button variant="filled" onClick={() => direto("assumir")}>Assumir atendimento</Button>}
          {analista && emAtendimento && <Button variant="filled" onClick={() => setFormulario({ titulo: "Resolver", rotulo: "Solução adotada", acao: "resolver", botao: "Resolver", campo: "texto" })}>Resolver</Button>}
          {analista && emAtendimento && <Button onClick={() => setFormulario(andamento)}>Registrar andamento</Button>}
          {analista && emAtendimento && <Button onClick={() => setFormulario({ titulo: "Aguardar cliente", rotulo: "O que falta do cliente", acao: "pausar", botao: "Pausar o prazo", campo: "texto" })}>Aguardar cliente</Button>}
          {analista && c.status === "aguardando_cliente" && <Button variant="filled" onClick={() => direto("retomar")}>Retomar</Button>}
          {analista && c.status === "aguardando_cliente" && <Button onClick={() => setFormulario(andamento)}>Registrar andamento</Button>}
          {analista && c.status === "resolvido" && <Button variant="filled" onClick={() => direto("encerrar")}>Encerrar</Button>}
          {!analista && ["aberto", "em_andamento", "aguardando_cliente"].includes(c.status) && <Button variant="filled" onClick={() => setFormulario({ titulo: "Escrever para o atendimento", rotulo: "Mensagem", acao: "comentar", botao: "Enviar", campo: "texto" })}>Escrever</Button>}
          {(podeEscalar || podeClassificar) && (
            <Menu position="bottom-end" shadow="md">
              <Menu.Target><ActionIcon variant="default" size={36} aria-label="Mais ações"><IconDots size={16} /></ActionIcon></Menu.Target>
              <Menu.Dropdown>
                {podeEscalar && <Menu.Item fz="sm" onClick={() => setEscalando(true)}>Escalar</Menu.Item>}
                {podeClassificar && <Menu.Item fz="sm" onClick={() => setClassificando(true)}>Classificar</Menu.Item>}
              </Menu.Dropdown>
            </Menu>
          )}
        </Group>
      </Group>
      {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}

      <Tabs value={aba} onChange={setAba}>
        <Tabs.List mb="md">
          <Tabs.Tab value="resumo">Resumo</Tabs.Tab>
          <Tabs.Tab value="prazos">Prazos</Tabs.Tab>
          <Tabs.Tab value="anexos">Anexos{c.anexos.length ? ` (${c.anexos.length})` : ""}</Tabs.Tab>
          <Tabs.Tab value="historico">Histórico</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="resumo"><AbaResumo c={c} /></Tabs.Panel>
        <Tabs.Panel value="prazos"><AbaPrazos c={c} /></Tabs.Panel>
        <Tabs.Panel value="anexos"><AbaAnexos c={c} rota={rota} aoMudar={atualizar} /></Tabs.Panel>
        <Tabs.Panel value="historico"><AbaHistorico rota={rota} versao={versao} /></Tabs.Panel>
      </Tabs>

      <ModalDeTexto formulario={formulario} aoEnviar={executar} aoFechar={() => setFormulario(null)} />
      <ModalDeEscalar key={c.nivel} aberto={escalando} nivelAtual={c.nivel} aoEnviar={executar} aoFechar={() => setEscalando(false)} />
      {classificando && <ModalDeClassificar aberto c={c} aoEnviar={executar} aoFechar={() => setClassificando(false)} />}
    </Stack>
  );
}
