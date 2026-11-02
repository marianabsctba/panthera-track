"""Rotas da tela. Cada pessoa escreve no motor com o próprio usuário-sombra; a leitura passa por aqui, depois da permissão."""
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import banco, operacoes
from .dominio import ABERTO, AGUARDANDO_CLIENTE, EM_ANDAMENTO, ENCERRADO, RESOLVIDO, representar
from .motor import MotorIndisponivel, motor_da_pessoa, motor_do_cliente
from .sessao import checar_origem, pessoa_pronta

router = APIRouter(prefix="/api/v1/tela", dependencies=[Depends(checar_origem)])
_LIMITE_POR_CLIENTE = 200


class NovoChamado(BaseModel):
    titulo: str = Field(min_length=1, max_length=255)
    descricao: str = Field(min_length=1, max_length=65000)
    criticidade: str = Field(pattern="^(critica|alta|media|baixa|consulta)$")
    cliente_id: Optional[UUID] = None
    solicitante: Optional[str] = Field(default=None, min_length=1, max_length=255)


class Texto(BaseModel):
    texto: str = Field(min_length=1, max_length=65000)


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


def _fila_do_cliente(pessoa, cliente):
    with banco.transacao(cliente["id"]) as conn:
        if _ve_tudo(pessoa):
            linhas = conn.execute("SELECT * FROM chamados ORDER BY numero DESC LIMIT %s", (_LIMITE_POR_CLIENTE,)).fetchall()
        else:
            linhas = conn.execute("SELECT * FROM chamados WHERE pessoa_solicitante = %s ORDER BY numero DESC LIMIT %s",
                                  (pessoa["id"], _LIMITE_POR_CLIENTE)).fetchall()
    dados = {d["id"]: d for d in motor_do_cliente(cliente).chamados([x["numero"] for x in linhas])}
    return [{**representar(x, dados[x["numero"]]), "cliente_id": str(cliente["id"]), "cliente": cliente["nome"]}
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
    if pessoa["tipo"] == "cliente":
        cliente = _cliente(pessoa, pessoa["cliente_id"])
        saida, _ = operacoes.abrir(cliente, novo.titulo, novo.descricao, novo.criticidade, pessoa["nome"], "portal",
                                   escritor=motor_da_pessoa(pessoa, cliente), pessoa_id=pessoa["id"])
    else:
        # Analista registra o que chegou por telefone, em nome de quem ligou.
        if not novo.cliente_id or not novo.solicitante:
            raise HTTPException(422, "Informe o cliente e quem pediu")
        cliente = _cliente(pessoa, novo.cliente_id)
        saida, _ = operacoes.abrir(cliente, novo.titulo, novo.descricao, novo.criticidade, novo.solicitante, "telefone",
                                   escritor=motor_da_pessoa(pessoa, cliente))
    return {**saida, "cliente_id": str(cliente["id"]), "cliente": cliente["nome"]}


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
    return {**representar(linha, motor_do_cliente(cliente).chamado(numero)), "cliente_id": str(cliente["id"]), "cliente": cliente["nome"]}


@router.get("/clientes/{cliente_id}/chamados/{numero}/historico")
def historico(cliente_id: UUID, numero: int, pessoa=Depends(pessoa_pronta)):
    cliente = _cliente(pessoa, cliente_id)
    _carregar(pessoa, cliente, numero)
    return operacoes.historico(cliente, numero)


def _operar(pessoa, cliente_id, numero, permitidos, tipo, acao):
    cliente = _cliente(pessoa, cliente_id)
    saida = operacoes.transicao(cliente, numero, permitidos, tipo, pessoa["nome"], acao,
                                escritor=motor_da_pessoa(pessoa, cliente), pessoa_id=pessoa["id"], antes=_pode_ver(pessoa))
    return {**saida, "cliente_id": str(cliente["id"]), "cliente": cliente["nome"]}


@router.post("/clientes/{cliente_id}/chamados/{numero}/assumir")
def assumir(cliente_id: UUID, numero: int, pessoa=Depends(_exigir_analista)):
    def acao(motor, conn):
        conn.execute("UPDATE chamados SET atendente = %s, pessoa_atendente = %s WHERE numero = %s", (pessoa["nome"], pessoa["id"], numero))
        motor.mudar_status(numero, EM_ANDAMENTO)
    return _operar(pessoa, cliente_id, numero, {ABERTO}, "em_andamento", acao)


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
    return _operar(pessoa, cliente_id, numero, {EM_ANDAMENTO}, "aguardando_cliente", acao)


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
