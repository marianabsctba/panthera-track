import { ActionIcon, Alert, Badge, Box, Button, Drawer, Group, Modal, ScrollArea, Select, SimpleGrid, Stack, Text, Textarea, TextInput, Tooltip, UnstyledButton } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import { detalhe, enviar, useAnalista, useDados, useEu, type Chamado, type Fila } from "../api";
import { Busca, Cabecalho, Carregando, Criticidade, Nivel, Numero, Painel, Situacao, Vazio } from "../componentes";
import { CATEGORIAS, CRITICIDADES, duracao, numero, opcoes, ORIGENS, quando, semHtml, SITUACOES, TIPOS } from "../formato";
import { DetalheDoChamado } from "./Chamado";

function textoDoSla(c: Chamado): { texto: string; classe?: string } {
  const s = c.sla;
  if (s.segundos_restantes === null) return { texto: "Sem prazo" };
  if (c.status === "resolvido" || c.status === "encerrado") return s.estourado ? { texto: `Fora do prazo, ${duracao(s.segundos_corridos)}`, classe: "tarde" } : { texto: `No prazo, ${duracao(s.segundos_corridos)}` };
  if (s.pausado) return { texto: `Pausado, restam ${duracao(s.segundos_restantes)}`, classe: "pausa" };
  if (s.estourado) return { texto: `Estourado há ${duracao(s.segundos_restantes)}`, classe: "tarde" };
  return s.em_risco ? { texto: `Em risco, restam ${duracao(s.segundos_restantes)}`, classe: "risco" } : { texto: `Restam ${duracao(s.segundos_restantes)}` };
}

function ItemDaFila({ c, atual, mostrarCliente, aoClicar }: { c: Chamado; atual: boolean; mostrarCliente: boolean; aoClicar: () => void }) {
  const sla = textoDoSla(c);
  return (
    <UnstyledButton className="item-fila clicavel" data-selecionado={atual || undefined} data-testid="item-fila" aria-current={atual || undefined} onClick={aoClicar}>
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Text size="sm" fw={600} className="numero" data-testid="codigo">{c.codigo}</Text>
        <Criticidade valor={c.criticidade} />
      </Group>
      <Text size="sm" lineClamp={2}>{c.titulo}</Text>
      <Group gap={6} mt={4} wrap="wrap">
        <Situacao valor={c.status} />
        {c.nivel !== "N1" && <Nivel valor={c.nivel} />}
        {c.aceite_pendente && <Badge variant="outline" size="sm" radius="sm" color="yellow" tt="none" fw={500}>Aguardando aceite</Badge>}
        <Text size="xs" className={`sla ${sla.classe ?? ""}`}>{sla.texto}</Text>
      </Group>
      <Text size="xs" c="dimmed" mt={2}>{mostrarCliente ? `${c.cliente}, ` : ""}{ORIGENS[c.origem] ?? c.origem}, {quando(c.aberto_em)}</Text>
    </UnstyledButton>
  );
}

