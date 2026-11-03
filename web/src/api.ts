import { createContext, useCallback, useContext, useEffect, useState } from "react";

export interface Eu {
  etapa: "trocar_senha" | "cadastrar_mfa" | "codigo_mfa" | "pronto";
  nome: string;
  email: string;
  tipo: "analista" | "cliente";
  papel: string | null;
  codigos_recuperacao?: string[];
}

export const ContextoEu = createContext<Eu | null>(null);
export const useEu = () => useContext(ContextoEu)!;
// Esconder é só conforto: quem decide é a API, em cada rota.
export const useAnalista = () => useEu().tipo === "analista";

async function pedir(rota: string, opcoes: RequestInit = {}) {
  const r = await fetch(rota, { credentials: "same-origin", ...opcoes, headers: { "X-Track-Tela": "1", ...opcoes.headers } });
  if (r.status === 401 && !rota.startsWith("/api/v1/sessao")) {
    window.dispatchEvent(new Event("track:sem-sessao"));
    throw new Error("sem sessão");
  }
  return r;
}

export async function detalhe(r: Response, padrao = "Não foi possível concluir. Tente de novo.") {
  const corpo = await r.json().catch(() => ({}));
  return typeof corpo.detail === "string" ? corpo.detail : padrao;
}

export async function ler<T>(rota: string): Promise<T> {
  const r = await pedir(rota);
  if (!r.ok) throw new Error(await detalhe(r));
  return r.json();
}

export async function enviar(rota: string, corpo?: unknown, metodo = "POST") {
  return pedir(rota, { method: metodo, ...(corpo === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) }) });
}

export async function enviarArquivo(rota: string, arquivo: File) {
  const dados = new FormData();
  dados.append("arquivo", arquivo);
  return pedir(rota, { method: "POST", body: dados });
}

export function useDados<T>(rota: string | null) {
  const [dados, setDados] = useState<T | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [versao, setVersao] = useState(0);
  const recarregar = useCallback(() => setVersao((v) => v + 1), []);
  useEffect(() => {
    if (rota === null) return;
    let valendo = true;
    ler<T>(rota)
      .then((d) => { if (valendo) { setDados(d); setErro(null); } })
      .catch((e: Error) => { if (valendo) setErro(e.message); });
    return () => { valendo = false; };
  }, [rota, versao]);
  return { dados, erro, recarregar };
}

export interface Resposta { prazo: string; em: string | null; segundos_restantes: number; cumprida: boolean | null; estourada: boolean }
export interface Sla { prazo: string | null; segundos_corridos: number; segundos_pausados: number; segundos_restantes: number | null; percentual: number | null; pausado: boolean; estourado: boolean | null; em_risco: boolean; resposta: Resposta | null }
export interface Aceite { id: string; tipo: "criticidade" | "encerramento"; criticidade: string | null; motivo: string; pedido_por: string; pedido_em: string; estado: "pendente" | "aceito" | "recusado"; decidido_por: string | null; decidido_em: string | null; comentario: string | null }
export interface Chamado {
  aceite_pendente?: boolean;
  numero: number; codigo: string; cliente_id: string; cliente: string; origem: string; tipo: string; categoria: string | null; titulo: string; descricao: string;
  criticidade: string; status: string; nivel: string; solicitante: string; atendente: string | null; contexto: Record<string, string>;
  aberto_em: string; resolvido_em: string | null; encerrado_em: string | null; sla: Sla;
}
export interface Pausa { motivo: string | null; autor: string; em: string; segundos: number | null; em_curso: boolean }
export interface Anexo { id: number; nome: string; criado_em: string; sha1: string | null; autor: string }
export interface ChamadoCompleto extends Chamado {
  contato: { telefone: string | null; email: string | null };
  tempos: { ate_assumir: number | null; aguardando_cliente: number; ate_resolver: number; ate_encerrar: number };
  pausas: Pausa[];
  aceites: Aceite[];
  anexos: Anexo[];
}
export interface Historico {
  eventos: { sequencia: number; tipo: string; autor: string; criado_em: string; detalhe: string | null }[];
  interacoes: { tipo: string; conteudo: string | null; autor: string; em: string | null }[];
}
export interface Fila { chamados: Chamado[]; indisponiveis: string[]; clientes: { id: string; nome: string }[] }
