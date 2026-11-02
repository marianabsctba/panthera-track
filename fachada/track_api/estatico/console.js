const CRIT = {critica:"Crítica",alta:"Alta",media:"Média",baixa:"Baixa",consulta:"Consulta"};
const SIT = {aberto:"Aberto",em_andamento:"Em andamento",aguardando_cliente:"Aguardando cliente",resolvido:"Resolvido",encerrado:"Encerrado"};
const EV = {aberto:"Abertura",em_andamento:"Em andamento",andamento:"Andamento registrado",comentario:"Comentário do cliente",aguardando_cliente:"Aguardando o cliente",resolvido:"Resolução",encerrado:"Encerramento"};
const ORIGEM = {siem:"SIEM",portal:"Portal",telefone:"Telefone"};
let eu = null, fila = {chamados:[], indisponiveis:[], clientes:[]}, selecionado = null, lidoEm = Date.now(), relogios = [];
const $ = id => document.getElementById(id);
function el(tag, props, ...filhos) { const e = Object.assign(document.createElement(tag), props || {}); e.append(...filhos); return e; }
function semHtml(t) { const d = s => new DOMParser().parseFromString(s || "", "text/html").body.textContent; return d(d(t)); }
function hora(t) { return t ? new Date(t).toLocaleString("pt-BR", {dateStyle:"short", timeStyle:"short"}) : "-"; }
function duracao(s) { const n = Math.abs(s), h = Math.floor(n/3600), m = Math.floor(n%3600/60), x = n%60; return h ? `${h}h ${String(m).padStart(2,"0")}min` : `${m}min ${String(x).padStart(2,"0")}s`; }
function analista() { return eu && eu.tipo === "analista"; }

async function api(metodo, caminho, corpo) {
  const r = await fetch(caminho, {method:metodo, headers:{"Content-Type":"application/json","X-Track-Tela":"1"}, body:corpo ? JSON.stringify(corpo) : undefined});
  const d = await r.json().catch(() => ({}));
  if (r.status === 401 && caminho !== "/api/v1/sessao" && eu && eu.etapa === "pronto") { mostrarEntrada("entrar"); throw new Error("Entre para continuar"); }
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Confira os campos e tente de novo");
  return d;
}

function mostrarEntrada(etapa) {
  relogios.forEach(clearInterval); relogios = [];
  $("tela-app").hidden = true; $("tela-entrada").hidden = false; $("erro-entrada").textContent = "";
  ["f-entrar","f-senha","f-mfa-cadastro","f-mfa","recuperacao"].forEach(i => { $(i).hidden = true; });
  document.querySelectorAll("#tela-entrada form").forEach(f => f.reset());
  const mapa = {entrar:"f-entrar",trocar_senha:"f-senha",cadastrar_mfa:"f-mfa-cadastro",codigo_mfa:"f-mfa"};
  $(mapa[etapa]).hidden = false;
  if (etapa === "cadastrar_mfa") api("GET", "/api/v1/sessao/mfa").then(d => { $("qr").src = d.qr; $("segredo").textContent = d.segredo; }).catch(e => { $("erro-entrada").textContent = e.message; });
}
function seguir(d) {
  eu = d;
  if (d.etapa === "pronto") return iniciarApp();
  mostrarEntrada(d.etapa);
}
function enviar(idForm, caminho, depois) {
  $(idForm).addEventListener("submit", async ev => {
    ev.preventDefault();
    const botao = $(idForm).querySelector("button"); botao.disabled = true; $("erro-entrada").textContent = "";
    try { depois(await api("POST", caminho, Object.fromEntries(new FormData($(idForm))))); }
    catch (e) { $("erro-entrada").textContent = e.message; }
    botao.disabled = false;
  });
}
enviar("f-entrar", "/api/v1/sessao", seguir);
enviar("f-senha", "/api/v1/sessao/senha", seguir);
enviar("f-mfa", "/api/v1/sessao/mfa", seguir);
enviar("f-mfa-cadastro", "/api/v1/sessao/mfa/ativar", d => {
  eu = d; $("f-mfa-cadastro").hidden = true; $("recuperacao").hidden = false;
  $("codigos").textContent = d.codigos_recuperacao.join("\n");
});
$("guardei").onclick = () => iniciarApp();
$("sair").onclick = async () => { await api("DELETE", "/api/v1/sessao").catch(() => {}); eu = null; selecionado = null; mostrarEntrada("entrar"); };

