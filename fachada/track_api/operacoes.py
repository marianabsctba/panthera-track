"""Regras do chamado, comuns à API assinada e à tela."""
import json
import logging
from html import escape

from fastapi import HTTPException

from . import banco
from .dominio import CRITICIDADES, ENCERRADO, STATUS, TIPOS, pausas_dos_eventos, representar
from .motor import MotorIndisponivel, motor_do_cliente

log = logging.getLogger("track.operacoes")


def html(texto):
    return "<p>" + escape(texto) + "</p>"


def _ligar_pessoas(cliente, numero, pessoa_id):
    """Quem abriu vira requerente e os gestores do cliente, observadores: é assim que o motor sabe quem avisar.

    O chamado já existe. Falhar aqui atrapalha só os avisos, então não desfaz a abertura.
    """
    with banco.transacao() as conn:
        ligacoes = conn.execute(
            """SELECT s.motor_id, p.id = %s AS requerente FROM sombras s JOIN pessoas p ON p.id = s.pessoa_id
               WHERE s.cliente_id = %s AND s.motor_id IS NOT NULL AND p.ativo AND (p.id = %s OR p.papel = 'gestor')""",
            (pessoa_id, cliente["id"], pessoa_id)).fetchall()
    motor = motor_do_cliente(cliente)
    for ligacao in ligacoes:
        try:
            motor.ligar_pessoa(numero, ligacao["motor_id"], "requester" if ligacao["requerente"] else "observer")
        except MotorIndisponivel as e:
            log.warning("chamado %s sem pessoa ligada: %s", numero, e)


def registrar(conn, cliente_id, numero, tipo, autor, dados, pessoa_id=None, detalhe=None):
    seq = conn.execute(
        "UPDATE chamados SET ultima_seq = ultima_seq + 1 WHERE numero = %s RETURNING ultima_seq",
        (numero,)).fetchone()["ultima_seq"]
    conn.execute(
        """INSERT INTO eventos (cliente_id, numero, sequencia, tipo, autor, dados, pessoa_id, detalhe)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (cliente_id, numero, seq, tipo, autor, json.dumps(dados, ensure_ascii=False), pessoa_id, detalhe))


def abrir(cliente, titulo, descricao, criticidade, solicitante, origem, escritor=None, id_externo=None, pessoa_id=None,
          tipo="incidente", categoria="outro", contexto=None, contato_telefone=None, contato_email=None):
    """Grava no motor com quem abriu e registra o primeiro evento. Sem escritor, usa a conta da fachada."""
    leitor = motor_do_cliente(cliente)
    escritor = escritor or leitor
    nivel = CRITICIDADES[criticidade]
    with banco.transacao(cliente["id"]) as conn:
        if id_externo:
            # A trava serializa reenvios simultâneos do mesmo alerta até o commit.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{cliente['id']}:{id_externo}",))
            linha = conn.execute("SELECT * FROM chamados WHERE id_externo = %s", (id_externo,)).fetchone()
            if linha:
                return representar(linha, leitor.chamado(linha["numero"])), False
        campos = dict(name=titulo, content=html(descricao), type=TIPOS[tipo], category=cliente["categorias"][categoria],
                      urgency=nivel, impact=nivel, priority=nivel, entity=0)
        if id_externo:
            campos["external_id"] = id_externo
        # Se caiu entre criar no motor e gravar aqui, o reenvio reencontra o mesmo chamado.
        numero = (id_externo and leitor.buscar_externo(id_externo)) or escritor.criar_chamado(**campos)
        linha = conn.execute(
            """INSERT INTO chamados (cliente_id, numero, codigo, id_externo, origem, criticidade, solicitante, pessoa_solicitante,
                                     contexto, contato_telefone, contato_email, prazo_resposta_min)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (cliente["id"], numero, f"{cliente['sigla']}-{numero:04d}", id_externo, origem, criticidade, solicitante, pessoa_id,
             json.dumps(contexto or {}, ensure_ascii=False), contato_telefone, contato_email,
             (cliente.get("prazos_resposta") or {}).get(criticidade))).fetchone()
        saida = representar(linha, leitor.chamado(numero))
        registrar(conn, cliente["id"], numero, "aberto", solicitante, saida, pessoa_id)
    _ligar_pessoas(cliente, numero, pessoa_id)
    return saida, True