function NovoChamado({ aberto, fila, aoFechar, aoCriar }: { aberto: boolean; fila: Fila; aoFechar: () => void; aoCriar: (c: Chamado) => void }) {
  const eu = useEu();
  const analista = useAnalista();
  const vazio = { cliente_id: fila.clientes[0]?.id ?? "", solicitante: "", titulo: "", descricao: "", tipo: "incidente", categoria: "outro", criticidade: "media", ativo: "", contato_telefone: "", contato_email: analista ? "" : eu.email };
  const [f, setF] = useState(vazio);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const muda = (campo: keyof typeof vazio) => (valor: string | null) => setF((a) => ({ ...a, [campo]: valor ?? "" }));

  useEffect(() => { if (aberto) { setF(vazio); setErro(null); } }, [aberto]); // eslint-disable-line react-hooks/exhaustive-deps

  const abrir = async () => {
    setErro(null);
    setEnviando(true);
    const corpo = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== "" && (analista || !["cliente_id", "solicitante"].includes(k))));
    const r = await enviar("/api/v1/tela/chamados", corpo).finally(() => setEnviando(false));
    if (!r.ok) {
      setErro(await detalhe(r, "Confira os campos e tente de novo."));
      return;
    }
    aoCriar(await r.json());
  };

  return (
    <Modal opened={aberto} onClose={aoFechar} title={analista ? "Registrar chamado por telefone" : "Novo chamado"} size="lg">
      <Stack gap="sm">
        {analista && (
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
            <Select label="Cliente" data={fila.clientes.map((c) => ({ value: c.id, label: c.nome }))} value={f.cliente_id} onChange={muda("cliente_id")} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
            <TextInput label="Quem pediu" required maxLength={255} value={f.solicitante} onChange={(e) => muda("solicitante")(e.currentTarget.value)} />
          </SimpleGrid>
        )}
        <TextInput label="Título" required maxLength={255} value={f.titulo} onChange={(e) => muda("titulo")(e.currentTarget.value)} data-autofocus />
        <Textarea label="Descrição" required autosize minRows={3} maxRows={8} value={f.descricao} onChange={(e) => muda("descricao")(e.currentTarget.value)} />
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
          <Select label="Tipo" data={opcoes(TIPOS)} value={f.tipo} onChange={muda("tipo")} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
          <Select label="Categoria" data={opcoes(CATEGORIAS)} value={f.categoria} onChange={muda("categoria")} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
          <Select label="Criticidade" data={opcoes(CRITICIDADES)} value={f.criticidade} onChange={muda("criticidade")} allowDeselect={false} comboboxProps={{ withinPortal: false }} />
        </SimpleGrid>
        <TextInput label="Ativo ou local afetado" maxLength={255} value={f.ativo} onChange={(e) => muda("ativo")(e.currentTarget.value)} />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput label="Telefone para contato" type="tel" maxLength={20} value={f.contato_telefone} onChange={(e) => muda("contato_telefone")(e.currentTarget.value)} />
          <TextInput label="E-mail para contato" type="email" maxLength={320} value={f.contato_email} onChange={(e) => muda("contato_email")(e.currentTarget.value)} />
        </SimpleGrid>
        {erro && <Alert color="red" variant="light" p="xs" fz="sm" role="alert">{erro}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={aoFechar}>Cancelar</Button>
          <Button variant="filled" loading={enviando} onClick={abrir} disabled={!f.titulo.trim() || !f.descricao.trim() || (analista && !f.solicitante.trim())}>Abrir chamado</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function FilaDeChamados() {
  const analista = useAnalista();
  const largo = useMediaQuery("(min-width: 75em)", true);
  const { dados, erro, recarregar } = useDados<Fila>("/api/v1/tela/chamados");
  const [situacao, setSituacao] = useState("");
  const [criticidade, setCriticidade] = useState("");
  const [cliente, setCliente] = useState("");
  const [busca, setBusca] = useState("");
  const [selecionado, setSelecionado] = useState<{ cliente_id: string; numero: number } | null>(null);
  const [novo, setNovo] = useState(false);

  useEffect(() => {
    const t = setInterval(recarregar, 20000);
    return () => clearInterval(t);
  }, [recarregar]);

  const chamados = dados?.chamados ?? [];
  const visiveis = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    return chamados.filter((c) => (!situacao || c.status === situacao) && (!criticidade || c.criticidade === criticidade) && (!cliente || c.cliente_id === cliente)
      && (!termo || [c.codigo, c.titulo, c.solicitante, c.cliente, c.atendente ?? "", semHtml(c.descricao)].some((t) => t.toLowerCase().includes(termo))));
  }, [chamados, situacao, criticidade, cliente, busca]);

  const cabecalho = (
    <Cabecalho titulo="Fila de chamados" ajuda="Na frente, o que está mais perto de estourar o prazo. O que já acabou fica no fim.">
      <Tooltip label="Atualizar"><ActionIcon variant="subtle" color="gray" onClick={recarregar} aria-label="Atualizar a fila"><IconRefresh size={16} /></ActionIcon></Tooltip>
      {dados && <Button variant="filled" onClick={() => setNovo(true)}>{analista ? "Registrar chamado" : "Novo chamado"}</Button>}
    </Cabecalho>
  );
  if (!dados) return <>{cabecalho}<Carregando erro={erro} /></>;

  const detalheAberto = selecionado && <DetalheDoChamado key={`${selecionado.cliente_id}-${selecionado.numero}`} clienteId={selecionado.cliente_id} numero={selecionado.numero} aoMudar={recarregar} />;

  return (
    <>
      {cabecalho}
      <Stack gap="sm">
        {dados.indisponiveis.length > 0 && <Alert color="yellow" variant="light" p="xs" fz="sm">Sem resposta agora: {dados.indisponiveis.join(", ")}</Alert>}
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm">
          {Object.entries(SITUACOES).map(([valor, rotulo]) => (
            <Numero key={valor} rotulo={rotulo} valor={numero(chamados.filter((c) => c.status === valor).length)} selecionado={situacao === valor} aoClicar={() => setSituacao(situacao === valor ? "" : valor)} />
          ))}
        </SimpleGrid>
        <Group gap="xs" wrap="wrap">
          <Busca valor={busca} aoMudar={setBusca} dica="Filtrar a lista" />
          <Select w={170} aria-label="Criticidade" value={criticidade || "todas"} onChange={(v) => setCriticidade(!v || v === "todas" ? "" : v)} allowDeselect={false} data={[{ value: "todas", label: "Toda criticidade" }, ...opcoes(CRITICIDADES)]} />
          {analista && dados.clientes.length > 1 && <Select w={190} aria-label="Cliente" value={cliente || "todos"} onChange={(v) => setCliente(!v || v === "todos" ? "" : v)} allowDeselect={false} data={[{ value: "todos", label: "Todos os clientes" }, ...dados.clientes.map((c) => ({ value: c.id, label: c.nome }))]} />}
        </Group>
        <Box style={largo ? { display: "grid", gridTemplateColumns: "minmax(320px, 420px) minmax(0, 1fr)", gap: 12, alignItems: "start" } : undefined}>
          <Painel p="xs">
            <ScrollArea.Autosize mah={largo ? "calc(100vh - 290px)" : undefined} type="auto" offsetScrollbars>
              <Stack gap={4}>
                {!visiveis.length && <Vazio>Nenhum chamado aqui.</Vazio>}
                {visiveis.map((c) => (
                  <ItemDaFila key={`${c.cliente_id}-${c.numero}`} c={c} mostrarCliente={analista} atual={selecionado?.cliente_id === c.cliente_id && selecionado.numero === c.numero} aoClicar={() => setSelecionado({ cliente_id: c.cliente_id, numero: c.numero })} />
                ))}
              </Stack>
            </ScrollArea.Autosize>
          </Painel>
          {largo && (
            <Painel>
              <ScrollArea.Autosize mah="calc(100vh - 255px)" type="auto" offsetScrollbars>
                {detalheAberto || <Vazio>Escolha um chamado.</Vazio>}
              </ScrollArea.Autosize>
            </Painel>
          )}
        </Box>
      </Stack>
      {!largo && <Drawer opened={selecionado !== null} onClose={() => setSelecionado(null)} title="Chamado">{detalheAberto}</Drawer>}
      <NovoChamado aberto={novo} fila={dados} aoFechar={() => setNovo(false)} aoCriar={(c) => { setNovo(false); recarregar(); setSelecionado({ cliente_id: c.cliente_id, numero: c.numero }); }} />
    </>
  );
}
