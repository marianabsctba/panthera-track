import hashlib
import hmac
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from html import escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import banco, entrega
from .dominio import (ABERTO, AGUARDANDO_CLIENTE, CRITICIDADES, EM_ANDAMENTO, ENCERRADO, RESOLVIDO, STATUS,
                      representar)
from .motor import MotorIndisponivel, motor_do_cliente

log = logging.getLogger("track.api")
_JANELA_ASSINATURA = 300


@asynccontextmanager
async def _vida(_app):
    parar = threading.Event()
    entrega.iniciar(parar)
    yield
    parar.set()
    banco.fechar()


app = FastAPI(title="Panthera Track", lifespan=_vida, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(MotorIndisponivel)
def _motor_fora(_request, erro):
    log.error("motor: %s", erro)
    return JSONResponse({"detail": "Serviço de chamados indisponível"}, status_code=503)


class Identidade:
    def __init__(self, cliente, papel, chave):
        self.cliente = cliente
        self.papel = papel
        self.chave = chave


async def autenticar(request: Request):
    chave = request.headers.get("X-Track-Chave", "")
    carimbo = request.headers.get("X-Track-Data", "")
    assinatura = request.headers.get("X-Track-Assinatura", "")
    negado = HTTPException(401, "Assinatura inválida")
    if not carimbo.isdigit() or abs(time.time() - int(carimbo)) > _JANELA_ASSINATURA:
        raise negado
    with banco.transacao() as conn:
        linha = conn.execute(
            """SELECT cr.segredo, cr.papel, row_to_json(c) AS cliente FROM credenciais cr
               JOIN clientes c ON c.id = cr.cliente_id WHERE cr.chave = %s AND cr.revogada_em IS NULL""",
            (chave,)).fetchone()
    if not linha:
        raise negado
    corpo = await request.body()
    alvo = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    mensagem = f"{carimbo}.{request.method}.{alvo}.{hashlib.sha256(corpo).hexdigest()}"
    esperada = hmac.new(banco.decifrar(linha["segredo"]).encode(), mensagem.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, assinatura):
        raise negado
    return Identidade(linha["cliente"], linha["papel"], chave)


def exigir_siem(eu: Identidade = Depends(autenticar)):
    if eu.papel != "siem":
        raise HTTPException(403, "Sem permissão")
    return eu


def exigir_atendente(eu: Identidade = Depends(autenticar)):
    if eu.papel != "atendente":
        raise HTTPException(403, "Sem permissão")
    return eu


class NovoChamado(BaseModel):
    id_externo: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    titulo: str = Field(min_length=1, max_length=255)
    descricao: str = Field(min_length=1, max_length=65000)
    criticidade: str = Field(pattern="^(critica|alta|media|baixa|consulta)$")
    solicitante: str = Field(min_length=1, max_length=255)


class Atendimento(BaseModel):
    atendente: str = Field(min_length=1, max_length=255)


class Texto(BaseModel):
    texto: str = Field(min_length=1, max_length=65000)


def _html(texto):
    return "<p>" + escape(texto) + "</p>"


def _registrar(conn, cliente_id, numero, tipo, autor, dados):
    seq = conn.execute(
        "UPDATE chamados SET ultima_seq = ultima_seq + 1 WHERE numero = %s RETURNING ultima_seq",
        (numero,)).fetchone()["ultima_seq"]
    conn.execute(
        "INSERT INTO eventos (cliente_id, numero, sequencia, tipo, autor, dados) VALUES (%s, %s, %s, %s, %s, %s)",
        (cliente_id, numero, seq, tipo, autor, json.dumps(dados, ensure_ascii=False)))


@app.post("/api/v1/siem/chamados")
def abrir_pelo_siem(novo: NovoChamado, eu: Identidade = Depends(exigir_siem)):
    cliente = eu.cliente
    motor = motor_do_cliente(cliente)
    nivel = CRITICIDADES[novo.criticidade]
    with banco.transacao(cliente["id"]) as conn:
        # A trava serializa reenvios simultâneos do mesmo alerta até o commit.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{cliente['id']}:{novo.id_externo}",))
        linha = conn.execute("SELECT * FROM chamados WHERE id_externo = %s", (novo.id_externo,)).fetchone()
        if linha:
            return JSONResponse(representar(linha, motor.chamado(linha["numero"])), status_code=200)
        # Se caiu entre criar no motor e gravar aqui, o reenvio reencontra o mesmo chamado.
        numero = motor.buscar_externo(novo.id_externo) or motor.criar_chamado(
            name=novo.titulo, content=_html(novo.descricao), type=1,
            urgency=nivel, impact=nivel, priority=nivel,
            external_id=novo.id_externo, entity=0)
        linha = conn.execute(
            """INSERT INTO chamados (cliente_id, numero, id_externo, origem, criticidade, solicitante)
               VALUES (%s, %s, %s, 'siem', %s, %s) RETURNING *""",
            (cliente["id"], numero, novo.id_externo, novo.criticidade, novo.solicitante)).fetchone()
        saida = representar(linha, motor.chamado(numero))
        _registrar(conn, cliente["id"], numero, "aberto", novo.solicitante, saida)
    return JSONResponse(saida, status_code=201)


@app.get("/api/v1/siem/chamados/{id_externo}")
def consultar_externo(id_externo: str, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE id_externo = %s", (id_externo,)).fetchone()
    if not linha:
        raise HTTPException(404, "Chamado não encontrado")
    return representar(linha, motor_do_cliente(eu.cliente).chamado(linha["numero"]))


def _carregar(conn, numero):
    linha = conn.execute("SELECT * FROM chamados WHERE numero = %s FOR UPDATE", (numero,)).fetchone()
    if not linha:
        raise HTTPException(404, "Chamado não encontrado")
    return linha


def _transicao(eu, numero, permitidos, tipo, autor, acao):
    """Confere o status no motor, aplica a ação e registra o evento na mesma transação."""
    cliente = eu.cliente
    motor = motor_do_cliente(cliente)
    with banco.transacao(cliente["id"]) as conn:
        _carregar(conn, numero)
        atual = motor.chamado(numero)["status"]["id"]
        if atual not in permitidos:
            raise HTTPException(409, f"Ação não permitida com o chamado {STATUS[atual]}")
        acao(motor, conn)
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s", (numero,)).fetchone()
        saida = representar(linha, motor.chamado(numero))
        _registrar(conn, cliente["id"], numero, tipo, autor, saida)
    return saida


@app.get("/api/v1/chamados/{numero}")
def consultar(numero: int, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s", (numero,)).fetchone()
    if not linha:
        raise HTTPException(404, "Chamado não encontrado")
    return representar(linha, motor_do_cliente(eu.cliente).chamado(numero))


@app.post("/api/v1/chamados/{numero}/atender")
def atender(numero: int, corpo: Atendimento, eu: Identidade = Depends(exigir_atendente)):
    def acao(motor, conn):
        conn.execute("UPDATE chamados SET atendente = %s WHERE numero = %s", (corpo.atendente, numero))
        motor.mudar_status(numero, EM_ANDAMENTO)
    return _transicao(eu, numero, {ABERTO}, "em_andamento", corpo.atendente, acao)


@app.post("/api/v1/chamados/{numero}/andamento")
def andamento(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    return _transicao(eu, numero, {EM_ANDAMENTO, AGUARDANDO_CLIENTE}, "andamento", _quem(eu, numero),
                      lambda motor, _c: motor.acompanhamento(numero, _html(corpo.texto)))


@app.post("/api/v1/chamados/{numero}/pausar")
def pausar(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    def acao(motor, _conn):
        motor.acompanhamento(numero, _html("Aguardando o cliente: " + corpo.texto))
        motor.mudar_status(numero, AGUARDANDO_CLIENTE)
    return _transicao(eu, numero, {EM_ANDAMENTO}, "aguardando_cliente", _quem(eu, numero), acao)


@app.post("/api/v1/chamados/{numero}/retomar")
def retomar(numero: int, eu: Identidade = Depends(exigir_atendente)):
    return _transicao(eu, numero, {AGUARDANDO_CLIENTE}, "em_andamento", _quem(eu, numero),
                      lambda motor, _c: motor.mudar_status(numero, EM_ANDAMENTO))


@app.post("/api/v1/chamados/{numero}/resolver")
def resolver(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    return _transicao(eu, numero, {EM_ANDAMENTO}, "resolvido", _quem(eu, numero),
                      lambda motor, _c: motor.solucao(numero, _html(corpo.texto)))


@app.post("/api/v1/chamados/{numero}/encerrar")
def encerrar(numero: int, eu: Identidade = Depends(exigir_atendente)):
    # Só sai de Resolvido: sem solução registrada, não há como encerrar.
    return _transicao(eu, numero, {RESOLVIDO}, "encerrado", _quem(eu, numero),
                      lambda motor, _c: motor.mudar_status(numero, ENCERRADO))


def _quem(eu, numero):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT atendente FROM chamados WHERE numero = %s", (numero,)).fetchone()
    return (linha or {}).get("atendente") or "atendimento"


@app.get("/api/v1/chamados/{numero}/historico")
def historico(numero: int, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT 1 FROM chamados WHERE numero = %s", (numero,)).fetchone()
        if not linha:
            raise HTTPException(404, "Chamado não encontrado")
        eventos = conn.execute(
            "SELECT sequencia, tipo, autor, criado_em, dados->>'status' AS status FROM eventos WHERE numero = %s ORDER BY sequencia",
            (numero,)).fetchall()
    interacoes = [{
        "tipo": {"Followup": "acompanhamento", "Solution": "solucao"}.get(i["type"], "outro"),
        "conteudo": i["item"].get("content"),
        "em": i["item"].get("date_creation"),
    } for i in motor_do_cliente(eu.cliente).linha_do_tempo(numero)]
    return {
        "numero": numero,
        "eventos": [{**e, "criado_em": e["criado_em"].isoformat(timespec="seconds")} for e in eventos],
        "interacoes": interacoes,
    }
