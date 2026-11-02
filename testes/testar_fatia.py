"""Primeira fatia de ponta a ponta: abrir pelo SIEM, acompanhar, encerrar, com SLA contado.

Sobe duas instâncias de cliente de verdade e as remove no fim.
sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_fatia.py'
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import psycopg

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import provisao  # noqa: E402
from track_api.dominio import calcular_sla  # noqa: E402
from track_api.sessao import etapa  # noqa: E402

API = "http://127.0.0.1:8710"
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}
falhas = []


def confere(nome, condicao, detalhe=""):
    print(("ok    " if condicao else "FALHA ") + nome + (f" | {detalhe}" if not condicao else ""), flush=True)
    if not condicao:
        falhas.append(nome)


def pedir(cred, metodo, caminho, corpo=None, carimbo=None, segredo=None):
    conteudo = json.dumps(corpo).encode() if corpo is not None else b""
    carimbo = str(carimbo or int(time.time()))
    msg = f"{carimbo}.{metodo}.{caminho}.{hashlib.sha256(conteudo).hexdigest()}"
    assinatura = hmac.new((segredo or cred["segredo"]).encode(), msg.encode(), hashlib.sha256).hexdigest()
    return httpx.request(metodo, API + caminho, content=conteudo, timeout=60, headers={
        "Content-Type": "application/json", "X-Track-Chave": cred["chave"],
        "X-Track-Data": carimbo, "X-Track-Assinatura": assinatura})


class Receptor(BaseHTTPRequestHandler):
    recebidos = []
    recusar = 1

    def do_POST(self):
        corpo = self.rfile.read(int(self.headers["Content-Length"]))
        if Receptor.recusar > 0:
            Receptor.recusar -= 1
            self.send_response(500)
            self.end_headers()
            return
        Receptor.recebidos.append((self.headers["X-GLPI-timestamp"], self.headers["X-GLPI-signature"], corpo))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_a):
        pass


def data(texto):
    return datetime.fromisoformat(texto)


def conferir_contas_de_sla():
    """Sem esperar horas de verdade: o cálculo com datas fixas, em cada faixa do prazo."""
    def sla(minutos_passados, status=2, **extras):
        inicio = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        chamado = {"status": {"id": status}, "date": inicio.isoformat(), "resolution_date": (inicio + timedelta(hours=4)).isoformat(), **extras}
        return calcular_sla(chamado, inicio + timedelta(minutes=minutos_passados), 15)

    confere("SLA com folga não está em risco", sla(60)["em_risco"] is False and sla(60)["percentual"] == 25.0, sla(60))
    confere("SLA com um quarto restante está em risco", sla(180)["em_risco"] is True and sla(180)["estourado"] is False, sla(180))
    confere("SLA vencido está estourado e não em risco", sla(241)["estourado"] is True and sla(241)["em_risco"] is False, sla(241))
    confere("resposta sem atendente estoura no prazo", sla(16)["resposta"]["estourada"] is True and sla(14)["resposta"]["estourada"] is False, sla(16))
    p = sla(100, status=4, begin_waiting_date=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).isoformat())
    confere("pausa em curso desconta dos corridos", p["segundos_corridos"] == 3600 and p["segundos_pausados"] == 2400 and p["pausado"] is True, p)
    confere("pausa em curso empurra o prazo", p["segundos_restantes"] == 4 * 3600 - 3600, p)


def conferir_chave_de_testes_do_mfa():
    sessao = {"trocar_senha": False, "mfa_ativo": False, "mfa_ok": False}
    guardado = {k: os.environ.pop(k, None) for k in ("TRACK_SEM_MFA", "TRACK_COOKIE_SEGURO")}
    try:
        confere("sem a chave, o segundo fator é obrigatório", etapa(sessao) == "cadastrar_mfa")
        os.environ["TRACK_SEM_MFA"] = "1"
        confere("com cookie seguro, a chave é ignorada", etapa(sessao) == "cadastrar_mfa")
        os.environ["TRACK_COOKIE_SEGURO"] = "1"
        confere("com cookie seguro explícito, a chave é ignorada", etapa(sessao) == "cadastrar_mfa")
        os.environ["TRACK_COOKIE_SEGURO"] = "0"
        confere("em ambiente local, a chave dispensa o segundo fator", etapa(sessao) == "pronto")
        confere("a chave não dispensa a troca da senha provisória", etapa({**sessao, "trocar_senha": True}) == "trocar_senha")
    finally:
        os.environ.pop("TRACK_SEM_MFA", None)
        os.environ.pop("TRACK_COOKIE_SEGURO", None)
        os.environ.update({k: v for k, v in guardado.items() if v is not None})


def principal():
    conferir_contas_de_sla()
    conferir_chave_de_testes_do_mfa()
    receptor = HTTPServer(("0.0.0.0", 0), Receptor)
    threading.Thread(target=receptor.serve_forever, daemon=True).start()
    # O motor roda em contêiner: precisa do endereço da máquina, não do local.
    ip_da_maquina = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
    url_receptor = f"http://{ip_da_maquina}:{receptor.server_port}/webhook"

    sufixo = uuid.uuid4().hex[:6]
    print("provisionando duas instâncias...", flush=True)
    a = provisao.criar(f"cliente-a-{sufixo}", PRAZOS, url_receptor, webhook_interno=True)
    b = None
    srv = None
    try:
        b = provisao.criar(f"cliente-b-{sufixo}", PRAZOS)
        srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", "8710", "--log-level", "warning"],
                               cwd=RAIZ / "fachada")
        for _ in range(40):
            try:
                httpx.get(API + "/api/v1/chamados/1", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)

        siem, atend = a["credenciais"]["siem"], a["credenciais"]["atendente"]
        siem_b = b["credenciais"]["siem"]
        novo = {"id_externo": f"incidente-{sufixo}", "titulo": "Força bruta em servidor", "descricao": "Alerta <b>123</b> virou incidente",
                "criticidade": "critica", "solicitante": "SIEM", "categoria": "acesso",
                "contexto": {"ativo": "SRV-01", "ip_origem": "203.0.113.45", "usuario": "root", "regra": "Força bruta", "tecnica_mitre": "T1110.001",
                             "url_alerta": "https://roar.exemplo.org/alertas/1"}}

        # Autenticação
        c = "/api/v1/siem/chamados"
        confere("assinatura errada recusada", pedir(siem, "POST", c, novo, segredo="errado").status_code == 401)
        confere("assinatura fora da janela recusada", pedir(siem, "POST", c, novo, carimbo=int(time.time()) - 900).status_code == 401)
        confere("criticidade inválida recusada", pedir(siem, "POST", c, {**novo, "criticidade": "urgente"}).status_code == 422)
        confere("atendente não abre pela rota do SIEM", pedir(atend, "POST", c, novo).status_code == 403)
        confere("id externo com caractere de filtro recusado", pedir(siem, "POST", c, {**novo, "id_externo": "a;b==c"}).status_code == 422)

        # Abertura e idempotência
        r = pedir(siem, "POST", c, novo)
        confere("abre pelo SIEM", r.status_code == 201, r.text)
        ch = r.json()
        n = ch["numero"]
        confere("nasce aberto", ch["status"] == "aberto")
        confere("registra solicitante e criticidade", ch["solicitante"] == "SIEM" and ch["criticidade"] == "critica")
        confere("código traz a sigla do cliente", ch["codigo"] == f"{a['sigla']}-{n:04d}", ch["codigo"])
        confere("guarda tipo, categoria e contexto", ch["tipo"] == "incidente" and ch["categoria"] == "Acesso"
                and ch["contexto"]["ip_origem"] == "203.0.113.45" and ch["contexto"]["tecnica_mitre"] == "T1110.001", ch)
        confere("primeira resposta tem prazo próprio", data(ch["sla"]["resposta"]["prazo"]) - data(ch["aberto_em"]) == timedelta(minutes=15), ch["sla"])
        confere("primeira resposta ainda pendente", ch["sla"]["resposta"]["em"] is None and ch["sla"]["resposta"]["cumprida"] is None, ch["sla"])
        confere("não leva dado de contato ao SIEM", "contato" not in ch, ch)
        for ruim, nome in (({"url_alerta": "javascript:alert(1)"}, "link que não é web"), ({"ip_origem": "nao-e-ip"}, "IP inválido"),
                           ({"tecnica_mitre": "x"}, "técnica inválida"), ({"cpf": "123"}, "campo desconhecido no contexto")):
            confere(f"{nome} recusado", pedir(siem, "POST", c, {**novo, "id_externo": f"ruim-{sufixo}", "contexto": ruim}).status_code == 422)
        confere("prazo é abertura + 4 horas corridas", data(ch["sla"]["prazo"]) - data(ch["aberto_em"]) == timedelta(hours=4), ch["sla"])
        confere("descrição é escapada", "<b>" not in ch["descricao"], ch["descricao"])
        r2 = pedir(siem, "POST", c, novo)
        confere("reenvio não duplica", r2.status_code == 200 and r2.json()["numero"] == n, r2.text)

        # Regras de encerramento e papéis
        p = f"/api/v1/chamados/{n}"
        confere("SIEM não opera o chamado", pedir(siem, "POST", p + "/atender", {"atendente": "x"}).status_code == 403)
        confere("não encerra sem resolução", pedir(atend, "POST", p + "/encerrar").status_code == 409)
        confere("não resolve sem atender", pedir(atend, "POST", p + "/resolver", {"texto": "x"}).status_code == 409)

        # Acompanhamento e pausa
        r = pedir(atend, "POST", p + "/atender", {"atendente": "Ana Analista"})
        confere("atende", r.status_code == 200 and r.json()["status"] == "em_andamento", r.text)
        confere("registra atendente", r.json().get("atendente") == "Ana Analista")
        confere("primeira resposta cumprida ao atender", r.json()["sla"]["resposta"]["cumprida"] is True, r.json()["sla"])
        confere("não assume duas vezes", pedir(atend, "POST", p + "/atender", {"atendente": "Outra"}).status_code == 409)
        r = pedir(atend, "POST", p + "/andamento", {"texto": "Bloqueado o IP de origem"})
        confere("registra andamento", r.status_code == 200, r.text)
        prazo_antes = data(r.json()["sla"]["prazo"])

        r = pedir(atend, "POST", p + "/pausar", {"texto": "Aguardando confirmação do cliente"})
        confere("pausa", r.status_code == 200 and r.json()["status"] == "aguardando_cliente", r.text)
        confere("SLA aparece pausado", r.json()["sla"]["pausado"] is True)
        corridos_1 = r.json()["sla"]["segundos_corridos"]
        time.sleep(6)
        r = pedir(siem, "GET", p)
        sla = r.json()["sla"]
        confere("corridos param durante a pausa", abs(sla["segundos_corridos"] - corridos_1) <= 1, sla)
        confere("pausa em curso é contada", sla["segundos_pausados"] >= 5, sla)
        confere("prazo anda durante a pausa", data(sla["prazo"]) - prazo_antes >= timedelta(seconds=5), sla)
        confere("resolver em pausa é recusado", pedir(atend, "POST", p + "/resolver", {"texto": "x"}).status_code == 409)

        r = pedir(atend, "POST", p + "/retomar")
        confere("retoma", r.status_code == 200 and r.json()["status"] == "em_andamento", r.text)
        sla = r.json()["sla"]
        confere("motor adiou o prazo pela pausa", data(sla["prazo"]) - prazo_antes >= timedelta(seconds=5), sla)
        confere("pausa ficou acumulada", sla["segundos_pausados"] >= 5 and sla["pausado"] is False, sla)

        # Encerramento
        confere("solução vazia recusada", pedir(atend, "POST", p + "/resolver", {"texto": ""}).status_code == 422)
        r = pedir(atend, "POST", p + "/resolver", {"texto": "IP bloqueado e senha trocada"})
        confere("resolve", r.status_code == 200 and r.json()["status"] == "resolvido", r.text)
        confere("registra data de resolução", r.json().get("resolvido_em") is not None)
        r = pedir(atend, "POST", p + "/encerrar")
        final = r.json()
        confere("encerra", r.status_code == 200 and final["status"] == "encerrado", r.text)
        confere("registra data e hora de fechamento", data(final["encerrado_em"]) >= data(final["resolvido_em"]), final)
        confere("não encerra duas vezes", pedir(atend, "POST", p + "/encerrar").status_code == 409)
        decorrido = (data(final["resolvido_em"]) - data(final["aberto_em"])).total_seconds()
        confere("SLA final desconta a pausa",
                final["sla"]["segundos_corridos"] == decorrido - final["sla"]["segundos_pausados"], final["sla"])
        confere("SLA cumprido", final["sla"]["estourado"] is False, final["sla"])

        # Histórico
        r = pedir(atend, "GET", p + "/historico")
        h = r.json()
        tipos = [e["tipo"] for e in h["eventos"]]
        esperado = ["aberto", "em_andamento", "andamento", "aguardando_cliente", "em_andamento", "resolvido", "encerrado"]
        confere("histórico tem cada mudança, em ordem", tipos == esperado, tipos)
        confere("histórico tem o autor", h["eventos"][1]["autor"] == "Ana Analista", h["eventos"][1])
        conteudos = " ".join(i["conteudo"] or "" for i in h["interacoes"])
        confere("histórico tem andamento e solução", "Bloqueado o IP" in conteudos and "senha trocada" in conteudos, conteudos)
        confere("resposta não revela a ferramenta", "glpi" not in (r.text + json.dumps(final)).lower())

        # Status de volta ao SIEM, pelo webhook nativo: a fila do motor roda a cada minuto.
        limite = time.time() + 300
        corpos = []
        while time.time() < limite:
            corpos = [json.loads(x[2]) for x in Receptor.recebidos]
            if any(x["status"] == 6 for x in corpos):
                break
            time.sleep(3)
        confere("webhook nativo leva o encerramento, mesmo com falha na primeira tentativa", any(x["status"] == 6 for x in corpos), corpos)
        confere("webhook leva o status do meio do caminho", any(x["status"] == 4 for x in corpos), corpos)
        confere("webhook identifica o chamado", all(x["numero"] == n and x["id_externo"] == novo["id_externo"] for x in corpos), corpos)
        carimbo, assinatura, bruto = Receptor.recebidos[0]
        calc = hmac.new(a["webhook_segredo"].encode(), bruto + carimbo.encode(), hashlib.sha256).hexdigest()
        confere("webhook assinado", hmac.compare_digest(calc, assinatura), assinatura)
        confere("webhook não leva o conteúdo do chamado", b"Bloqueado" not in bruto and b"titulo" not in bruto, bruto)

        # As cinco criticidades
        for crit, horas in PRAZOS.items():
            r = pedir(siem, "POST", c, {**novo, "id_externo": f"nivel-{crit}-{sufixo}", "criticidade": crit})
            x = r.json()
            confere(f"{crit}: prazo de {horas} horas", r.status_code == 201 and data(x["sla"]["prazo"]) - data(x["aberto_em"]) == timedelta(hours=horas), r.text)

        # Isolamento
        confere("outro cliente não acha pelo id externo", pedir(siem_b, "GET", f"{c}/{novo['id_externo']}").status_code == 404)
        confere("outro cliente não acha pelo número", pedir(siem_b, "GET", p).status_code == 404)
        confere("outro cliente não lê o histórico", pedir(siem_b, "GET", p + "/historico").status_code == 404)
        r = pedir(siem_b, "POST", c, {**novo})
        confere("mesmo id externo em outro cliente é outro chamado", r.status_code == 201, r.text)
        with psycopg.connect(os.environ["TRACK_BANCO_DSN"]) as conn:
            confere("banco sem cliente na transação não mostra nada", conn.execute("SELECT count(*) FROM chamados").fetchone()[0] == 0)
            conn.execute("SELECT set_config('track.cliente', %s, true)", (a["cliente_id"],))
            confere("banco com cliente mostra só os dele",
                    {str(x[0]) for x in conn.execute("SELECT DISTINCT cliente_id FROM chamados").fetchall()} == {a["cliente_id"]})
            try:
                conn.execute("DELETE FROM eventos")
                confere("API não apaga o histórico", False)
            except psycopg.errors.InsufficientPrivilege:
                confere("API não apaga o histórico", True)
    finally:
        if srv:
            srv.terminate()
            srv.wait()
        print("removendo instâncias...", flush=True)
        provisao.remover(a["identificador"])
        if b:
            provisao.remover(b["identificador"])
        receptor.shutdown()

    print(f"\n{len(falhas)} falha(s)" if falhas else "\ntudo certo")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    principal()