function textoSla(c) {
  const s = c.sla;
  if (s.segundos_restantes === null) return el("span", {className:"apagado", textContent:"Sem prazo"});
  if (c.status === "resolvido" || c.status === "encerrado") return el("span", {className:s.estourado ? "tarde" : "", textContent:(s.estourado ? "Fora do prazo, " : "No prazo, ") + duracao(s.segundos_corridos)});
  if (s.pausado) return el("span", {className:"pausa", textContent:"Pausado, restam " + duracao(s.segundos_restantes)});
  const resta = s.segundos_restantes - Math.floor((Date.now() - lidoEm)/1000);
  if (resta < 0) return el("span", {className:"tarde", textContent:"Estourado há " + duracao(resta)});
  return el("span", {className:s.em_risco ? "risco" : "", textContent:(s.em_risco ? "Em risco, restam " : "Restam ") + duracao(resta)});
}
function colunas() {
  const mostraCliente = analista();
  return mostraCliente ? ["Nº","Cliente","Chamado","Criticidade","Situação","SLA"] : ["Nº","Chamado","Criticidade","Situação","SLA"];
}
function desenhaLista() {
  $("resumo").replaceChildren(...Object.keys(SIT).map(k => el("div", {className:"cartao"}, el("b", {textContent:fila.chamados.filter(c => c.status === k).length}), el("small", {textContent:SIT[k]}))));
  $("aviso").hidden = !fila.indisponiveis.length;
  $("aviso").textContent = fila.indisponiveis.length ? "Sem resposta agora: " + fila.indisponiveis.join(", ") : "";
  $("linhas").replaceChildren(...fila.chamados.map(c => {
    const marcado = selecionado && c.numero === selecionado.numero && c.cliente_id === selecionado.cliente_id;
    const tr = el("tr", {className:"item" + (marcado ? " sel" : "")}, el("td", {textContent:c.numero}));
    if (analista()) tr.append(el("td", {textContent:c.cliente}));
    tr.append(el("td", {}, c.titulo, el("div", {className:"apagado", textContent:ORIGEM[c.origem] + ", aberto em " + hora(c.aberto_em)})),
      el("td", {}, el("span", {className:"selo c-" + c.criticidade, textContent:CRIT[c.criticidade]})),
      el("td", {}, el("span", {className:"selo s-" + c.status, textContent:SIT[c.status]})), el("td", {}, textoSla(c)));
    tr.onclick = () => abrirChamado(c.cliente_id, c.numero);
    return tr;
  }));
}
async function carregar() { fila = await api("GET", "/api/v1/tela/chamados"); lidoEm = Date.now(); desenhaLista(); }
function rota(s) { return `/api/v1/tela/clientes/${s.cliente_id}/chamados/${s.numero}`; }
function acao(rotulo, caminho, opcoes = {}) {
  const b = el("button", {textContent:rotulo, className:opcoes.forte ? "forte" : ""});
  b.onclick = async () => {
    const corpo = opcoes.texto ? {texto:$("texto").value.trim()} : undefined;
    b.disabled = true;
    try { await api("POST", `${rota(selecionado)}/${caminho}`, corpo); await carregar(); await abrirChamado(selecionado.cliente_id, selecionado.numero); }
    catch (e) { $("erro").textContent = e.message; b.disabled = false; }
  };
  return b;
}
async function abrirChamado(cliente_id, numero) {
  selecionado = {cliente_id, numero}; desenhaLista();
  const c = await api("GET", rota(selecionado)), h = await api("GET", rota(selecionado) + "/historico");
  const btns = [];
  let dica = null;
  if (analista()) {
    if (c.status === "aberto") btns.push(acao("Assumir atendimento", "assumir", {forte:true}));
    if (c.status === "em_andamento") { dica = "Solução, andamento ou motivo da espera"; btns.push(acao("Resolver", "resolver", {forte:true, texto:true}), acao("Registrar andamento", "andamento", {texto:true}), acao("Aguardar cliente", "pausar", {texto:true})); }
    if (c.status === "aguardando_cliente") { dica = "Andamento"; btns.push(acao("Retomar", "retomar", {forte:true}), acao("Registrar andamento", "andamento", {texto:true})); }
    if (c.status === "resolvido") btns.push(acao("Encerrar", "encerrar", {forte:true}));
  } else if (["aberto","em_andamento","aguardando_cliente"].includes(c.status)) {
    dica = "Escreva para o atendimento"; btns.push(acao("Enviar comentário", "comentar", {forte:true, texto:true}));
  }
  $("detalhe").replaceChildren(
    el("div", {className:"apagado", textContent:`${c.cliente}, chamado nº ${c.numero}`}), el("h2", {textContent:c.titulo}),
    el("span", {className:"selo c-" + c.criticidade, textContent:CRIT[c.criticidade]}), " ", el("span", {className:"selo s-" + c.status, textContent:SIT[c.status]}),
    el("p", {textContent:semHtml(c.descricao)}),
    el("dl", {className:"dados"},
      el("dt", {textContent:"Origem"}), el("dd", {textContent:ORIGEM[c.origem]}),
      el("dt", {textContent:"Solicitante"}), el("dd", {textContent:c.solicitante}), el("dt", {textContent:"Atendente"}), el("dd", {textContent:c.atendente || "-"}),
      el("dt", {textContent:"Abertura"}), el("dd", {textContent:hora(c.aberto_em)}), el("dt", {textContent:"Resolução"}), el("dd", {textContent:hora(c.resolvido_em)}),
      el("dt", {textContent:"Encerramento"}), el("dd", {textContent:hora(c.encerrado_em)}), el("dt", {textContent:"Prazo"}), el("dd", {textContent:hora(c.sla.prazo)}),
      el("dt", {textContent:"Tempo corrido"}), el("dd", {textContent:duracao(c.sla.segundos_corridos)}), el("dt", {textContent:"Tempo em pausa"}), el("dd", {textContent:duracao(c.sla.segundos_pausados)})),
    el("div", {className:"acoes"}, ...(dica ? [el("textarea", {id:"texto", rows:2, placeholder:dica})] : []), ...btns),
    el("p", {id:"erro", className:"erro", role:"alert"}),
    el("h3", {textContent:"Histórico"}),
    ...h.eventos.map(e => el("div", {className:"evento"}, el("b", {textContent:EV[e.tipo] || e.tipo}), el("div", {className:"apagado", textContent:hora(e.criado_em) + ", " + e.autor}))),
    el("h3", {textContent:"Registros"}),
    ...(h.interacoes.length ? h.interacoes.map(i => el("div", {className:"evento"}, el("b", {textContent:i.tipo === "solucao" ? "Solução" : "Andamento"}), el("div", {textContent:semHtml(i.conteudo)}), el("div", {className:"apagado", textContent:hora(i.em) + ", " + i.autor}))) : [el("span", {className:"apagado", textContent:"Nada registrado ainda."})]));
}
async function iniciarApp() {
  $("tela-entrada").hidden = true; $("tela-app").hidden = false;
  $("quem").textContent = eu.nome;
  $("cabecalho").replaceChildren(...colunas().map(t => el("th", {textContent:t})));
  $("detalhe").replaceChildren(el("span", {className:"apagado", textContent:"Escolha um chamado para ver o andamento."}));
  await carregar();
  relogios = [setInterval(desenhaLista, 1000), setInterval(() => carregar().catch(() => {}), 20000)];
}
$("abrir").onclick = () => {
  const a = analista();
  $("novo").reset(); $("erro-novo").textContent = "";
  $("novo-titulo").textContent = a ? "Registrar chamado por telefone" : "Novo chamado";
  $("campo-cliente").hidden = !a; $("campo-solicitante").hidden = !a;
  $("novo").elements.solicitante.required = a;
  if (a) $("novo").elements.cliente_id.replaceChildren(...fila.clientes.map(c => el("option", {value:c.id, textContent:c.nome})));
  $("dialogo").showModal();
};
$("cancelar").onclick = () => $("dialogo").close();
$("novo").addEventListener("submit", async ev => {
  ev.preventDefault();
  const dados = Object.fromEntries(new FormData($("novo")));
  if (!analista()) { delete dados.cliente_id; delete dados.solicitante; }
  try {
    const novo = await api("POST", "/api/v1/tela/chamados", dados);
    $("dialogo").close(); await carregar(); abrirChamado(novo.cliente_id, novo.numero);
  } catch (e) { $("erro-novo").textContent = e.message; }
});
api("GET", "/api/v1/sessao").then(seguir).catch(() => mostrarEntrada("entrar"));
