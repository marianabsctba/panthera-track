"""Sobe um cliente de exemplo e a tela de console, só em endereço local e sem login.

Quem assina os pedidos é este processo, com as credenciais do arquivo local: não serve para produção.
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/demonstracao.py'
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import provisao  # noqa: E402

API = "http://127.0.0.1:8710"
ARQUIVO = Path("/opt/panthera-track/demonstracao.json")
TELA = RAIZ / "fachada/track_api/estatico/console.html"
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}


def pedir(cred, metodo, caminho, corpo=b""):
    carimbo = str(int(time.time()))
    msg = f"{carimbo}.{metodo}.{caminho}.{hashlib.sha256(corpo).hexdigest()}"
    return httpx.request(metodo, API + caminho, content=corpo, timeout=60, headers={
        "Content-Type": "application/json", "X-Track-Chave": cred["chave"], "X-Track-Data": carimbo,
        "X-Track-Assinatura": hmac.new(cred["segredo"].encode(), msg.encode(), hashlib.sha256).hexdigest()})


def semear(cred):
    def abre(sufixo, titulo, descricao, criticidade):
        corpo = {"id_externo": f"exemplo-{sufixo}", "titulo": titulo, "descricao": descricao,
                 "criticidade": criticidade, "solicitante": "SIEM"}
        return pedir(cred["siem"], "POST", "/api/v1/siem/chamados", json.dumps(corpo).encode()).json()["numero"]

    def faz(numero, acao, **corpo):
        r = pedir(cred["atendente"], "POST", f"/api/v1/chamados/{numero}/{acao}", json.dumps(corpo).encode() if corpo else b"")
        r.raise_for_status()

    n = abre("consulta", "Dúvida sobre retenção de logs", "Cliente pergunta por quanto tempo os logs ficam guardados.", "consulta")
    faz(n, "atender", atendente="Bia Santos")
    faz(n, "resolver", texto="Explicada a política de retenção ao cliente.")
    faz(n, "encerrar")

    n = abre("baixa", "Agente fora do ar em estação", "Estação parou de enviar eventos há duas horas.", "baixa")
    faz(n, "atender", atendente="Bia Santos")
    faz(n, "resolver", texto="Serviço do agente reiniciado e coleta normalizada.")

    n = abre("media", "Conta de serviço com senha antiga", "Política de troca de senha vencida.", "media")
    faz(n, "atender", atendente="Caio Lima")
    faz(n, "pausar", texto="Cliente precisa indicar a janela de troca.")

    n = abre("alta", "Varredura de portas vinda da rede interna", "Correlação apontou varredura a partir de uma estação.", "alta")
    faz(n, "atender", atendente="Ana Rocha")
    faz(n, "andamento", texto="Estação isolada, análise em curso.")

    abre("critica", "Força bruta seguida de login em servidor", "Muitas falhas de login e um sucesso na mesma origem.", "critica")


class Tela(BaseHTTPRequestHandler):
    credenciais = None

    def _repassar(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho)
        papel = "siem" if self.path.startswith("/api/v1/siem/") else "atendente"
        r = pedir(self.credenciais[papel], self.command, self.path, corpo)
        self.send_response(r.status_code)
        self.send_header("Content-Type", r.headers.get("content-type", "application/json"))
        self.end_headers()
        self.wfile.write(r.content)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._repassar()
        simbolo = self.path == "/estatico/pantera.webp"
        self.send_response(200)
        self.send_header("Content-Type", "image/webp" if simbolo else "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((TELA.with_name("pantera.webp") if simbolo else TELA).read_bytes())

    def do_POST(self):
        self._repassar()

    def log_message(self, *_a):
        pass


def principal():
    novo = not ARQUIVO.exists()
    if novo:
        dados = provisao.criar("demonstracao", PRAZOS)
        ARQUIVO.touch(mode=0o600)
        ARQUIVO.write_text(json.dumps(dados))
    dados = json.loads(ARQUIVO.read_text())
    Tela.credenciais = dados["credenciais"]

    fachada = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", "8710", "--log-level", "warning"],
                               cwd=RAIZ / "fachada")
    try:
        for _ in range(40):
            try:
                httpx.get(API + "/api/v1/chamados", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        if novo:
            semear(dados["credenciais"])
        print("tela em http://127.0.0.1:8720", flush=True)
        ThreadingHTTPServer(("127.0.0.1", 8720), Tela).serve_forever()
    finally:
        fachada.terminate()


if __name__ == "__main__":
    principal()
