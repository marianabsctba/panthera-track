"""Cria um cliente: uma instância própria do motor, SLAs e credenciais.

python -m track_api.provisao criar --nome acme --prazos '{"critica":4,"alta":8,"media":24,"baixa":48,"consulta":72}'
"""
import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import unicodedata
import uuid
from pathlib import Path

import httpx
import psycopg

from .banco import cifrar
from .dominio import CATEGORIAS, CRITICIDADES, NIVEIS
from .motor import PERFIL_FACHADA, Motor

RAIZ = Path(__file__).resolve().parents[2]
CLIENTES = Path(os.environ.get("TRACK_CLIENTES", "/opt/panthera-track/clientes"))
_CONTAS_PADRAO = ("tech", "normal", "post-only")
_REGRA_AO_CRIAR_OU_ALTERAR = 3
# Ponto de partida para quem não informa; o combinado com cada cliente vai em --prazos-resposta.
PRAZOS_RESPOSTA_PADRAO = {"critica": 15, "alta": 30, "media": 60, "baixa": 240, "consulta": 480}
# O que uma equipe de segurança anexa e a instalação padrão não aceita.
_EXTENSOES_EXTRAS = {"log": "text/plain", "json": "application/json", "eml": "message/rfc822", "pcap": "application/vnd.tcpdump.pcap"}
_TENTATIVAS_WEBHOOK = 5
_DESTINO_USUARIO = 1  # Notification::USER_TYPE
_QUEM_ESCREVEU = 7  # Notification::RECIPIENT
_CARGA_WEBHOOK = '{"numero":{{ item.id }},"id_externo":"{{ item.external_id }}","status":{{ item.status.id }},"atualizado_em":"{{ item.date_mod }}"}'


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _compose(pasta, projeto, *args, **k):
    return subprocess.run(["docker", "compose", "--project-directory", str(pasta), "--project-name", projeto, *args],
                          check=True, capture_output=True, text=True, **k)


def _console(pasta, projeto, *args):
    return _compose(pasta, projeto, "exec", "-T", "glpi", "php", "bin/console", *args, "-n")


def _subir(pasta, projeto, porta):
    _compose(pasta, projeto, "up", "-d")
    url = f"http://127.0.0.1:{porta}"
    # A primeira subida instala o banco inteiro e pode levar minutos numa máquina ocupada.
    for _ in range(300):
        try:
            if httpx.get(url, timeout=5).status_code in (200, 302):
                return url
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("a instância não respondeu a tempo")


def _sigla_livre(nome):
    """Última palavra do nome, em maiúsculas; com número no fim se já houver outra igual."""
    palavras = re.findall(r"[A-Z0-9]+", unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().upper())
    base = (palavras[-1] if palavras else "CLI")[:6].ljust(2, "X")
    with psycopg.connect(os.environ["TRACK_BANCO_DONO_DSN"]) as conn:
        usadas = {x[0] for x in conn.execute("SELECT sigla FROM clientes").fetchall()}
    if base not in usadas:
        return base
    return next(f"{base}{n}" for n in range(2, 100) if f"{base}{n}" not in usadas)


def _rodar_php(pasta, projeto, arquivo, dados):
    return _compose(pasta, projeto, "exec", "-T", "glpi", "php", "--", json.dumps(dados),
                    input=(RAIZ / "infra/glpi" / arquivo).read_text()).stdout


def _configurar_correio(pasta, projeto, admin, correio, sigla):
    """E-mail é do motor: coletor, regras, avisos e resposta. Aqui só se liga e se ajusta o que ele traz de fábrica."""
    _rodar_php(pasta, projeto, "configurar_correio.php", correio)
    remetente = correio["remetente"]
    admin.pedir("PATCH", "/Administration/Entity/0", json={
        "admin_email": remetente, "admin_email_name": "Atendimento", "from_email": remetente, "from_email_name": "Atendimento",
        "replyto_email": remetente, "notification_subject_tag": sigla, "mailing_signature": "Atendimento"})
    admin.pedir("POST", "/Setup/EmailCollector", json={
        "name": remetente, "host": correio["imap_caixa"], "login": correio["imap_usuario"], "password": correio["imap_senha"],
        "is_active": True, "collect_only_unread": True, "use_mail_date": False, "create_user_from_email": False})
    # A pessoa só vira requerente depois de criado o chamado: sem isto, o primeiro aviso não chegaria a quem abriu.
    for notificacao in admin.pedir("GET", "/Notifications/Notification", params={"limit": 200, "filter": "itemtype==Ticket"}):
        if notificacao["event"] == "update":
            admin.pedir("PATCH", f"/Notifications/Notification/{notificacao['id']}", json={"is_active": True})
        elif notificacao["event"] == "requester_user":
            admin.pedir("PATCH", f"/Notifications/Notification/{notificacao['id']}", json={"is_active": False})
        elif notificacao["event"] == "new":
            admin.pedir("POST", f"/Notifications/Notification/{notificacao['id']}/Recipient", json={"type": _DESTINO_USUARIO, "items_id": _QUEM_ESCREVEU})


