"""Rotas da tela. Cada pessoa escreve no motor com o próprio usuário-sombra; a leitura passa por aqui, depois da permissão."""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from . import banco, operacoes
from .dominio import (ABERTO, AGUARDANDO_CLIENTE, CATEGORIAS, CRITICIDADES, EM_ANDAMENTO, ENCERRADO, NIVEIS, PADRAO_CATEGORIA, PADRAO_CRITICIDADE,
                      PADRAO_TIPO, RESOLVIDO, TIPOS, nivel_do_chamado, representar, tempos_do_chamado)
from .motor import MotorIndisponivel, motor_da_pessoa, motor_do_cliente
from .sessao import checar_origem, pessoa_pronta

router = APIRouter(prefix="/api/v1/tela", dependencies=[Depends(checar_origem)])
_LIMITE_POR_CLIENTE = 200
_TAMANHO_MAXIMO = 10 * 1024 * 1024
_ROTULOS_CRITICIDADE = {"critica": "Crítica", "alta": "Alta", "media": "Média", "baixa": "Baixa", "consulta": "Consulta"}
_ROTULOS_TIPO = {"incidente": "Incidente", "requisicao": "Requisição"}
_EXTENSOES = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".log", ".csv", ".json", ".zip", ".eml", ".docx", ".xlsx", ".pcap"}


class NovoChamado(BaseModel):
    titulo: str = Field(min_length=1, max_length=255)
    descricao: str = Field(min_length=1, max_length=65000)
    criticidade: str = Field(pattern=PADRAO_CRITICIDADE)
    tipo: str = Field(default="incidente", pattern=PADRAO_TIPO)
    categoria: str = Field(default="outro", pattern=PADRAO_CATEGORIA)
    ativo: Optional[str] = Field(default=None, max_length=255)
    contato_telefone: Optional[str] = Field(default=None, pattern=r"^[0-9+() .-]{8,20}$")
    contato_email: Optional[str] = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    cliente_id: Optional[UUID] = None
    solicitante: Optional[str] = Field(default=None, min_length=1, max_length=255)


class Texto(BaseModel):
    texto: str = Field(min_length=1, max_length=65000)


class Escalonamento(BaseModel):
    nivel: str = Field(pattern="^(N2|N3)$")
    motivo: str = Field(min_length=1, max_length=65000)


class PedidoDeAceite(BaseModel):
    tipo: str = Field(pattern="^(criticidade|encerramento)$")
    criticidade: Optional[str] = Field(default=None, pattern=PADRAO_CRITICIDADE)
    motivo: str = Field(min_length=1, max_length=65000)


class DecisaoDeAceite(BaseModel):
    aceito: bool
    comentario: Optional[str] = Field(default=None, max_length=65000)


class Classificacao(BaseModel):
    tipo: str = Field(pattern=PADRAO_TIPO)
    categoria: str = Field(pattern=PADRAO_CATEGORIA)


def _clientes_de(pessoa):
    with banco.transacao() as conn:
        if pessoa["tipo"] == "cliente":
            return conn.execute("SELECT * FROM clientes WHERE id = %s", (pessoa["cliente_id"],)).fetchall()
        return conn.execute(
            "SELECT c.* FROM clientes c JOIN vinculos v ON v.cliente_id = c.id WHERE v.pessoa_id = %s ORDER BY c.nome",
            (pessoa["id"],)).fetchall()


def _cliente(pessoa, cliente_id):
    """Fora do alcance da pessoa, responde como se não existisse."""
    for cliente in _clientes_de(pessoa):
        if str(cliente["id"]) == str(cliente_id):
            return cliente
    raise HTTPException(404, "Chamado não encontrado")


def _ve_tudo(pessoa):
    return pessoa["tipo"] == "analista" or pessoa["papel"] == "gestor"


def _pode_ver(pessoa):
    return lambda linha: _ve_tudo(pessoa) or linha["pessoa_solicitante"] == pessoa["id"]


def _exigir_analista(pessoa=Depends(pessoa_pronta)):
    if pessoa["tipo"] != "analista":
        raise HTTPException(403, "Sem permissão")
    return pessoa


def _do_cliente(saida, cliente):
    return {**saida, "cliente_id": str(cliente["id"]), "cliente": cliente["nome"]}


