"""Aceite do cliente: mudar criticidade e encerrar sem resolução, com instância de verdade.

sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_aceite.py'
"""
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pyotp

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import pessoas, provisao  # noqa: E402

API = "http://127.0.0.1:8713"
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}
SENHA = "senha-de-teste-" + uuid.uuid4().hex
falhas = []


def confere(nome, condicao, detalhe=""):
    print(("ok    " if condicao else "FALHA ") + nome + (f" | {str(detalhe)[:400]}" if not condicao else ""), flush=True)
    if not condicao:
        falhas.append(nome)


def entrar(dados):
    c = httpx.Client(base_url=API, headers={"X-Track-Tela": "1"}, timeout=120)
    c.post("/api/v1/sessao", json={"email": dados["email"], "senha": dados["senha_provisoria"]}).raise_for_status()
    c.post("/api/v1/sessao/senha", json={"senha_atual": dados["senha_provisoria"], "senha_nova": SENHA}).raise_for_status()
    segredo = c.get("/api/v1/sessao/mfa").json()["segredo"]
    c.post("/api/v1/sessao/mfa/ativar", json={"codigo": pyotp.TOTP(segredo).now()}).raise_for_status()
    return c


def principal():
    sufixo = uuid.uuid4().hex[:6]
    nome_a, nome_b = f"aceite-a-{sufixo}", f"aceite-b-{sufixo}"
    print("provisionando...", flush=True)
    a = provisao.criar(nome_a, PRAZOS)
    b = srv = None
    try:
        b = provisao.criar(nome_b, PRAZOS)
        e = lambda n: f"{n}.{sufixo}@exemplo.org"  # noqa: E731
        ana = pessoas.criar("Ana Rocha", e("ana"), "analista", [nome_a, nome_b])
        gil = pessoas.criar("Gil Gestor", e("gil"), "cliente", [nome_a], "gestor")
        sol = pessoas.criar("Sol Souza", e("sol"), "cliente", [nome_a], "solicitante")
        outro = pessoas.criar("Otto Gestor", e("otto"), "cliente", [nome_b], "gestor")
        srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", "8713", "--log-level", "warning"],
                               cwd=RAIZ / "fachada", env={**os.environ, "TRACK_COOKIE_SEGURO": "0"})
        for _ in range(40):
            try:
                httpx.get(API + "/", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        ca, cg, cs, co = entrar(ana), entrar(gil), entrar(sol), entrar(outro)

        # Mudança de criticidade
        r = cs.post("/api/v1/tela/chamados", json={"titulo": "Sistema fora do ar", "descricao": "x", "criticidade": "media"})
        ch = r.json()
        cid, n = ch["cliente_id"], ch["numero"]
        p = f"/api/v1/tela/clientes/{cid}/chamados/{n}"
        pedido = {"tipo": "criticidade", "criticidade": "critica", "motivo": "Afeta a produção inteira"}
        confere("pessoa do cliente não pede", cg.post(p + "/aceites", json=pedido).status_code == 403)
        confere("motivo é obrigatório", ca.post(p + "/aceites", json={**pedido, "motivo": ""}).status_code == 422)
        confere("criticidade inválida recusada", ca.post(p + "/aceites", json={**pedido, "criticidade": "urgente"}).status_code == 422)
        confere("pedido de criticidade precisa dizer qual", ca.post(p + "/aceites", json={"tipo": "criticidade", "motivo": "x"}).status_code == 422)
        confere("encerramento não informa criticidade", ca.post(p + "/aceites", json={**pedido, "tipo": "encerramento"}).status_code == 422)
        confere("mesma criticidade recusada", ca.post(p + "/aceites", json={**pedido, "criticidade": "media"}).status_code == 409)
        r = ca.post(p + "/aceites", json=pedido)
        confere("analista pede a mudança", r.status_code == 201, r.text)
        v = ca.get(p).json()
        confere("nada muda antes do aceite", v["criticidade"] == "media" and v["sla"]["prazo"] == ch["sla"]["prazo"], v["sla"])
        confere("não abre segundo pedido", ca.post(p + "/aceites", json={**pedido, "criticidade": "alta"}).status_code == 409)
        pendente = next(x for x in v["aceites"] if x["estado"] == "pendente")
        confere("pedido traz quem pediu e o motivo", pendente["pedido_por"] == "Ana Rocha" and pendente["motivo"] == "Afeta a produção inteira", pendente)
        confere("fila marca o aguardo", next(x for x in ca.get("/api/v1/tela/chamados").json()["chamados"] if x["numero"] == n and x["cliente_id"] == cid)["aceite_pendente"] is True)
        d = f"{p}/aceites/{pendente['id']}/decidir"
        confere("analista não aceita o próprio pedido", ca.post(d, json={"aceito": True}).status_code == 403)
        confere("solicitante não decide", cs.post(d, json={"aceito": True}).status_code == 403)
        confere("gestor de outro cliente não alcança", co.post(d, json={"aceito": True}).status_code == 404)
        confere("nada mudou com as tentativas", ca.get(p).json()["criticidade"] == "media")
        r = cg.post(d, json={"aceito": True, "comentario": "De acordo"})
        confere("gestor aceita", r.status_code == 200, r.text)
        v = ca.get(p).json()
        confere("criticidade muda com o aceite", v["criticidade"] == "critica", v["criticidade"])
        confere("prazo recalcula pela nova criticidade", datetime.fromisoformat(v["sla"]["prazo"]) - datetime.fromisoformat(v["aberto_em"]) == timedelta(hours=4), v["sla"])
        confere("prazo de resposta também recalcula", datetime.fromisoformat(v["sla"]["resposta"]["prazo"]) - datetime.fromisoformat(v["aberto_em"]) == timedelta(minutes=15), v["sla"])
        confere("decisão fica registrada", v["aceites"][0]["estado"] == "aceito" and v["aceites"][0]["decidido_por"] == "Gil Gestor" and v["aceites"][0]["comentario"] == "De acordo", v["aceites"])
        confere("não decide duas vezes", cg.post(d, json={"aceito": False}).status_code == 409)
        h = ca.get(p + "/historico").json()
        tipos = [x["tipo"] for x in h["eventos"]]
        confere("histórico tem pedido e decisão", "aceite_pedido" in tipos and "aceite_decidido" in tipos, tipos)
        confere("autores certos no histórico", any(x["tipo"] == "aceite_decidido" and x["autor"] == "Gil Gestor" for x in h["eventos"]), h["eventos"])
        confere("registro no motor conta a decisão", any("Aceito por Gil Gestor" in (i["conteudo"] or "") for i in h["interacoes"]), h["interacoes"])

        # Recusa
        ca.post(p + "/aceites", json={"tipo": "criticidade", "criticidade": "baixa", "motivo": "Teste de recusa"}).raise_for_status()
        pendente = next(x for x in ca.get(p).json()["aceites"] if x["estado"] == "pendente")
        cg.post(f"{p}/aceites/{pendente['id']}/decidir", json={"aceito": False, "comentario": "Continua crítico"}).raise_for_status()
        v = ca.get(p).json()
        confere("recusa mantém a criticidade", v["criticidade"] == "critica" and v["aceites"][0]["estado"] == "recusado", v["aceites"][0])

        # Encerramento sem resolução
        confere("sem aceite segue sem encerrar", ca.post(p + "/encerrar").status_code == 409)
        ca.post(p + "/assumir").raise_for_status()
        r = ca.post(p + "/aceites", json={"tipo": "encerramento", "motivo": "Cliente desistiu da mudança"})
        confere("analista pede encerramento", r.status_code == 201, r.text)
        confere("continua aberto antes do aceite", ca.get(p).json()["status"] == "em_andamento")
        pendente = next(x for x in ca.get(p).json()["aceites"] if x["estado"] == "pendente")
        r = cg.post(f"{p}/aceites/{pendente['id']}/decidir", json={"aceito": True})
        confere("gestor aceita o encerramento", r.status_code == 200 and r.json()["status"] == "encerrado", r.text)
        v = ca.get(p).json()
        confere("encerrado registra a data", v["encerrado_em"] is not None)
        confere("encerrado não aceita novo pedido", ca.post(p + "/aceites", json={"tipo": "encerramento", "motivo": "x"}).status_code == 409)

        # Encerrar com pedido em aberto não deixa o pedido pendurado
        r = cs.post("/api/v1/tela/chamados", json={"titulo": "Outro", "descricao": "x", "criticidade": "baixa"})
        p2 = f"/api/v1/tela/clientes/{cid}/chamados/{r.json()['numero']}"
        ca.post(p2 + "/assumir").raise_for_status()
        ca.post(p2 + "/aceites", json={"tipo": "criticidade", "criticidade": "alta", "motivo": "x"}).raise_for_status()
        ca.post(p2 + "/resolver", json={"texto": "Resolvido antes da decisão"}).raise_for_status()
        ca.post(p2 + "/encerrar").raise_for_status()
        v = ca.get(p2).json()
        confere("encerrar fecha o pedido em aberto", v["aceites"][0]["estado"] == "recusado" and v["criticidade"] == "baixa", v["aceites"])

        # Isolamento
        with psycopg.connect(os.environ["TRACK_BANCO_DSN"]) as conn:
            confere("banco sem cliente na transação não mostra aceites", conn.execute("SELECT count(*) FROM aceites").fetchone()[0] == 0)
            try:
                conn.execute("DELETE FROM aceites")
                confere("API não apaga aceites", False)
            except psycopg.errors.InsufficientPrivilege:
                confere("API não apaga aceites", True)
    finally:
        if srv:
            srv.terminate()
            srv.wait()
        print("removendo instâncias...", flush=True)
        provisao.remover(a["identificador"])
        if b:
            provisao.remover(b["identificador"])

    print(f"\n{len(falhas)} falha(s)" if falhas else "\ntudo certo")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    principal()
