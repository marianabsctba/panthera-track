"""E-mail de verdade: abertura, resposta, recusa de desconhecido e avisos, com uma caixa de testes própria.

sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_correio.py'
"""
import email
import email.utils
import imaplib
import os
import re
import smtplib
import subprocess
import sys
import time
import uuid
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path

import httpx
import pyotp

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import pessoas, provisao  # noqa: E402

API = "http://127.0.0.1:8712"
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}
SENHA = "senha-de-teste-" + uuid.uuid4().hex
CONTENTOR = "track-correio"
falhas = []


def confere(nome, condicao, detalhe=""):
    print(("ok    " if condicao else "FALHA ") + nome + (f" | {str(detalhe)[:500]}" if not condicao else ""), flush=True)
    if not condicao:
        falhas.append(nome)


def _texto(valor):
    return "".join(p.decode(c or "utf-8") if isinstance(p, bytes) else p for p, c in decode_header(valor or ""))


def caixa(usuario):
    m = imaplib.IMAP4("127.0.0.1", 3143)
    m.login(usuario, usuario)
    m.select("INBOX")
    _, ids = m.search(None, "ALL")
    saida = []
    for i in ids[0].split():
        _, dados = m.fetch(i, "(RFC822)")
        msg = email.message_from_bytes(dados[0][1])
        corpo = ""
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain":
                corpo = parte.get_payload(decode=True).decode(parte.get_content_charset() or "utf-8", "replace")
                break
        saida.append((_texto(msg["Subject"]), corpo))
    m.logout()
    return saida


def enviar(de, para, assunto, corpo):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = de, para, assunto
    msg["Date"], msg["Message-ID"] = email.utils.formatdate(), email.utils.make_msgid()
    msg.set_content(corpo)
    with smtplib.SMTP("127.0.0.1", 3025) as s:
        s.send_message(msg)


def esperar(condicao, segundos=240):
    limite = time.time() + segundos
    while time.time() < limite:
        valor = condicao()
        if valor:
            return valor
        time.sleep(4)
    return condicao()


def entrar(dados):
    c = httpx.Client(base_url=API, headers={"X-Track-Tela": "1"}, timeout=120)
    c.post("/api/v1/sessao", json={"email": dados["email"], "senha": dados["senha_provisoria"]}).raise_for_status()
    c.post("/api/v1/sessao/senha", json={"senha_atual": dados["senha_provisoria"], "senha_nova": SENHA}).raise_for_status()
    segredo = c.get("/api/v1/sessao/mfa").json()["segredo"]
    c.post("/api/v1/sessao/mfa/ativar", json={"codigo": pyotp.TOTP(segredo).now()}).raise_for_status()
    return c


