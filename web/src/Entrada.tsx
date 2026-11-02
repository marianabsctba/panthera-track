import { Alert, Button, Card, Center, Code, Image, PasswordInput, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { detalhe, enviar, ler, type Eu } from "./api";

function Moldura({ children, aoEnviar }: { children: ReactNode; aoEnviar: (e: FormEvent<HTMLFormElement>) => void }) {
  return (
    <Center mih="100%" p="md" style={{ background: "radial-gradient(circle at 50% 0%, #2a0a1d 0%, var(--mantine-color-dark-8) 55%)" }}>
      <Card w={380} maw="100%" p="xl">
        <form onSubmit={aoEnviar} noValidate>
          <Stack gap="md">
            <Stack gap={6} align="center">
              <Image src="/estatico/pantera.webp" alt="" w={64} h={64} radius="md" />
              <Title order={1}>Panthera Track</Title>
            </Stack>
            {children}
          </Stack>
        </form>
      </Card>
    </Center>
  );
}

function Erro({ texto }: { texto: string | null }) {
  return texto ? <Alert color="red" variant="light" p="xs" role="alert"><Text size="sm">{texto}</Text></Alert> : null;
}

function useEnvio(rota: string, aoConcluir: (d: Eu) => void, padrao: string) {
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const mandar = async (corpo: unknown) => {
    setErro(null);
    setEnviando(true);
    try {
      const r = await enviar(rota, corpo);
      if (!r.ok) {
        setErro(await detalhe(r, padrao));
        return;
      }
      aoConcluir(await r.json());
    } finally {
      setEnviando(false);
    }
  };
  return { erro, enviando, mandar };
}

function FormularioDeEntrada({ aoSeguir }: { aoSeguir: (d: Eu) => void }) {
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const { erro, enviando, mandar } = useEnvio("/api/v1/sessao", aoSeguir, "Não foi possível entrar.");
  return (
    <Moldura aoEnviar={(e) => { e.preventDefault(); mandar({ email, senha }); setSenha(""); }}>
      <TextInput label="E-mail" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.currentTarget.value)} autoFocus />
      <PasswordInput label="Senha" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.currentTarget.value)} />
      <Erro texto={erro} />
      <Button type="submit" variant="filled" loading={enviando} disabled={!email.trim() || !senha} fullWidth>Entrar</Button>
    </Moldura>
  );
}

function FormularioDeSenha({ aoSeguir }: { aoSeguir: (d: Eu) => void }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const { erro, enviando, mandar } = useEnvio("/api/v1/sessao/senha", aoSeguir, "A nova senha precisa de pelo menos 12 caracteres.");
  return (
    <Moldura aoEnviar={(e) => { e.preventDefault(); mandar({ senha_atual: atual, senha_nova: nova }); }}>
      <Text size="sm" c="dimmed" ta="center">Troque a senha provisória.</Text>
      <PasswordInput label="Senha provisória" autoComplete="current-password" value={atual} onChange={(e) => setAtual(e.currentTarget.value)} autoFocus />
      <PasswordInput label="Nova senha" description="Pelo menos 12 caracteres" autoComplete="new-password" value={nova} onChange={(e) => setNova(e.currentTarget.value)} />
      <Erro texto={erro} />
      <Button type="submit" variant="filled" loading={enviando} disabled={!atual || nova.length < 12} fullWidth>Trocar senha</Button>
    </Moldura>
  );
}

function CadastroDoAutenticador({ aoSeguir }: { aoSeguir: (d: Eu) => void }) {
  const [codigo, setCodigo] = useState("");
  const [qr, setQr] = useState<{ qr: string; segredo: string } | null>(null);
  const { erro, enviando, mandar } = useEnvio("/api/v1/sessao/mfa/ativar", aoSeguir, "Código incorreto. Confira o horário do celular.");
  useEffect(() => {
    let valendo = true;
    ler<{ qr: string; segredo: string }>("/api/v1/sessao/mfa").then((d) => { if (valendo) setQr(d); }).catch(() => {});
    return () => { valendo = false; };
  }, []);
  return (
    <Moldura aoEnviar={(e) => { e.preventDefault(); mandar({ codigo }); setCodigo(""); }}>
      <Text size="sm" c="dimmed" ta="center">Leia o código no seu aplicativo autenticador.</Text>
      {qr && <Image src={qr.qr} alt="Código para o aplicativo autenticador" w={200} h={200} mx="auto" radius="sm" bg="white" data-testid="qr" />}
      {qr && <Code block data-testid="segredo" style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>{qr.segredo}</Code>}
      <TextInput label="Código de 6 números" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={codigo} onChange={(e) => setCodigo(e.currentTarget.value)} />
      <Erro texto={erro} />
      <Button type="submit" variant="filled" loading={enviando} disabled={codigo.length !== 6} fullWidth>Ativar</Button>
    </Moldura>
  );
}

function CodigosDeRecuperacao({ codigos, aoSeguir }: { codigos: string[]; aoSeguir: () => void }) {
  return (
    <Moldura aoEnviar={(e) => { e.preventDefault(); aoSeguir(); }}>
      <Alert color="yellow" variant="light" p="xs"><Text size="sm">Guarde estes códigos. Eles aparecem só agora e cada um vale uma vez.</Text></Alert>
      <SimpleGrid cols={2} spacing={4}>{codigos.map((c) => <Text key={c} className="mono" ta="center">{c}</Text>)}</SimpleGrid>
      <Button type="submit" variant="filled" fullWidth>Já guardei</Button>
    </Moldura>
  );
}

function ConfirmacaoDoCodigo({ aoSeguir }: { aoSeguir: (d: Eu) => void }) {
  const [codigo, setCodigo] = useState("");
  const { erro, enviando, mandar } = useEnvio("/api/v1/sessao/mfa", aoSeguir, "Código incorreto.");
  return (
    <Moldura aoEnviar={(e) => { e.preventDefault(); mandar({ codigo }); setCodigo(""); }}>
      <TextInput label="Código do autenticador ou de recuperação" autoComplete="one-time-code" maxLength={32} value={codigo} onChange={(e) => setCodigo(e.currentTarget.value)} autoFocus />
      <Erro texto={erro} />
      <Button type="submit" variant="filled" loading={enviando} disabled={!codigo.trim()} fullWidth>Confirmar</Button>
    </Moldura>
  );
}

/** Cada etapa é decidida pela API, e a sessão só vale de verdade depois do segundo fator. */
export function Entrada({ eu, aoSeguir }: { eu: Eu | null; aoSeguir: (d: Eu) => void }) {
  const [codigos, setCodigos] = useState<string[] | null>(null);
  const [concluido, setConcluido] = useState<Eu | null>(null);
  if (codigos && concluido) return <CodigosDeRecuperacao codigos={codigos} aoSeguir={() => aoSeguir(concluido)} />;
  if (!eu) return <FormularioDeEntrada aoSeguir={aoSeguir} />;
  if (eu.etapa === "trocar_senha") return <FormularioDeSenha aoSeguir={aoSeguir} />;
  if (eu.etapa === "cadastrar_mfa") return <CadastroDoAutenticador aoSeguir={(d) => { setConcluido(d); setCodigos(d.codigos_recuperacao ?? []); }} />;
  return <ConfirmacaoDoCodigo aoSeguir={aoSeguir} />;
}
