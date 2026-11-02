import { ActionIcon, Badge, Card, Center, Group, Loader, Popover, Text, TextInput, Title } from "@mantine/core";
import { IconHelp, IconSearch } from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";

import { CRITICIDADES, quando, SITUACOES } from "./formato";

/** Título, ajuda sob demanda e, à direita, no máximo uma ação principal. */
export function Cabecalho({ titulo, ajuda, children }: { titulo: string; ajuda: string; children?: ReactNode }) {
  return (
    <Group justify="space-between" mb="md" wrap="wrap" gap="sm">
      <Group gap={6}>
        <Title order={1}>{titulo}</Title>
        <Popover width={320} position="bottom-start" withArrow shadow="md">
          <Popover.Target>
            <ActionIcon variant="subtle" color="gray" size="sm" aria-label={`O que é ${titulo}`}><IconHelp size={16} /></ActionIcon>
          </Popover.Target>
          <Popover.Dropdown><Text size="sm">{ajuda}</Text></Popover.Dropdown>
        </Popover>
      </Group>
      <Group gap="xs">{children}</Group>
    </Group>
  );
}

export function Painel({ children, p }: { children: ReactNode; p?: string | number }) {
  return <Card p={p ?? "md"}>{children}</Card>;
}

export function Numero({ rotulo, valor, aoClicar, selecionado }: { rotulo: string; valor: ReactNode; aoClicar?: () => void; selecionado?: boolean }) {
  return (
    <Card p="sm" className={aoClicar ? "clicavel" : undefined} data-selecionado={selecionado || undefined} {...(aoClicar ? { component: "button" as const, type: "button" as const, onClick: aoClicar, "aria-pressed": selecionado } : {})}>
      <Text size="xs" c="dimmed">{rotulo}</Text>
      <Text fz={24} fw={600} lh="32px" className="numero">{valor}</Text>
    </Card>
  );
}

const CRITICIDADE: Record<string, { icone: string; cor: string }> = {
  critica: { icone: "▲", cor: "#ff5a52" },
  alta: { icone: "◆", cor: "#ff9f43" },
  media: { icone: "●", cor: "#ffd24d" },
  baixa: { icone: "○", cor: "#6db3f2" },
  consulta: { icone: "◇", cor: "#9b9ba3" },
};

/** Criticidade nunca é só cor: leva também forma e texto. */
export function Criticidade({ valor }: { valor: string }) {
  const c = CRITICIDADE[valor] ?? CRITICIDADE.consulta;
  return <Text span size="xs" fw={500} style={{ color: c.cor, whiteSpace: "nowrap" }}>{c.icone} {CRITICIDADES[valor] ?? valor}</Text>;
}

const CORES_DE_SITUACAO: Record<string, string> = { aberto: "gray", em_andamento: "blue", aguardando_cliente: "yellow", resolvido: "green", encerrado: "green" };

export function Situacao({ valor }: { valor: string }) {
  return <Badge variant="light" size="sm" radius="sm" color={CORES_DE_SITUACAO[valor] ?? "gray"} tt="none" fw={500}>{SITUACOES[valor] ?? valor}</Badge>;
}

export function Nivel({ valor }: { valor: string }) {
  return <Badge variant="outline" size="sm" radius="sm" color="rosa.3" tt="none" fw={500} data-testid="nivel">{valor}</Badge>;
}

/** Data e hora nunca quebram em duas linhas. */
export function Data({ valor }: { valor: string | null | undefined }) {
  return <Text span size="sm" c="dimmed" className="numero" style={{ whiteSpace: "nowrap" }}>{quando(valor)}</Text>;
}

export function Vazio({ children }: { children: ReactNode }) {
  return <Center py="xl"><Text size="sm" c="dimmed">{children}</Text></Center>;
}

export function Carregando({ erro }: { erro?: string | null }) {
  if (erro) return <Vazio>{erro}</Vazio>;
  return <Center py="xl"><Loader size="sm" color="gray" /></Center>;
}

/** Busca compacta: aplica sozinha, um instante depois de parar de digitar. */
export function Busca({ valor, aoMudar, dica }: { valor: string; aoMudar: (v: string) => void; dica: string }) {
  const [texto, setTexto] = useState(valor);
  useEffect(() => {
    const t = setTimeout(() => { if (texto !== valor) aoMudar(texto); }, 300);
    return () => clearTimeout(t);
  }, [texto, valor, aoMudar]);
  return <TextInput w={280} maw="100%" leftSection={<IconSearch size={14} />} placeholder={dica} aria-label={dica} value={texto} onChange={(e) => setTexto(e.currentTarget.value)} type="search" />;
}

export function Fato({ rotulo, valor }: { rotulo: string; valor: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">{rotulo}</Text>
      <Text size="sm" component="div">{valor || "-"}</Text>
    </div>
  );
}

/** Barra fina metálica de prazo: a cor diz como ele está. */
export function BarraDePrazo({ rotulo, percentual, estado, texto }: { rotulo: string; percentual: number | null; estado?: "risco" | "tarde" | "ok"; texto: string }) {
  return (
    <div className="barra-prazo">
      <Group justify="space-between" gap="xs" mb={4} wrap="wrap">
        <Text size="sm" fw={500}>{rotulo}</Text>
        <Text size="xs" c="dimmed">{texto}</Text>
      </Group>
      <span className="barra-trilho"><span className="barra-cheia" data-estado={estado} style={{ width: `${Math.min(100, Math.max(1, percentual ?? 0))}%` }} /></span>
    </div>
  );
}