def principal():
    subprocess.run(["docker", "rm", "-f", CONTENTOR], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTENTOR, "-p", "3025:3025", "-p", "3143:3143",
                    "-e", "GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled",
                    "greenmail/standalone:2.1.3"], check=True, capture_output=True)
    time.sleep(6)
    ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
    sufixo = uuid.uuid4().hex[:6]
    suporte, equipe = f"suporte.{sufixo}@exemplo.org", f"equipe.{sufixo}@exemplo.org"
    correio = {"smtp_host": ip, "smtp_port": 3025, "imap_caixa": "{" + ip + ":3143/imap/novalidate-cert}INBOX",
               "imap_usuario": suporte, "imap_senha": suporte, "remetente": suporte, "equipe": equipe,
               "tela": "https://chamados.exemplo.org"}
    nome = f"correio-{sufixo}"
    sigla = "C" + sufixo[:5].upper()
    print("provisionando...", flush=True)
    a = provisao.criar(nome, PRAZOS, sigla=sigla, correio=correio)
    srv = None
    try:
        e = lambda n: f"{n}.{sufixo}@exemplo.org"  # noqa: E731
        ana = pessoas.criar("Ana Rocha", e("ana"), "analista", [nome])
        sol = pessoas.criar("Sol Souza", e("sol"), "cliente", [nome], "solicitante")
        gil = pessoas.criar("Gil Gestor", e("gil"), "cliente", [nome], "gestor")
        srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", "8712", "--log-level", "warning"],
                               cwd=RAIZ / "fachada", env={**os.environ, "TRACK_COOKIE_SEGURO": "0"})
        for _ in range(40):
            try:
                httpx.get(API + "/", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        ca, cs, cg = entrar(ana), entrar(sol), entrar(gil)

        # Abertura por e-mail
        print("enviando e esperando a coleta...", flush=True)
        enviar(sol["email"], suporte, "Sem acesso ao sistema", "Não consigo entrar desde hoje cedo.")
        enviar(f"estranho.{sufixo}@fora.net", suporte, "Pedido de desconhecido", "Abram um chamado para mim.")

        def do_email():
            return next((x for x in ca.get("/api/v1/tela/chamados").json()["chamados"] if x["titulo"] == "Sem acesso ao sistema"), None)
        ch = esperar(do_email)
        confere("e-mail de quem tem conta vira chamado na fila", ch is not None)
        if not ch:
            raise RuntimeError("sem chamado do e-mail")
        confere("origem é e-mail e o solicitante é a pessoa", ch["origem"] == "email" and ch["solicitante"] == "Sol Souza", ch)
        confere("código segue o padrão do cliente", ch["codigo"] == f"{sigla}-{ch['numero']:04d}", ch["codigo"])
        confere("nasce aberto, com o prazo pela regra", ch["status"] == "aberto" and ch["sla"]["prazo"] and ch["criticidade"] == "media", ch)
        confere("tem prazo de primeira resposta", ch["sla"]["resposta"] is not None and ch["sla"]["resposta"]["em"] is None, ch["sla"])
        confere("a pessoa vê o chamado que abriu por e-mail", any(x["numero"] == ch["numero"] for x in cs.get("/api/v1/tela/chamados").json()["chamados"]))
        p = f"/api/v1/tela/clientes/{ch['cliente_id']}/chamados/{ch['numero']}"
        confere("descrição veio do e-mail", "Não consigo entrar" in ca.get(p).json()["descricao"], ca.get(p).json()["descricao"])
        confere("contato é o e-mail da conta", ca.get(p).json()["contato"]["email"] == sol["email"])
        h = ca.get(p + "/historico").json()
        confere("abertura fica no histórico com o autor", h["eventos"][0]["tipo"] == "aberto" and h["eventos"][0]["autor"] == "Sol Souza", h["eventos"])
        time.sleep(20)
        fila = ca.get("/api/v1/tela/chamados").json()["chamados"]
        confere("adoção não duplica o chamado", sum(x["numero"] == ch["numero"] for x in fila) == 1 and len(ca.get(p + "/historico").json()["eventos"]) == 1)
        confere("desconhecido não vira chamado", not any("desconhecido" in x["titulo"] for x in fila), [x["titulo"] for x in fila])

        # Movimentação gera aviso
        ca.post(p + "/assumir").raise_for_status()
        ca.post(p + "/andamento", json={"texto": "Estamos verificando o seu acesso"}).raise_for_status()
        print("esperando os avisos...", flush=True)
        recebidos = esperar(lambda: [x for x in caixa(sol["email"]) if "verificando" in x[1]], 200)
        confere("solicitante recebe aviso do andamento", bool(recebidos), [x[0] for x in caixa(sol["email"])])
        assunto, corpo = recebidos[-1] if recebidos else ("", "")
        confere("assunto traz a sigla do cliente", f"[{sigla} #" in assunto, assunto)
        confere("aviso está em português", "Situação" in corpo, corpo[:300])
        todos = caixa(sol["email"])
        tudo = " ".join(a_ + c for a_, c in todos).lower()
        confere("aviso não revela a ferramenta", "glpi" not in tudo, tudo[:600])
        confere("aviso não leva endereço interno", "localhost" not in tudo and "127.0.0.1" not in tudo and "index.php" not in tudo, tudo[:600])
        confere("aviso não mostra o usuário-sombra", not re.search(r"\bp[0-9a-f]{16}\b", tudo), tudo[:600])
        confere("aviso mostra o nome de quem pediu", "Solicitante: Sol Souza" in corpo, corpo[:400])
        confere("aviso leva o endereço da tela", "chamados.exemplo.org" in corpo, corpo[:400])
        confere("autor no aviso é o analista", "Ana Rocha" in corpo, corpo[:500])
        ca.post(p + "/pausar", json={"texto": "Precisamos do seu ramal"}).raise_for_status()
        confere("mudança de status gera aviso", bool(esperar(lambda: [x for x in caixa(sol["email"]) if "Pendente" in x[1]], 200)), [x[0] for x in caixa(sol["email"])])
        confere("equipe recebe os avisos", bool(caixa(equipe)), "caixa vazia")

        # Resposta por e-mail
        enviar(sol["email"], suporte, assunto.replace("Novo acompanhamento", "Re:"), "Meu ramal é 4321.")

        def resposta():
            return [i for i in ca.get(p + "/historico").json()["interacoes"] if "4321" in (i["conteudo"] or "")]
        r = esperar(resposta)
        confere("resposta por e-mail entra no mesmo chamado", bool(r), ca.get(p + "/historico").json()["interacoes"])
        confere("resposta traz o autor real", bool(r) and r[0]["autor"] == "Sol Souza", r)
        v = ca.get(p).json()
        confere("resposta por e-mail tira da pausa, para Em andamento", v["status"] == "em_andamento" and v["sla"]["pausado"] is False, v["status"])
        confere("pausa encerrada pelo cliente aparece como tal", v["pausas"][-1]["em_curso"] is False, v["pausas"])
        fila = ca.get("/api/v1/tela/chamados").json()["chamados"]
        confere("resposta não abre outro chamado", not any("4321" in (x["descricao"] or "") and x["numero"] != ch["numero"] for x in fila))

        # Abertura pela tela e pelo SIEM
        r = cs.post("/api/v1/tela/chamados", json={"titulo": "Aberto pela tela", "descricao": "x", "criticidade": "baixa"})
        confere("quem abre pela tela recebe o aviso de abertura", bool(esperar(lambda: [x for x in caixa(sol["email"]) if "Aberto pela tela" in x[0]], 200)), [x[0] for x in caixa(sol["email"])])
        confere("gestor é avisado do chamado de outra pessoa", bool(esperar(lambda: [x for x in caixa(gil["email"]) if "Aberto pela tela" in x[0]], 200)), [x[0] for x in caixa(gil["email"])])
    finally:
        if srv:
            srv.terminate()
            srv.wait()
        print("removendo instância e caixa...", flush=True)
        provisao.remover(a["identificador"])
        subprocess.run(["docker", "rm", "-f", CONTENTOR], capture_output=True)

    print(f"\n{len(falhas)} falha(s)" if falhas else "\ntudo certo")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    principal()
