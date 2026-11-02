const CRIT = {critica:"Crítica",alta:"Alta",media:"Média",baixa:"Baixa",consulta:"Consulta"};
const SIT = {aberto:"Aberto",em_andamento:"Em andamento",aguardando_cliente:"Aguardando cliente",resolvido:"Resolvido",encerrado:"Encerrado"};
const EV = {aberto:"Abertura",em_andamento:"Em andamento",andamento:"Andamento registrado",comentario:"Comentário do cliente",aguardando_cliente:"Aguardando o cliente",resolvido:"Resolução",encerrado:"Encerramento",escalado:"Escalado",classificado:"Classificação alterada",anexo:"Anexo incluído"};
const ORIGEM = {siem:"SIEM",portal:"Portal",telefone:"Telefone",email:"E-mail"};
const TIPO = {incidente:"Incidente",requisicao:"Requisição"};
const CATEGORIA = {acesso:"Acesso",malware:"Malware",rede:"Rede",vazamento:"Vazamento de dados",vulnerabilidade:"Vulnerabilidade",disponibilidade:"Disponibilidade",duvida:"Dúvida",outro:"Outro"};
let eu = null, fila = {chamados:[], indisponiveis:[], clientes:[]}, selecionado = null, lidoEm = Date.now(), relogios = [];
const $ = id => document.getElementById(id);
function el(tag, props, ...filhos) { const e = Object.assign(document.createElement(tag), props || {}); e.append(...filhos); return e; }
function semHtml(t) { const d = s => new DOMParser().parseFromString(s || "", "text/html").body.textContent; return d(d(t)); }
function hora(t) { return t ? new Date(t).toLocaleString("pt-BR", {dateStyle:"short", timeStyle:"short"}) : "-"; }
function duracao(s) { const n = Math.abs(s), h = Math.floor(n/3600), m = Math.floor(n%3600/60), x = n%60; return h ? `${h}h ${String(m).padStart(2,"0")}min` : `${m}min ${String(x).padStart(2,"0")}s`; }
function analista() { return eu && eu.tipo === "analista"; }
function opcoes(mapa, atual) { return Object.entries(mapa).map(([v, t]) => el("option", {value:v, textContent:t, selected:v === atual})); }