def _fila_do_cliente(pessoa, cliente):
    with banco.transacao(cliente["id"]) as conn:
        if _ve_tudo(pessoa):
            linhas = conn.execute("SELECT * FROM chamados ORDER BY numero DESC LIMIT %s", (_LIMITE_POR_CLIENTE,)).fetchall()
        else:
            linhas = conn.execute("SELECT * FROM chamados WHERE pessoa_solicitante = %s ORDER BY numero DESC LIMIT %s",
                                  (pessoa["id"], _LIMITE_POR_CLIENTE)).fetchall()
        pendentes = {a["numero"] for a in conn.execute("SELECT numero FROM aceites WHERE estado = 'pendente'").fetchall()}
    dados = {d["id"]: d for d in motor_do_cliente(cliente).chamados([x["numero"] for x in linhas])}
    return [{**_do_cliente(representar(x, dados[x["numero"]]), cliente), "aceite_pendente": x["numero"] in pendentes}
            for x in linhas if x["numero"] in dados]


def _ordem(chamado):
    """Na frente, o que está mais perto de estourar; o que já acabou vai para o fim, do mais novo ao mais antigo."""
    if chamado["status"] in ("resolvido", "encerrado"):
        return (1, -chamado["numero"])
    restante = chamado["sla"]["segundos_restantes"]
    return (0, restante if restante is not None else float("inf"))


@router.get("/chamados")
def fila(pessoa=Depends(pessoa_pronta)):
    clientes = _clientes_de(pessoa)
    chamados, indisponiveis = [], []
    with ThreadPoolExecutor(max_workers=8) as grupo:
        futuros = [(c, grupo.submit(_fila_do_cliente, pessoa, c)) for c in clientes]
        for cliente, futuro in futuros:
            try:
                chamados.extend(futuro.result())
            except MotorIndisponivel:
                # Um cliente fora do ar não pode tirar da vista os chamados dos outros.
                indisponiveis.append(cliente["nome"])
    chamados.sort(key=_ordem)
    return {"chamados": chamados, "indisponiveis": indisponiveis,
            "clientes": [{"id": str(c["id"]), "nome": c["nome"]} for c in clientes]}


@router.post("/chamados", status_code=201)
def abrir(novo: NovoChamado, pessoa=Depends(pessoa_pronta)):
    contexto = {"ativo": novo.ativo} if novo.ativo else {}
    comuns = dict(tipo=novo.tipo, categoria=novo.categoria, contexto=contexto, contato_telefone=novo.contato_telefone)
    if pessoa["tipo"] == "cliente":
        cliente = _cliente(pessoa, pessoa["cliente_id"])
        saida, _ = operacoes.abrir(cliente, novo.titulo, novo.descricao, novo.criticidade, pessoa["nome"], "portal",
                                   escritor=motor_da_pessoa(pessoa, cliente), pessoa_id=pessoa["id"],
                                   contato_email=novo.contato_email or pessoa["email"], **comuns)
    else:
        # Analista registra o que chegou por telefone, em nome de quem ligou.
        if not novo.cliente_id or not novo.solicitante:
            raise HTTPException(422, "Informe o cliente e quem pediu")
        cliente = _cliente(pessoa, novo.cliente_id)
        saida, _ = operacoes.abrir(cliente, novo.titulo, novo.descricao, novo.criticidade, novo.solicitante, "telefone",
                                   escritor=motor_da_pessoa(pessoa, cliente), contato_email=novo.contato_email, **comuns)
    return _do_cliente(saida, cliente)