def transicao(cliente, numero, permitidos, tipo, autor, acao, escritor=None, pessoa_id=None, antes=None, detalhe=None):
    """Confere o status no motor, aplica a ação e registra o evento na mesma transação."""
    leitor = motor_do_cliente(cliente)
    with banco.transacao(cliente["id"]) as conn:
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s FOR UPDATE", (numero,)).fetchone()
        if not linha or (antes and not antes(linha)):
            raise HTTPException(404, "Chamado não encontrado")
        atual = leitor.chamado(numero)["status"]["id"]
        if atual not in permitidos:
            raise HTTPException(409, f"Ação não permitida com o chamado {STATUS[atual]}")
        acao(escritor or leitor, conn)
        linha = conn.execute("SELECT * FROM chamados WHERE numero = %s", (numero,)).fetchone()
        saida = representar(linha, leitor.chamado(numero))
        registrar(conn, cliente["id"], numero, tipo, autor, saida, pessoa_id, detalhe)
    return saida


def assumir(conn, numero, atendente, pessoa_id=None):
    if conn.execute("SELECT atendente FROM chamados WHERE numero = %s", (numero,)).fetchone()["atendente"]:
        raise HTTPException(409, "O chamado já tem atendente")
    conn.execute(
        """UPDATE chamados SET atendente = %s, pessoa_atendente = %s, primeira_resposta_em = COALESCE(primeira_resposta_em, now())
           WHERE numero = %s""", (atendente, pessoa_id, numero))


def encerrar(motor, conn, numero):
    # Pedido em aberto não sobrevive ao encerramento: não haveria mais o que aceitar.
    conn.execute(
        """UPDATE aceites SET estado = 'recusado', decidido_por = 'Atendimento', decidido_em = now(),
           comentario = 'Chamado encerrado antes da decisão' WHERE numero = %s AND estado = 'pendente'""", (numero,))
    motor.mudar_status(numero, ENCERRADO)


def _eventos(cliente, numero):
    with banco.transacao(cliente["id"]) as conn:
        return conn.execute(
            "SELECT sequencia, tipo, autor, criado_em, detalhe, dados->>'status' AS status FROM eventos WHERE numero = %s ORDER BY sequencia",
            (numero,)).fetchall()


def _nomes(cliente):
    with banco.transacao() as conn:
        return {x["motor_usuario"]: x["nome"] for x in conn.execute(
            "SELECT s.motor_usuario, p.nome FROM sombras s JOIN pessoas p ON p.id = s.pessoa_id WHERE s.cliente_id = %s",
            (cliente["id"],)).fetchall()}


def historico(cliente, numero):
    eventos = _eventos(cliente, numero)
    nomes = _nomes(cliente)
    interacoes = [{
        "tipo": {"Followup": "acompanhamento", "Solution": "solucao"}[i["type"]],
        "conteudo": i["item"].get("content"),
        "autor": nomes.get((i["item"].get("user") or {}).get("username"), "Atendimento"),
        "em": i["item"].get("date_creation"),
    } for i in motor_do_cliente(cliente).linha_do_tempo(numero) if i["type"] in ("Followup", "Solution")]
    return {
        "numero": numero,
        "eventos": [{**e, "criado_em": e["criado_em"].isoformat(timespec="seconds")} for e in eventos],
        "interacoes": interacoes,
    }


def pausas(cliente, numero, ainda_pausado):
    return pausas_dos_eventos(_eventos(cliente, numero), ainda_pausado)


def anexos(cliente, numero):
    motor, nomes, saida = motor_do_cliente(cliente), _nomes(cliente), []
    for item in motor.documentos(numero):
        doc = motor.documento(item["item"]["documents_id"])
        saida.append({"id": doc["id"], "nome": doc.get("filename") or doc["name"], "criado_em": doc["date_creation"],
                      "sha1": doc.get("sha1sum"), "autor": nomes.get((doc.get("user") or {}).get("username"), "Atendimento")})
    return saida