async function tratar(r, caminho) {
  const d = await r.json().catch(() => ({}));
  if (r.status === 401 && caminho !== "/api/v1/sessao" && eu && eu.etapa === "pronto") { mostrarEntrada("entrar"); throw new Error("Entre para continuar"); }
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Confira os campos e tente de novo");
  return d;
}
async function api(metodo, caminho, corpo) {
  return tratar(await fetch(caminho, {method:metodo, headers:{"Content-Type":"application/json","X-Track-Tela":"1"}, body:corpo ? JSON.stringify(corpo) : undefined}), caminho);
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
function desenhaLista() {
  $("resumo").replaceChildren(...Object.keys(SIT).map(k => el("div", {className:"cartao"}, el("b", {textContent:fila.chamados.filter(c => c.status === k).length}), el("small", {textContent:SIT[k]}))));
  $("aviso").hidden = !fila.indisponiveis.length;
  $("aviso").textContent = fila.indisponiveis.length ? "Sem resposta agora: " + fila.indisponiveis.join(", ") : "";
  $("linhas").replaceChildren(...fila.chamados.map(c => {
    const marcado = selecionado && c.numero === selecionado.numero && c.cliente_id === selecionado.cliente_id;
    const tr = el("tr", {className:"item" + (marcado ? " sel" : "")}, el("td", {className:"codigo", textContent:c.codigo}));
    if (analista()) tr.append(el("td", {textContent:c.cliente}));
    tr.append(el("td", {}, c.titulo, ...(c.nivel !== "N1" ? [" ", el("span", {className:"selo nivel", textContent:c.nivel})] : []), el("div", {className:"apagado", textContent:`${TIPO[c.tipo]}, ${c.categoria || "Sem categoria"}. ${ORIGEM[c.origem]}, ${hora(c.aberto_em)}`})),
      el("td", {}, el("span", {className:"selo c-" + c.criticidade, textContent:CRIT[c.criticidade]})),
      el("td", {}, el("span", {className:"selo s-" + c.status, textContent:SIT[c.status]})), el("td", {}, textoSla(c)));
    tr.onclick = () => abrirChamado(c.cliente_id, c.numero);
    return tr;
  }));
}
async function carregar() { fila = await api("GET", "/api/v1/tela/chamados"); lidoEm = Date.now(); desenhaLista(); }
function rota(s) { return `/api/v1/tela/clientes/${s.cliente_id}/chamados/${s.numero}`; }
async function recarregarDetalhe() { await carregar(); await abrirChamado(selecionado.cliente_id, selecionado.numero); }
function acao(rotulo, caminho, ops = {}) {
  const b = el("button", {textContent:rotulo, className:ops.forte ? "forte" : ""});
  b.onclick = async () => {
    const corpo = ops.texto ? {texto:$("texto").value.trim()} : undefined;
    b.disabled = true;
    try { await api("POST", `${rota(selecionado)}/${caminho}`, corpo); await recarregarDetalhe(); }
    catch (e) { $("erro").textContent = e.message; b.disabled = false; }
  };
  return b;
}
function par(rotulo, valor) { return [el("dt", {textContent:rotulo}), el("dd", {}, valor || "-")]; }
function barraPrazo(rotulo, percentual, classe, texto) {
  const i = el("i"); i.style.width = Math.min(100, Math.max(0, percentual ?? 0)) + "%";
  return el("div", {className:"prazo"}, el("b", {}, el("span", {textContent:rotulo}), el("span", {className:"apagado", textContent:texto})), el("div", {className:"barra " + classe}, i));
}
function blocoSla(c) {
  const s = c.sla, partes = [];
  if (s.resposta) {
    const r = s.resposta;
    const texto = r.em ? `${r.cumprida ? "Cumprida" : "Fora do prazo"} em ${hora(r.em)}` : r.estourada ? `Estourada, era até ${hora(r.prazo)}` : `Até ${hora(r.prazo)}`;
    const abertura = new Date(c.aberto_em).getTime(), total = new Date(r.prazo).getTime() - abertura;
    const gasto = (r.em ? new Date(r.em).getTime() : Date.now()) - abertura;
    partes.push(barraPrazo("Primeira resposta", total > 0 ? gasto*100/total : 100, r.em && r.cumprida ? "ok-b" : r.estourada ? "tarde-b" : "", texto));
  }
  if (s.prazo) {
    const encerrado = c.status === "resolvido" || c.status === "encerrado";
    const texto = s.pausado ? `Pausado, prazo em ${hora(s.prazo)}` : encerrado ? `${s.estourado ? "Fora do prazo" : "Cumprido"}, prazo era ${hora(s.prazo)}` : `Até ${hora(s.prazo)}`;
    const classe = s.estourado ? "tarde-b" : encerrado ? "ok-b" : s.em_risco || s.pausado ? "risco-b" : "";
    partes.push(barraPrazo("Resolução", s.percentual, classe, texto));
  }
  const t = c.tempos, tempos = [...par("Até assumir", t.ate_assumir === null ? "Ninguém assumiu" : duracao(t.ate_assumir)), ...par("Aguardando cliente", duracao(t.aguardando_cliente))];
  if (c.resolvido_em) tempos.push(...par("Até resolver, sem pausas", duracao(t.ate_resolver)));
  if (c.encerrado_em) tempos.push(...par("Até encerrar", duracao(t.ate_encerrar)));
  partes.push(el("dl", {className:"dados"}, ...par("Tempo corrido", duracao(s.segundos_corridos)), ...par("Tempo em pausa", duracao(s.segundos_pausados)), ...tempos));
  if (c.pausas.length) partes.push(el("h3", {textContent:"Pausas"}), ...c.pausas.map(p => el("div", {className:"evento"},
    el("div", {textContent:p.motivo || "Sem motivo registrado"}),
    el("div", {className:"apagado", textContent:`${hora(p.em)}, ${p.autor}, ${p.segundos === null ? "encerrada pela resposta do cliente" : (p.em_curso ? "em curso há " : "durou ") + duracao(p.segundos)}`}))));
  return partes;
}
function blocoContexto(c) {
  const x = c.contexto || {}, itens = [];
  if (x.ativo) itens.push(...par("Ativo ou local", x.ativo));
  if (x.ip_origem) itens.push(...par("IP de origem", x.ip_origem));
  if (x.ip_destino) itens.push(...par("IP de destino", x.ip_destino));
  if (x.usuario) itens.push(...par("Usuário", x.usuario));
  if (x.regra) itens.push(...par("Regra", x.regra));
  if (x.tecnica_mitre) itens.push(...par("Técnica", el("a", {href:`https://attack.mitre.org/techniques/${x.tecnica_mitre.replace(".", "/")}/`, target:"_blank", rel:"noopener noreferrer", textContent:x.tecnica_mitre})));
  // O servidor só aceita http e https; a conferência aqui é a segunda barreira.
  if (x.url_alerta && /^https?:\/\//.test(x.url_alerta)) itens.push(...par("Alerta", el("a", {href:x.url_alerta, target:"_blank", rel:"noopener noreferrer", textContent:"Abrir o alerta"})));
  return itens.length ? [el("h3", {textContent:"Contexto do incidente"}), el("dl", {className:"dados"}, ...itens)] : [];
}
function blocoClassificacao(c) {
  if (!analista() || c.status === "encerrado") return [];
  const t = el("select", {id:"novo-tipo"}, ...opcoes(TIPO, c.tipo)), g = el("select", {id:"nova-categoria"}, ...opcoes(CATEGORIA, Object.keys(CATEGORIA).find(k => CATEGORIA[k] === c.categoria)));
  const b = el("button", {textContent:"Salvar classificação"});
  b.onclick = async () => {
    b.disabled = true;
    try { await api("POST", `${rota(selecionado)}/classificar`, {tipo:t.value, categoria:g.value}); await recarregarDetalhe(); }
    catch (e) { $("erro").textContent = e.message; b.disabled = false; }
  };
  return [el("div", {className:"acoes"}, el("div", {className:"duas"}, t, g), b)];
}
function blocoEscalar(c) {
  if (!analista() || !["aberto","em_andamento","aguardando_cliente"].includes(c.status) || c.nivel === "N3") return [];
  const niveis = c.nivel === "N1" ? ["N2","N3"] : ["N3"];
  const n = el("select", {id:"novo-nivel"}, ...niveis.map(v => el("option", {value:v, textContent:v})));
  const m = el("input", {id:"motivo-escala", placeholder:"Motivo do escalonamento", maxLength:1000});
  const b = el("button", {textContent:"Escalar"});
  b.onclick = async () => {
    b.disabled = true;
    try { await api("POST", `${rota(selecionado)}/escalar`, {nivel:n.value, motivo:m.value.trim()}); await recarregarDetalhe(); }
    catch (e) { $("erro").textContent = e.message; b.disabled = false; }
  };
  return [el("div", {className:"anexo-novo"}, n, m, b)];
}
function blocoAnexos(c) {
  const lista = c.anexos.map(a => el("div", {className:"evento"},
    el("a", {href:`${rota(selecionado)}/anexos/${a.id}`, textContent:a.nome, download:a.nome}),
    el("div", {className:"apagado", textContent:`${hora(a.criado_em)}, ${a.autor}`})));
  const partes = [el("h3", {textContent:"Anexos"}), ...(lista.length ? lista : [el("span", {className:"apagado", textContent:"Nenhum anexo."})])];
  if (c.status !== "encerrado") {
    const entrada = el("input", {type:"file", id:"arquivo"}), b = el("button", {textContent:"Anexar"});
    b.onclick = async () => {
      if (!entrada.files.length) { $("erro").textContent = "Escolha um arquivo"; return; }
      const dados = new FormData(); dados.append("arquivo", entrada.files[0]);
      b.disabled = true;
      try {
        const caminho = `${rota(selecionado)}/anexos`;
        await tratar(await fetch(caminho, {method:"POST", headers:{"X-Track-Tela":"1"}, body:dados}), caminho);
        await recarregarDetalhe();
      } catch (e) { $("erro").textContent = e.message; b.disabled = false; }
    };
    partes.push(el("div", {className:"anexo-novo"}, entrada, b));
  }
  return partes;
}
async function abrirChamado(cliente_id, numero) {
  selecionado = {cliente_id, numero}; desenhaLista();
  const c = await api("GET", rota(selecionado)), h = await api("GET", rota(selecionado) + "/historico");
  const btns = [];
  let dica = null;
  if (analista()) {
    if (c.status === "aberto" || (c.status === "em_andamento" && !c.atendente)) btns.push(acao("Assumir atendimento", "assumir", {forte:true}));
    if (c.status === "em_andamento" && c.atendente) { dica = "Solução, andamento ou motivo da espera"; btns.push(acao("Resolver", "resolver", {forte:true, texto:true}), acao("Registrar andamento", "andamento", {texto:true}), acao("Aguardar cliente", "pausar", {texto:true})); }
    if (c.status === "aguardando_cliente") { dica = "Andamento"; btns.push(acao("Retomar", "retomar", {forte:true}), acao("Registrar andamento", "andamento", {texto:true})); }
    if (c.status === "resolvido") btns.push(acao("Encerrar", "encerrar", {forte:true}));
  } else if (["aberto","em_andamento","aguardando_cliente"].includes(c.status)) {
    dica = "Escreva para o atendimento"; btns.push(acao("Enviar comentário", "comentar", {forte:true, texto:true}));
  }
  const contato = c.contato.telefone || c.contato.email ? el("span", {textContent:[c.contato.telefone, c.contato.email].filter(Boolean).join(", ")}) : null;
  $("detalhe").replaceChildren(
    el("div", {className:"apagado", textContent:c.cliente}), el("h2", {}, `${c.codigo}: `, c.titulo),
    el("span", {className:"selo c-" + c.criticidade, textContent:CRIT[c.criticidade]}), " ", el("span", {className:"selo s-" + c.status, textContent:SIT[c.status]}),
    el("p", {textContent:semHtml(c.descricao)}),
    el("dl", {className:"dados"},
      ...par("Tipo", TIPO[c.tipo]), ...par("Categoria", c.categoria), ...par("Origem", ORIGEM[c.origem]),
      ...par("Solicitante", c.solicitante), ...par("Contato", contato), ...par("Atendente", c.atendente), ...par("Nível", c.nivel),
      ...par("Abertura", hora(c.aberto_em)), ...par("Resolução", hora(c.resolvido_em)), ...par("Encerramento", hora(c.encerrado_em))),
    el("div", {className:"acoes"}, ...(dica ? [el("textarea", {id:"texto", rows:2, placeholder:dica})] : []), ...btns),
    ...blocoEscalar(c),
    ...blocoClassificacao(c),
    el("p", {id:"erro", className:"erro", role:"alert"}),
    el("h3", {textContent:"Prazos"}), ...blocoSla(c),
    ...blocoContexto(c),
    ...blocoAnexos(c),
    el("h3", {textContent:"Histórico"}),
    ...h.eventos.map(e => el("div", {className:"evento"}, el("b", {textContent:EV[e.tipo] || e.tipo}), ...(e.detalhe ? [el("div", {textContent:e.detalhe})] : []), el("div", {className:"apagado", textContent:hora(e.criado_em) + ", " + e.autor}))),
    el("h3", {textContent:"Registros"}),
    ...(h.interacoes.length ? h.interacoes.map(i => el("div", {className:"evento"}, el("b", {textContent:i.tipo === "solucao" ? "Solução" : "Andamento"}), el("div", {textContent:semHtml(i.conteudo)}), el("div", {className:"apagado", textContent:hora(i.em) + ", " + i.autor}))) : [el("span", {className:"apagado", textContent:"Nada registrado ainda."})]));
}
async function iniciarApp() {
  $("tela-entrada").hidden = true; $("tela-app").hidden = false;
  $("quem").textContent = eu.nome;
  const colunas = analista() ? ["Código","Cliente","Chamado","Criticidade","Situação","SLA"] : ["Código","Chamado","Criticidade","Situação","SLA"];
  $("cabecalho").replaceChildren(...colunas.map(t => el("th", {textContent:t})));
  $("detalhe").replaceChildren(el("span", {className:"apagado", textContent:"Escolha um chamado para ver o andamento."}));
  await carregar();
  relogios = [setInterval(desenhaLista, 1000), setInterval(() => carregar().catch(() => {}), 20000)];
}
$("abrir").onclick = () => {
  const a = analista(), f = $("novo");
  f.reset(); $("erro-novo").textContent = "";
  $("novo-titulo").textContent = a ? "Registrar chamado por telefone" : "Novo chamado";
  $("campo-cliente").hidden = !a; $("campo-solicitante").hidden = !a;
  f.elements.solicitante.required = a;
  f.elements.categoria.replaceChildren(...opcoes(CATEGORIA, "outro"));
  if (a) f.elements.cliente_id.replaceChildren(...fila.clientes.map(c => el("option", {value:c.id, textContent:c.nome})));
  else f.elements.contato_email.value = eu.email;
  $("dialogo").showModal();
};
$("cancelar").onclick = () => $("dialogo").close();
$("novo").addEventListener("submit", async ev => {
  ev.preventDefault();
  const dados = Object.fromEntries(new FormData($("novo")));
  if (!analista()) { delete dados.cliente_id; delete dados.solicitante; }
  for (const k of ["ativo","contato_telefone","contato_email"]) if (!dados[k]) delete dados[k];
  try {
    const novo = await api("POST", "/api/v1/tela/chamados", dados);
    $("dialogo").close(); await carregar(); abrirChamado(novo.cliente_id, novo.numero);
  } catch (e) { $("erro-novo").textContent = e.message; }
});
api("GET", "/api/v1/sessao").then(seguir).catch(() => mostrarEntrada("entrar"));
