export const numero = (v: number) => v.toLocaleString("pt-BR");

export function quando(valor: string | null | undefined) {
  if (!valor) return "-";
  return new Date(valor).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function duracao(segundos: number) {
  const n = Math.abs(segundos);
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}min` : `${m}min ${String(n % 60).padStart(2, "0")}s`;
}

/** O conteúdo vem do motor como HTML escapado: aqui ele só vira texto, nunca marcação. */
export function semHtml(texto: string | null | undefined) {
  const decodificar = (s: string) => new DOMParser().parseFromString(s, "text/html").body.textContent ?? "";
  return decodificar(decodificar(texto ?? ""));
}

export const SITUACOES: Record<string, string> = { aberto: "Aberto", em_andamento: "Em andamento", aguardando_cliente: "Aguardando cliente", resolvido: "Resolvido", encerrado: "Encerrado" };
export const CRITICIDADES: Record<string, string> = { critica: "Crítica", alta: "Alta", media: "Média", baixa: "Baixa", consulta: "Consulta" };
export const TIPOS: Record<string, string> = { incidente: "Incidente", requisicao: "Requisição" };
export const CATEGORIAS: Record<string, string> = { acesso: "Acesso", malware: "Malware", rede: "Rede", vazamento: "Vazamento de dados", vulnerabilidade: "Vulnerabilidade", disponibilidade: "Disponibilidade", duvida: "Dúvida", outro: "Outro" };
export const ORIGENS: Record<string, string> = { siem: "SIEM", portal: "Portal", telefone: "Telefone", email: "E-mail" };
export const EVENTOS: Record<string, string> = {
  aberto: "Abertura", em_andamento: "Em andamento", andamento: "Andamento registrado", comentario: "Comentário do cliente", aguardando_cliente: "Aguardando o cliente",
  resolvido: "Resolução", encerrado: "Encerramento", escalado: "Escalado", classificado: "Classificação alterada", anexo: "Anexo incluído",
};

export const opcoes = (mapa: Record<string, string>) => Object.entries(mapa).map(([value, label]) => ({ value, label }));
