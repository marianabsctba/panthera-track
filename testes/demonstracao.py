"""Sobe dois clientes de exemplo com chamados e pessoas, e a tela em endereço local.

As senhas provisórias ficam só em /opt/panthera-track/demonstracao.json, com permissão 600.
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/demonstracao.py'
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import pessoas, provisao  # noqa: E402

PORTA = 8720
API = f"http://127.0.0.1:{PORTA}"
ARQUIVO = Path("/opt/panthera-track/demonstracao.json")
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}


def pedir(cred, metodo, caminho, corpo=None):
    conteudo = json.dumps(corpo).encode() if corpo is not None else b""
    carimbo = str(int(time.time()))
    msg = f"{carimbo}.{metodo}.{caminho}.{hashlib.sha256(conteudo).hexdigest()}"
    r = httpx.request(metodo, API + caminho, content=conteudo, timeout=60, headers={
        "Content-Type": "application/json", "X-Track-Chave": cred["chave"], "X-Track-Data": carimbo,
        "X-Track-Assinatura": hmac.new(cred["segredo"].encode(), msg.encode(), hashlib.sha256).hexdigest()})
    r.raise_for_status()
    return r.json()


def semear(alfa, beta):
    def abre(cliente, sufixo, titulo, descricao, criticidade):
        return pedir(cliente["credenciais"]["siem"], "POST", "/api/v1/siem/chamados", {
            "id_externo": f"exemplo-{sufixo}", "titulo": titulo, "descricao": descricao, "criticidade": criticidade, "solicitante": "SIEM"})["numero"]

    def faz(cliente, numero, acao, **corpo):
        pedir(cliente["credenciais"]["atendente"], "POST", f"/api/v1/chamados/{numero}/{acao}", corpo or None)

    n = abre(alfa, "consulta", "Dúvida sobre retenção de logs", "Cliente pergunta por quanto tempo os logs ficam guardados.", "consulta")
    faz(alfa, n, "atender", atendente="Bia Santos")
    faz(alfa, n, "resolver", texto="Explicada a política de retenção ao cliente.")
    faz(alfa, n, "encerrar")

    n = abre(alfa, "baixa", "Agente fora do ar em estação", "Estação parou de enviar eventos há duas horas.", "baixa")
    faz(alfa, n, "atender", atendente="Bia Santos")
    faz(alfa, n, "resolver", texto="Serviço do agente reiniciado e coleta normalizada.")

    n = abre(alfa, "media", "Conta de serviço com senha antiga", "Política de troca de senha vencida.", "media")
    faz(alfa, n, "atender", atendente="Caio Lima")
    faz(alfa, n, "pausar", texto="Cliente precisa indicar a janela de troca.")

    n = abre(alfa, "alta", "Varredura de portas vinda da rede interna", "Correlação apontou varredura a partir de uma estação.", "alta")
    faz(alfa, n, "atender", atendente="Caio Lima")
    faz(alfa, n, "andamento", texto="Estação isolada, análise em curso.")

    abre(alfa, "critica", "Força bruta seguida de login em servidor", "Muitas falhas de login e um sucesso na mesma origem.", "critica")
    abre(beta, "critica", "Malware detectado em servidor de arquivos", "Antivírus e correlação apontam o mesmo arquivo.", "critica")
    abre(beta, "media", "Pico de alertas em firewall", "Volume acima do normal na última hora.", "media")


def principal():
    novo = not ARQUIVO.exists()
    if novo:
        alfa = provisao.criar("Cliente Alfa", PRAZOS)
        beta = provisao.criar("Cliente Beta", PRAZOS)
        contas = {
            "analista": pessoas.criar("Ana Rocha", "ana@exemplo.org", "analista", ["Cliente Alfa", "Cliente Beta"]),
            "gestor": pessoas.criar("Gil Gestor", "gil@exemplo.org", "cliente", ["Cliente Alfa"], "gestor"),
            "solicitante": pessoas.criar("Sol Souza", "sol@exemplo.org", "cliente", ["Cliente Alfa"], "solicitante"),
        }
        ARQUIVO.touch(mode=0o600)
        ARQUIVO.write_text(json.dumps({"tela": API, "contas": contas, "alfa": alfa, "beta": beta}, indent=2))
    dados = json.loads(ARQUIVO.read_text())

    env = {**os.environ, "TRACK_COOKIE_SEGURO": "0"}
    fachada = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", str(PORTA), "--log-level", "warning"],
                               cwd=RAIZ / "fachada", env=env)
    try:
        for _ in range(40):
            try:
                httpx.get(API + "/", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        if novo:
            semear(dados["alfa"], dados["beta"])
        print(f"tela em {API}", flush=True)
        fachada.wait()
    finally:
        fachada.terminate()


if __name__ == "__main__":
    principal()
