import hashlib
import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import banco, entrega, operacoes, sessao, tela
from .dominio import ABERTO, AGUARDANDO_CLIENTE, EM_ANDAMENTO, ENCERRADO, RESOLVIDO, representar
from .motor import MotorIndisponivel, motor_do_cliente

log = logging.getLogger("track.api")
_JANELA_ASSINATURA = 300
_ESTATICO = Path(__file__).resolve().parent / "estatico"


@asynccontextmanager
async def _vida(_app):
    parar = threading.Event()
    entrega.iniciar(parar)
    yield
    parar.set()
    banco.fechar()


app = FastAPI(title="Panthera Track", lifespan=_vida, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(sessao.router)
app.include_router(tela.router)
app.mount("/estatico", StaticFiles(directory=_ESTATICO), name="estatico")


@app.middleware("http")
async def _cabecalhos(request, chamar):
    resposta = await chamar(request)
    resposta.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(_ESTATICO / "console.html")


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


@app.post("/api/v1/siem/chamados")
def abrir_pelo_siem(novo: NovoChamado, eu: Identidade = Depends(exigir_siem)):
    saida, criado = operacoes.abrir(eu.cliente, novo.titulo, novo.descricao, novo.criticidade, novo.solicitante, "siem",
                                    id_externo=novo.id_externo)
    return JSONResponse(saida, status_code=201 if criado else 200)


@app.get("/api/v1/siem/chamados/{id_externo}")
def consultar_externo(id_externo: str, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE id_externo = %s", (id_externo,)).fetchone()
    if not linha:
        raise HTTPException(404, "Chamado não encontrado")
    return representar(linha, motor_do_cliente(eu.cliente).chamado(linha["numero"]))


@app.get("/api/v1/chamados")
def listar(eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linhas = conn.execute("SELECT * FROM chamados ORDER BY numero DESC LIMIT 100").fetchall()
    motor = motor_do_cliente(eu.cliente)
    dados = {d["id"]: d for d in motor.chamados([x["numero"] for x in linhas])}
    return [representar(x, dados[x["numero"]]) for x in linhas if x["numero"] in dados]


@app.get("/api/v1/chamados/{numero}")
def consultar(numero: int, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s", (numero,)).fetchone()
    if not linha:
        raise HTTPException(404, "Chamado não encontrado")
    return representar(linha, motor_do_cliente(eu.cliente).chamado(numero))


def _quem(eu, numero):
    with banco.transacao(eu.cliente["id"]) as conn:
        linha = conn.execute("SELECT atendente FROM chamados WHERE numero = %s", (numero,)).fetchone()
    return (linha or {}).get("atendente") or "atendimento"


@app.post("/api/v1/chamados/{numero}/atender")
def atender(numero: int, corpo: Atendimento, eu: Identidade = Depends(exigir_atendente)):
    def acao(motor, conn):
        conn.execute("UPDATE chamados SET atendente = %s WHERE numero = %s", (corpo.atendente, numero))
        motor.mudar_status(numero, EM_ANDAMENTO)
    return operacoes.transicao(eu.cliente, numero, {ABERTO}, "em_andamento", corpo.atendente, acao)


@app.post("/api/v1/chamados/{numero}/andamento")
def andamento(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    return operacoes.transicao(eu.cliente, numero, {EM_ANDAMENTO, AGUARDANDO_CLIENTE}, "andamento", _quem(eu, numero),
                               lambda motor, _c: motor.acompanhamento(numero, operacoes.html(corpo.texto)))


@app.post("/api/v1/chamados/{numero}/pausar")
def pausar(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    def acao(motor, _conn):
        motor.acompanhamento(numero, operacoes.html("Aguardando o cliente: " + corpo.texto))
        motor.mudar_status(numero, AGUARDANDO_CLIENTE)
    return operacoes.transicao(eu.cliente, numero, {EM_ANDAMENTO}, "aguardando_cliente", _quem(eu, numero), acao)


@app.post("/api/v1/chamados/{numero}/retomar")
def retomar(numero: int, eu: Identidade = Depends(exigir_atendente)):
    return operacoes.transicao(eu.cliente, numero, {AGUARDANDO_CLIENTE}, "em_andamento", _quem(eu, numero),
                               lambda motor, _c: motor.mudar_status(numero, EM_ANDAMENTO))


@app.post("/api/v1/chamados/{numero}/resolver")
def resolver(numero: int, corpo: Texto, eu: Identidade = Depends(exigir_atendente)):
    return operacoes.transicao(eu.cliente, numero, {EM_ANDAMENTO}, "resolvido", _quem(eu, numero),
                               lambda motor, _c: motor.solucao(numero, operacoes.html(corpo.texto)))


@app.post("/api/v1/chamados/{numero}/encerrar")
def encerrar(numero: int, eu: Identidade = Depends(exigir_atendente)):
    # Só sai de Resolvido: sem solução registrada, não há como encerrar.
    return operacoes.transicao(eu.cliente, numero, {RESOLVIDO}, "encerrado", _quem(eu, numero),
                               lambda motor, _c: motor.mudar_status(numero, ENCERRADO))


@app.get("/api/v1/chamados/{numero}/historico")
def historico(numero: int, eu: Identidade = Depends(autenticar)):
    with banco.transacao(eu.cliente["id"]) as conn:
        if not conn.execute("SELECT 1 FROM chamados WHERE numero = %s", (numero,)).fetchone():
            raise HTTPException(404, "Chamado não encontrado")
    return operacoes.historico(eu.cliente, numero)
