"""Traz para a fila os chamados que nasceram no motor, como os vindos por e-mail."""
import json
import logging
import threading

from . import banco, operacoes
from .dominio import CRITICIDADES, representar
from .motor import MotorIndisponivel, motor_do_cliente

log = logging.getLogger("track.sincronia")
_INTERVALO = 15
_CRITICIDADE_POR_NIVEL = {v: k for k, v in CRITICIDADES.items()}


def _veio_por_email(chamado):
    return "mail" in ((chamado.get("request_type") or {}).get("name") or "").lower()


def _quem_pediu(conn, cliente, chamado):
    """O motor já identificou o remetente: procura a pessoa pelo usuário-sombra."""
    requerente = next((m["user"]["username"] for m in chamado.get("team") or [] if m.get("role") == "requester" and m.get("user")), None)
    return conn.execute(
        """SELECT p.id, p.nome, p.email FROM sombras s JOIN pessoas p ON p.id = s.pessoa_id
           WHERE s.cliente_id = %s AND s.motor_usuario = %s""", (cliente["id"], requerente)).fetchone()


def adotar(cliente):
    motor = motor_do_cliente(cliente)
    # Só o que veio por e-mail: quem nasce pela fachada grava a própria linha, na mesma transação.
    novos = [c for c in motor.recentes() if _veio_por_email(c)]
    if not novos:
        return 0
    adotados = 0
    with banco.transacao() as conn:
        pessoas = {c["id"]: _quem_pediu(conn, cliente, c) for c in novos}
    with banco.transacao(cliente["id"]) as conn:
        for chamado in novos:
            pessoa = pessoas[chamado["id"]]
            criticidade = _CRITICIDADE_POR_NIVEL.get(chamado["priority"], "media")
            solicitante = pessoa["nome"] if pessoa else "Remetente de e-mail"
            linha = conn.execute(
                """INSERT INTO chamados (cliente_id, numero, codigo, origem, criticidade, solicitante, pessoa_solicitante,
                                         contexto, contato_email, prazo_resposta_min)
                   VALUES (%s, %s, %s, 'email', %s, %s, %s, '{}', %s, %s)
                   ON CONFLICT (cliente_id, numero) DO NOTHING RETURNING *""",
                (cliente["id"], chamado["id"], f"{cliente['sigla']}-{chamado['id']:04d}", criticidade, solicitante,
                 pessoa["id"] if pessoa else None, pessoa["email"] if pessoa else None,
                 (cliente.get("prazos_resposta") or {}).get(criticidade))).fetchone()
            if linha:
                operacoes.registrar(conn, cliente["id"], chamado["id"], "aberto", solicitante, representar(linha, chamado),
                                    pessoa["id"] if pessoa else None)
                adotados += 1
    return adotados


def sincronizar():
    with banco.transacao() as conn:
        clientes = conn.execute("SELECT * FROM clientes").fetchall()
    for cliente in clientes:
        try:
            adotar(cliente)
        except MotorIndisponivel as e:
            # Um cliente fora do ar não atrasa os outros.
            log.warning("cliente %s: %s", cliente["nome"], e)


def iniciar(parar):
    def laco():
        while not parar.wait(_INTERVALO):
            try:
                sincronizar()
            except Exception:
                log.exception("falha na sincronia")
    t = threading.Thread(target=laco, daemon=True, name="sincronia")
    t.start()
    return t