def criar_niveis_de_sla(pasta, projeto, sla, grupos, segundos_do_prazo):
    """Vencido o prazo, vai para N2; passada mais metade dele, para N3. Só muda o grupo, nunca a criticidade."""
    niveis = [{"nome": "N2 no vencimento", "segundos": 0, "grupo": grupos["N2"]},
              {"nome": "N3 depois do vencimento", "segundos": segundos_do_prazo // 2, "grupo": grupos["N3"]}]
    _rodar_php(pasta, projeto, "criar_niveis_sla.php", {"sla": sla, "niveis": niveis})


def criar(nome, prazos, webhook_url=None, prazos_resposta=None, sigla=None, webhook_interno=False, correio=None):
    if set(prazos) != set(CRITICIDADES):
        raise ValueError("informe o prazo em horas das cinco criticidades")
    prazos_resposta = prazos_resposta or PRAZOS_RESPOSTA_PADRAO
    if set(prazos_resposta) != set(CRITICIDADES):
        raise ValueError("informe o prazo de resposta em minutos das cinco criticidades")
    sigla = sigla or _sigla_livre(nome)
    if not re.fullmatch(r"[A-Z0-9]{2,8}", sigla):
        raise ValueError("a sigla tem de 2 a 8 letras maiúsculas ou números")
    identificador = uuid.uuid4().hex[:12]
    pasta = CLIENTES / identificador
    pasta.mkdir(parents=True)
    try:
        return _montar(identificador, pasta, nome, prazos, webhook_url, prazos_resposta, sigla, webhook_interno, correio)
    except Exception:
        # Instância pela metade não fica para trás, com senha padrão ou porta aberta.
        if (pasta / ".env").exists():
            remover(identificador)
        else:
            shutil.rmtree(pasta)
        raise


def _montar(identificador, pasta, nome, prazos, webhook_url, prazos_resposta, sigla, webhook_interno, correio):
    projeto = f"track-{identificador}"
    os.chmod(pasta, 0o700)
    shutil.copy(RAIZ / "infra/glpi/docker-compose.yml", pasta / "docker-compose.yml")

    porta = _porta_livre()
    senha_admin = secrets.token_hex(24)
    senha_fachada = secrets.token_hex(24)
    env = pasta / ".env"
    env.touch(mode=0o600)
    env.write_text(f"SENHA_BANCO={secrets.token_hex(24)}\nPORTA_GLPI={porta}\n")

    # O motor recusa destino em rede privada, contra SSRF. Só libera quando o SIEM está na mesma rede.
    (pasta / "local_define.php").write_text(
        "<?php\n" + ("define('GLPI_SERVERSIDE_URL_ALLOWED_PRIVATE_NETWORKS_CONTEXTS', [Webhook::class]);\n" if webhook_interno else ""))

    url = _subir(pasta, projeto, porta)
    for opcao in ("enable_hlapi", "enable_api", "enable_api_login_credentials"):
        _console(pasta, projeto, "config:set", opcao, "1")
    _console(pasta, projeto, "user:reset_password", "glpi", "-p", senha_admin)
    for conta in _CONTAS_PADRAO:
        _console(pasta, projeto, "user:disable", conta)
    _console(pasta, projeto, "user:create", "fachada", "-p", senha_fachada)
    _console(pasta, projeto, "user:grant", "fachada", "-p", PERFIL_FACHADA, "-e", "0", "-r")

    _rodar_php(pasta, projeto, "definir_contato.php", {"usuario": "fachada", "nome": "Atendimento"})

    php = (RAIZ / "infra/glpi/criar_cliente_oauth.php").read_text()
    saida = _compose(pasta, projeto, "exec", "-T", "glpi", "php", input=php).stdout
    oauth = json.loads(saida.strip().splitlines()[-1])

    # Os SLAs nascem com a conta de administração, que depois não é mais usada pela API.
    admin = Motor(url, oauth["id"], oauth["segredo"], "glpi", senha_admin)
    slm = admin.pedir("POST", "/Setup/SLM", json={"name": "Atendimento", "entity": 0, "is_recursive": True,
                                                  "use_ticket_calendar": False})["id"]
    categorias = {chave: admin.pedir("POST", "/Dropdowns/ITILCategory", json={"name": rotulo, "entity": 0, "is_recursive": True})["id"]
                  for chave, rotulo in CATEGORIAS.items()}
    for extensao, tipo_mime in _EXTENSOES_EXTRAS.items():
        admin.pedir("POST", "/Dropdowns/DocumentType", json={"name": extensao, "extension": extensao, "mime": tipo_mime, "is_uploadable": True})
    grupos = {nivel: admin.pedir("POST", "/Administration/Group", json={
        "name": nivel, "entity": 0, "is_recursive": True, "maybe_assigned": True, "maybe_notified": True})["id"] for nivel in NIVEIS[1:]}
    slas = {}
    for criticidade, horas in prazos.items():
        slas[criticidade] = admin.pedir("POST", "/Setup/SLA", json={
            "name": f"Resolução {criticidade}", "slm": slm, "entity": 0, "is_recursive": True,
            "type": 0, "time": horas, "time_unit": "hour", "use_ticket_calendar": False})["id"]
        # A regra vale para qualquer canal, inclusive e-mail, e o perfil da fachada não precisa do direito de SLA.
        criar_niveis_de_sla(pasta, projeto, slas[criticidade], grupos, horas * 3600)
        resposta = admin.pedir("POST", "/Setup/SLA", json={
            "name": f"Resposta {criticidade}", "slm": slm, "entity": 0, "is_recursive": True,
            "type": 1, "time": prazos_resposta[criticidade], "time_unit": "minute", "use_ticket_calendar": False})["id"]
        regra = admin.pedir("POST", "/Rule/Collection/Ticket/Rule", json={
            "name": f"SLA {criticidade}", "entity": 0, "is_recursive": True, "is_active": True,
            "match": "AND", "condition": _REGRA_AO_CRIAR_OU_ALTERAR})["id"]
        admin.pedir("POST", f"/Rule/Collection/Ticket/Rule/{regra}/Criteria", json={
            "criteria": "priority", "condition": 0, "pattern": str(CRITICIDADES[criticidade])})
        admin.pedir("POST", f"/Rule/Collection/Ticket/Rule/{regra}/Action", json={
            "action_type": "assign", "field": "slas_id_ttr", "value": str(slas[criticidade])})
        admin.pedir("POST", f"/Rule/Collection/Ticket/Rule/{regra}/Action", json={
            "action_type": "assign", "field": "slas_id_tto", "value": str(resposta)})

    if correio:
        _configurar_correio(pasta, projeto, admin, correio, sigla)

    webhook_segredo = None
    if webhook_url:
        webhook_segredo = secrets.token_urlsafe(32)
        admin.pedir("POST", "/Setup/Webhook", json={
            "name": "Status para o SIEM", "entity": 0, "is_recursive": True, "itemtype": "Ticket", "event": "update",
            "use_default_payload": False, "payload": _CARGA_WEBHOOK, "url": webhook_url, "secret": webhook_segredo,
            "http_method": "POST", "sent_try": _TENTATIVAS_WEBHOOK, "is_active": True})

    credenciais = {
        papel: {"chave": f"trk_{secrets.token_hex(12)}", "segredo": secrets.token_urlsafe(32)}
        for papel in ("siem", "atendente")
    }
    with psycopg.connect(os.environ["TRACK_BANCO_DONO_DSN"]) as conn:
        cliente_id = conn.execute(
            """INSERT INTO clientes (nome, identificador, sigla, categorias, grupos, prazos_resposta, motor_url, motor_oauth_id,
                                     motor_oauth_segredo, motor_usuario, motor_senha, slas)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'fachada', %s, %s) RETURNING id""",
            (nome, identificador, sigla, json.dumps(categorias), json.dumps(grupos), json.dumps(prazos_resposta), url, oauth["id"],
             cifrar(oauth["segredo"]), cifrar(senha_fachada), json.dumps(slas))).fetchone()[0]
        for papel, c in credenciais.items():
            conn.execute("INSERT INTO credenciais (chave, cliente_id, segredo, papel) VALUES (%s, %s, %s, %s)",
                         (c["chave"], cliente_id, cifrar(c["segredo"]), papel))

    # A senha de administração fica só no disco do servidor, para manutenção.
    with env.open("a") as f:
        f.write(f"SENHA_ADMIN={senha_admin}\n")
    return {"cliente_id": str(cliente_id), "identificador": identificador, "sigla": sigla, "credenciais": credenciais,
            "webhook_segredo": webhook_segredo}


def remover(identificador):
    pasta = CLIENTES / identificador
    _compose(pasta, f"track-{identificador}", "down", "-v")
    shutil.rmtree(pasta)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("criar")
    c.add_argument("--nome", required=True)
    c.add_argument("--prazos", required=True)
    c.add_argument("--prazos-resposta", help="minutos até a primeira resposta, por criticidade")
    c.add_argument("--sigla", help="de 2 a 8 letras maiúsculas ou números, usada no código dos chamados")
    c.add_argument("--webhook-url")
    c.add_argument("--webhook-interno", action="store_true", help="libera destino em rede privada, quando o SIEM está na mesma rede")
    c.add_argument("--correio", help="arquivo JSON local com SMTP, IMAP, remetente e e-mail da equipe")
    c.add_argument("--saida", required=True, help="arquivo local que recebe as credenciais, com permissão 600")
    a = p.parse_args()
    r = criar(a.nome, json.loads(a.prazos), a.webhook_url,
              json.loads(a.prazos_resposta) if a.prazos_resposta else None, a.sigla, a.webhook_interno,
              json.loads(Path(a.correio).read_text()) if a.correio else None)
    saida = Path(a.saida)
    saida.touch(mode=0o600)
    saida.write_text(json.dumps(r, indent=2))
    print(f"cliente {r['cliente_id']} criado; credenciais em {saida}")


if __name__ == "__main__":
    main()