def _carregar(pessoa, cliente, numero):
    with banco.transacao(cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s", (numero,)).fetchone()
    if not linha or not _pode_ver(pessoa)(linha):
        raise HTTPException(404, "Chamado não encontrado")
    return linha


def _aceites(cliente, numero):
    with banco.transacao(cliente["id"]) as conn:
        linhas = conn.execute(
            """SELECT id, tipo, criticidade, motivo, pedido_por, pedido_em, estado, decidido_por, decidido_em, comentario
               FROM aceites WHERE numero = %s ORDER BY pedido_em DESC""", (numero,)).fetchall()
    return [{**a, "id": str(a["id"]), "pedido_em": a["pedido_em"].isoformat(timespec="seconds"),
             "decidido_em": a["decidido_em"].isoformat(timespec="seconds") if a["decidido_em"] else None} for a in linhas]


@router.get("/clientes/{cliente_id}/chamados/{numero}")
def consultar(cliente_id: UUID, numero: int, pessoa=Depends(pessoa_pronta)):
    cliente = _cliente(pessoa, cliente_id)
    linha = _carregar(pessoa, cliente, numero)
    chamado = motor_do_cliente(cliente).chamado(numero)
    return {**_do_cliente(representar(linha, chamado), cliente),
            "contato": {"telefone": linha["contato_telefone"], "email": linha["contato_email"]},
            "aceites": _aceites(cliente, numero), "tempos": tempos_do_chamado(linha, chamado), "pausas": operacoes.pausas(cliente, numero, chamado["status"]["id"] == AGUARDANDO_CLIENTE), "anexos": operacoes.anexos(cliente, numero)}


@router.get("/clientes/{cliente_id}/chamados/{numero}/historico")
def historico(cliente_id: UUID, numero: int, pessoa=Depends(pessoa_pronta)):
    cliente = _cliente(pessoa, cliente_id)
    _carregar(pessoa, cliente, numero)
    return operacoes.historico(cliente, numero)


def _operar(pessoa, cliente_id, numero, permitidos, tipo, acao, detalhe=None):
    cliente = _cliente(pessoa, cliente_id)
    saida = operacoes.transicao(cliente, numero, permitidos, tipo, pessoa["nome"], acao, detalhe=detalhe,
                                escritor=motor_da_pessoa(pessoa, cliente), pessoa_id=pessoa["id"], antes=_pode_ver(pessoa))
    return _do_cliente(saida, cliente)


@router.post("/clientes/{cliente_id}/chamados/{numero}/assumir")
def assumir(cliente_id: UUID, numero: int, pessoa=Depends(_exigir_analista)):
    def acao(motor, conn):
        operacoes.assumir(conn, numero, pessoa["nome"], pessoa["id"])
        # O atendente precisa estar no motor: é ele quem avisa essa pessoa e decide para onde o chamado volta
        # quando o cliente responde numa pausa. Sem atendente, voltaria para Aberto.
        sombra = conn.execute("SELECT motor_id FROM sombras WHERE pessoa_id = %s AND cliente_id = %s",
                              (pessoa["id"], cliente_id)).fetchone()
        motor.ligar_pessoa(numero, sombra["motor_id"], "assigned")
        motor.mudar_status(numero, EM_ANDAMENTO)
    # Escalar atribui grupo, e o motor passa o chamado para Em andamento antes de alguém assumir.
    return _operar(pessoa, cliente_id, numero, {ABERTO, EM_ANDAMENTO}, "em_andamento", acao)


@router.post("/clientes/{cliente_id}/chamados/{numero}/escalar")
def escalar(cliente_id: UUID, numero: int, corpo: Escalonamento, pessoa=Depends(_exigir_analista)):
    cliente = _cliente(pessoa, cliente_id)

    def acao(motor, _conn):
        atual = nivel_do_chamado(motor_do_cliente(cliente).chamado(numero))
        if NIVEIS.index(corpo.nivel) <= NIVEIS.index(atual):
            raise HTTPException(409, f"O chamado já está em {atual}")
        # Só muda quem atende: criticidade e prazo continuam os mesmos.
        motor.atribuir_grupo(numero, cliente["grupos"][corpo.nivel])
        motor.acompanhamento(numero, operacoes.html(f"Escalado para {corpo.nivel}: {corpo.motivo}"))
    return _operar(pessoa, cliente_id, numero, {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE}, "escalado", acao,
                   detalhe=f"{corpo.nivel}: {corpo.motivo}")


@router.post("/clientes/{cliente_id}/chamados/{numero}/aceites", status_code=201)
def pedir_aceite(cliente_id: UUID, numero: int, corpo: PedidoDeAceite, pessoa=Depends(_exigir_analista)):
    if (corpo.tipo == "criticidade") != (corpo.criticidade is not None):
        raise HTTPException(422, "Só a mudança de criticidade informa a nova criticidade")
    if corpo.tipo == "criticidade":
        resumo = f"Mudar a criticidade para {_ROTULOS_CRITICIDADE[corpo.criticidade]}"
        permitidos = {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE}
    else:
        resumo = "Encerrar sem resolução"
        permitidos = {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE, RESOLVIDO}
    cliente = _cliente(pessoa, cliente_id)

    def acao(motor, conn):
        atual = conn.execute("SELECT criticidade FROM chamados WHERE numero = %s", (numero,)).fetchone()["criticidade"]
        if corpo.criticidade == atual:
            raise HTTPException(409, "O chamado já tem esta criticidade")
        if conn.execute("SELECT 1 FROM aceites WHERE numero = %s AND estado = 'pendente'", (numero,)).fetchone():
            raise HTTPException(409, "Já existe um pedido aguardando o cliente")
        conn.execute(
            "INSERT INTO aceites (cliente_id, numero, tipo, criticidade, motivo, pedido_por) VALUES (%s, %s, %s, %s, %s, %s)",
            (cliente["id"], numero, corpo.tipo, corpo.criticidade, corpo.motivo, pessoa["nome"]))
        # O acompanhamento é o que faz o motor avisar o cliente por e-mail.
        motor.acompanhamento(numero, operacoes.html(f"Pedido de aceite: {resumo}. Motivo: {corpo.motivo}"))
    return _operar(pessoa, cliente_id, numero, permitidos, "aceite_pedido", acao, detalhe=f"{resumo}: {corpo.motivo}")


@router.post("/clientes/{cliente_id}/chamados/{numero}/aceites/{aceite_id}/decidir")
def decidir_aceite(cliente_id: UUID, numero: int, aceite_id: UUID, corpo: DecisaoDeAceite, pessoa=Depends(pessoa_pronta)):
    # Quem aceita é o cliente, nunca quem atende.
    if pessoa["tipo"] != "cliente" or pessoa["papel"] != "gestor":
        raise HTTPException(403, "Só o gestor do cliente decide")
    cliente = _cliente(pessoa, cliente_id)
    decisao = "Aceito" if corpo.aceito else "Recusado"
    resumo = {}

    def acao(motor, conn):
        a = conn.execute(
            """UPDATE aceites SET estado = %s, decidido_por = %s, pessoa_decisao = %s, decidido_em = now(), comentario = %s
               WHERE id = %s AND numero = %s AND estado = 'pendente' RETURNING *""",
            ("aceito" if corpo.aceito else "recusado", pessoa["nome"], pessoa["id"], corpo.comentario, aceite_id, numero)).fetchone()
        if not a:
            raise HTTPException(409, "Este pedido não está mais aguardando decisão")
        alvo = _ROTULOS_CRITICIDADE[a["criticidade"]] if a["tipo"] == "criticidade" else None
        resumo["texto"] = f"Mudar a criticidade para {alvo}" if alvo else "Encerrar sem resolução"
        comentario = f" {corpo.comentario}" if corpo.comentario else ""
        # A pessoa do cliente não tem direito de mudar isso no motor: a permissão foi conferida aqui, e a conta da fachada grava.
        motor.acompanhamento(numero, operacoes.html(f"{decisao} por {pessoa['nome']}: {resumo['texto']}.{comentario}"))
        if corpo.aceito and a["tipo"] == "criticidade":
            motor.mudar_criticidade(numero, CRITICIDADES[a["criticidade"]])
            conn.execute("UPDATE chamados SET criticidade = %s, prazo_resposta_min = %s WHERE numero = %s",
                         (a["criticidade"], (cliente.get("prazos_resposta") or {}).get(a["criticidade"]), numero))
        elif corpo.aceito:
            motor.mudar_status(numero, ENCERRADO)

    saida = operacoes.transicao(cliente, numero, {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE, RESOLVIDO}, "aceite_decidido", pessoa["nome"], acao,
                                pessoa_id=pessoa["id"], antes=_pode_ver(pessoa), detalhe=f"{decisao}{' ' + corpo.comentario if corpo.comentario else ''}")
    return _do_cliente(saida, cliente)


@router.post("/clientes/{cliente_id}/chamados/{numero}/classificar")
def classificar(cliente_id: UUID, numero: int, corpo: Classificacao, pessoa=Depends(_exigir_analista)):
    cliente = _cliente(pessoa, cliente_id)
    return _operar(pessoa, cliente_id, numero, {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE, RESOLVIDO}, "classificado",
                   lambda motor, _c: motor.classificar(numero, TIPOS[corpo.tipo], cliente["categorias"][corpo.categoria]),
                   detalhe=f"{_ROTULOS_TIPO[corpo.tipo]}, {CATEGORIAS[corpo.categoria]}")


@router.post("/clientes/{cliente_id}/chamados/{numero}/andamento")
def andamento(cliente_id: UUID, numero: int, corpo: Texto, pessoa=Depends(_exigir_analista)):
    return _operar(pessoa, cliente_id, numero, {EM_ANDAMENTO, AGUARDANDO_CLIENTE}, "andamento",
                   lambda motor, _c: motor.acompanhamento(numero, operacoes.html(corpo.texto)))


@router.post("/clientes/{cliente_id}/chamados/{numero}/comentar")
def comentar(cliente_id: UUID, numero: int, corpo: Texto, pessoa=Depends(pessoa_pronta)):
    if pessoa["tipo"] != "cliente":
        raise HTTPException(403, "Sem permissão")
    return _operar(pessoa, cliente_id, numero, {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE}, "comentario",
                   lambda motor, _c: motor.acompanhamento(numero, operacoes.html(corpo.texto)))


@router.post("/clientes/{cliente_id}/chamados/{numero}/pausar")
def pausar(cliente_id: UUID, numero: int, corpo: Texto, pessoa=Depends(_exigir_analista)):
    def acao(motor, _conn):
        motor.acompanhamento(numero, operacoes.html("Aguardando o cliente: " + corpo.texto))
        motor.mudar_status(numero, AGUARDANDO_CLIENTE)
    return _operar(pessoa, cliente_id, numero, {EM_ANDAMENTO}, "aguardando_cliente", acao, detalhe=corpo.texto)


@router.post("/clientes/{cliente_id}/chamados/{numero}/retomar")
def retomar(cliente_id: UUID, numero: int, pessoa=Depends(_exigir_analista)):
    return _operar(pessoa, cliente_id, numero, {AGUARDANDO_CLIENTE}, "em_andamento",
                   lambda motor, _c: motor.mudar_status(numero, EM_ANDAMENTO))


@router.post("/clientes/{cliente_id}/chamados/{numero}/resolver")
def resolver(cliente_id: UUID, numero: int, corpo: Texto, pessoa=Depends(_exigir_analista)):
    return _operar(pessoa, cliente_id, numero, {EM_ANDAMENTO}, "resolvido",
                   lambda motor, _c: motor.solucao(numero, operacoes.html(corpo.texto)))


@router.post("/clientes/{cliente_id}/chamados/{numero}/encerrar")
def encerrar(cliente_id: UUID, numero: int, pessoa=Depends(_exigir_analista)):
    # Só sai de Resolvido: sem solução registrada, não há como encerrar.
    return _operar(pessoa, cliente_id, numero, {RESOLVIDO}, "encerrado",
                   lambda motor, conn: operacoes.encerrar(motor, conn, numero))


def _nome_seguro(nome):
    nome = PurePosixPath((nome or "").replace("\\", "/")).name
    nome = re.sub(r"[\x00-\x1f\x7f]", "", nome).strip()
    return nome[:200]


@router.post("/clientes/{cliente_id}/chamados/{numero}/anexos", status_code=201)
def anexar(cliente_id: UUID, numero: int, arquivo: UploadFile = File(...), pessoa=Depends(pessoa_pronta)):
    nome = _nome_seguro(arquivo.filename)
    if not nome or PurePosixPath(nome).suffix.lower() not in _EXTENSOES:
        raise HTTPException(415, "Tipo de arquivo não aceito")
    conteudo = arquivo.file.read(_TAMANHO_MAXIMO + 1)
    if len(conteudo) > _TAMANHO_MAXIMO:
        raise HTTPException(413, "O arquivo passa de 10 MB")
    if not conteudo:
        raise HTTPException(422, "Arquivo vazio")
    resumo = hashlib.sha256(conteudo).hexdigest()
    cliente = _cliente(pessoa, cliente_id)

    # O motor guarda o arquivo; o SHA-256 fica no histórico, que não muda.
    operacoes.transicao(cliente, numero, {ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE, RESOLVIDO}, "anexo", pessoa["nome"],
                        lambda motor, _c: motor.anexar(numero, nome, conteudo), escritor=motor_da_pessoa(pessoa, cliente),
                        pessoa_id=pessoa["id"], antes=_pode_ver(pessoa), detalhe=f"{nome}, SHA-256 {resumo}")
    return {"nome": nome, "tamanho": len(conteudo), "sha256": resumo}


@router.get("/clientes/{cliente_id}/chamados/{numero}/anexos/{documento_id}")
def baixar(cliente_id: UUID, numero: int, documento_id: int, pessoa=Depends(pessoa_pronta)):
    cliente = _cliente(pessoa, cliente_id)
    _carregar(pessoa, cliente, numero)
    # O documento precisa ser deste chamado: o id sozinho alcançaria qualquer arquivo da instância.
    achado = next((a for a in operacoes.anexos(cliente, numero) if a["id"] == documento_id), None)
    if not achado:
        raise HTTPException(404, "Anexo não encontrado")
    # Sempre como download e com tipo genérico: arquivo de fora nunca abre dentro da tela.
    return Response(motor_do_cliente(cliente).baixar_documento(documento_id), media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(achado["nome"])})
