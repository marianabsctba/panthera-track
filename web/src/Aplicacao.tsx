import { Alert, AppShell, Avatar, Box, Burger, Button, Center, Group, Loader, Menu, Modal, PasswordInput, ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconChevronDown, IconKey, IconListCheck, IconLogout } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { ContextoEu, detalhe, enviar, ler, type Eu } from "./api";
import { Entrada } from "./Entrada";
import { FilaDeChamados } from "./telas/Fila";

function TrocaDeSenha({ aberta, aoFechar }: { aberta: boolean; aoFechar: () => void }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const fechar = () => { setAtual(""); setNova(""); setErro(null); aoFechar(); };
  const trocar = async () => {
    setErro(null);
    const r = await enviar("/api/v1/sessao/senha", { senha_atual: atual, senha_nova: nova });
    if (!r.ok) {
      setErro(await detalhe(r, "A nova senha precisa de pelo menos 12 caracteres."));
      return;
    }
    fechar();
  };
  return (
    <Modal opened={aberta} onClose={fechar} title="Trocar senha">
      <Stack gap="sm">
        <PasswordInput label="Senha atual" autoComplete="current-password" value={atual} onChange={(e) => setAtual(e.currentTarget.value)} data-autofocus />
        <PasswordInput label="Nova senha" description="Pelo menos 12 caracteres" autoComplete="new-password" value={nova} onChange={(e) => setNova(e.currentTarget.value)} />
        {erro && <Alert color="red" variant="light" p="xs" fz="sm">{erro}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={fechar}>Cancelar</Button>
          <Button variant="filled" onClick={trocar} disabled={!atual || nova.length < 12}>Trocar senha</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function Aplicacao() {
  const [eu, setEu] = useState<Eu | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [verSenha, setVerSenha] = useState(false);
  const [aberto, { toggle }] = useDisclosure();

  useEffect(() => {
    const semSessao = () => setEu(null);
    window.addEventListener("track:sem-sessao", semSessao);
    ler<Eu>("/api/v1/sessao").then(setEu).catch(() => setEu(null)).finally(() => setCarregando(false));
    return () => window.removeEventListener("track:sem-sessao", semSessao);
  }, []);

  if (carregando) return <Center h="100%"><Loader color="gray" size="sm" /></Center>;
  if (!eu || eu.etapa !== "pronto") return <Entrada eu={eu} aoSeguir={setEu} />;

  const sair = async () => {
    await enviar("/api/v1/sessao", undefined, "DELETE").catch(() => {});
    setEu(null);
  };

  return (
    <ContextoEu.Provider value={eu}>
      <AppShell header={{ height: 48 }} navbar={{ width: 224, breakpoint: "sm", collapsed: { mobile: !aberto } }} padding="lg">
        <AppShell.Header className="barra-metal">
          <Group h="100%" px="md" justify="space-between" wrap="nowrap">
            <Group gap="sm" wrap="nowrap">
              <Burger opened={aberto} onClick={toggle} hiddenFrom="sm" size="sm" color="white" aria-label="Abrir o menu" />
              <Avatar src="/estatico/pantera.webp" size={24} radius="sm" alt="" />
              <Text fw={600} size="sm">Panthera Track</Text>
            </Group>
            <Menu position="bottom-end" shadow="md">
              <Menu.Target>
                <UnstyledButton aria-label="Menu da conta">
                  <Group gap={6} wrap="nowrap">
                    <Avatar size={24} radius="xl" color="dark" variant="filled" name={eu.nome} />
                    <Text size="sm" visibleFrom="sm">{eu.nome}</Text>
                    <IconChevronDown size={14} />
                  </Group>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item fz="sm" leftSection={<IconKey size={14} />} onClick={() => setVerSenha(true)}>Trocar senha</Menu.Item>
                <Menu.Divider />
                <Menu.Item fz="sm" leftSection={<IconLogout size={14} />} onClick={sair}>Sair</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </AppShell.Header>

        <AppShell.Navbar style={{ background: "var(--mantine-color-dark-7)", borderColor: "var(--track-borda)" }}>
          <ScrollArea h="100%" px={8} pb={8} component="nav" aria-label="Seções">
            <Box>
              <div className="menu-grupo">Atender</div>
              <button type="button" className="menu-item" aria-current="page">
                <IconListCheck size={16} stroke={1.6} />
                <span>Fila de chamados</span>
              </button>
            </Box>
          </ScrollArea>
        </AppShell.Navbar>

        <TrocaDeSenha aberta={verSenha} aoFechar={() => setVerSenha(false)} />
        <AppShell.Main style={{ background: "var(--mantine-color-dark-8)" }}>
          <Box maw={1400} mx="auto"><FilaDeChamados /></Box>
        </AppShell.Main>
      </AppShell>
    </ContextoEu.Provider>
  );
}
