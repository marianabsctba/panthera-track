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
from .dominio import (ABERTO, AGUARDANDO_CLIENTE, CATEGORIAS, EM_ANDAMENTO, ENCERRADO, PADRAO_CATEGORIA, PADRAO_CRITICIDADE,
                      PADRAO_TIPO, RESOLVIDO, TIPOS, representar, tempos_do_motor)
from .motor import MotorIndisponivel, motor_da_pessoa, motor_do_cliente
from .sessao import checar_origem, pessoa_pronta

router = APIRouter(prefix="/api/v1/tela", dependencies=[Depends(checar_origem)])
_LIMITE_POR_CLIENTE = 200
_TAMANHO_MAXIMO = 10 * 1024 * 1024
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
    dados = {d["id"]: d for d in motor_do_cliente(cliente).chamados([x["numero"] for x in linhas])}
    return [_do_cliente(representar(x, dados[x["numero"]]), cliente) for x in linhas if x["numero"] in dados]


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


@router.get("/clientes/{cliente_id}/chamados/{numero}")
def consultar(cliente_id: UUID, numero: int, pessoa=Depends(pessoa_pronta)):
    cliente = _cliente(pessoa, cliente_id)
    linha = _carregar(pessoa, cliente, numero)
    chamado = motor_do_cliente(cliente).chamado(numero)
    return {**_do_cliente(representar(linha, chamado), cliente),
            "contato": {"telefone": linha["contato_telefone"], "email": linha["contato_email"]},
            "tempos": tempos_do_motor(chamado), "pausas": operacoes.pausas(cliente, numero), "anexos": operacoes.anexos(cliente, numero)}


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
        conn.execute("UPDATE chamados SET atendente = %s, pessoa_atendente = %s WHERE numero = %s", (pessoa["nome"], pessoa["id"], numero))
        motor.mudar_status(numero, EM_ANDAMENTO)
    return _operar(pessoa, cliente_id, numero, {ABERTO}, "em_andamento", acao)


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
                   lambda motor, _c: motor.mudar_status(numero, ENCERRADO))


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
