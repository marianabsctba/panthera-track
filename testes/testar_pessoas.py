"""Login, MFA, permissões e 15 pessoas do cliente abrindo chamados, em instâncias de verdade.

sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_pessoas.py'
"""
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg
import pyotp

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import pessoas, provisao  # noqa: E402

API = "http://127.0.0.1:8711"
PRAZOS = {"critica": 4, "alta": 8, "media": 24, "baixa": 48, "consulta": 72}
SENHA = "senha-de-teste-" + uuid.uuid4().hex
falhas = []


def confere(nome, condicao, detalhe=""):
    print(("ok    " if condicao else "FALHA ") + nome + (f" | {str(detalhe)[:400]}" if not condicao else ""), flush=True)
    if not condicao:
        falhas.append(nome)


def cliente_http():
    return httpx.Client(base_url=API, headers={"X-Track-Tela": "1"}, timeout=120)


def entrar_pela_primeira_vez(dados):
    """Faz o caminho inteiro e devolve a sessão, o segredo do autenticador e os códigos de recuperação."""
    c = cliente_http()
    c.post("/api/v1/sessao", json={"email": dados["email"], "senha": dados["senha_provisoria"]}).raise_for_status()
    c.post("/api/v1/sessao/senha", json={"senha_atual": dados["senha_provisoria"], "senha_nova": SENHA}).raise_for_status()
    segredo = c.get("/api/v1/sessao/mfa").json()["segredo"]
    r = c.post("/api/v1/sessao/mfa/ativar", json={"codigo": pyotp.TOTP(segredo).now()})
    r.raise_for_status()
    return c, segredo, r.json()["codigos_recuperacao"]


def principal():
    sufixo = uuid.uuid4().hex[:6]
    nome_a, nome_b = f"alfa-{sufixo}", f"beta-{sufixo}"
    print("provisionando duas instâncias...", flush=True)
    a = provisao.criar(nome_a, PRAZOS)
    b = srv = None
    try:
        b = provisao.criar(nome_b, PRAZOS)
        print("criando pessoas...", flush=True)
        e = lambda n: f"{n}.{sufixo}@exemplo.org"  # noqa: E731
        ana = pessoas.criar("Ana Rocha", e("ana"), "analista", [nome_a, nome_b])
        caio = pessoas.criar("Caio Lima", e("caio"), "analista", [nome_a])
        gestora = pessoas.criar("Gil Gestora", e("gil"), "cliente", [nome_a], "gestor")
        beto = pessoas.criar("Beto de Beta", e("beto"), "cliente", [nome_b], "solicitante")
        travada = pessoas.criar("Tina Trava", e("tina"), "cliente", [nome_a], "solicitante")
        with ThreadPoolExecutor(max_workers=5) as grupo:
            do_cliente = list(grupo.map(lambda i: pessoas.criar(f"Pessoa {i}", e(f"p{i}"), "cliente", [nome_a], "solicitante"), range(15)))

        env = {**os.environ, "TRACK_COOKIE_SEGURO": "0"}
        srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "track_api.app:app", "--port", "8711", "--log-level", "warning"],
                               cwd=RAIZ / "fachada", env=env)
        for _ in range(40):
            try:
                httpx.get(API + "/", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)

        # Tela e cabeçalhos
        r = httpx.get(API + "/")
        confere("tela é servida", r.status_code == 200 and "Panthera" in r.text)
        confere("tela tem CSP sem script inline", "default-src 'self'" in r.headers.get("content-security-policy", "") and "<script>" not in r.text)
        js = httpx.get(API + "/estatico/console.js").text + r.text
        confere("tela não revela a ferramenta", "glpi" not in js.lower())
        confere("simbolo é servido", httpx.get(API + "/estatico/pantera.webp").status_code == 200)

        # Entrada
        c = cliente_http()
        confere("sem sessão não vê nada", c.get("/api/v1/tela/chamados").status_code == 401)
        confere("senha errada recusada", c.post("/api/v1/sessao", json={"email": ana["email"], "senha": "errada"}).status_code == 401)
        confere("e-mail inexistente responde igual", c.post("/api/v1/sessao", json={"email": "nada@exemplo.org", "senha": "x"}).status_code == 401)
        r = httpx.post(API + "/api/v1/sessao", json={"email": ana["email"], "senha": ana["senha_provisoria"]})
        confere("pedido sem o cabeçalho da tela é recusado", r.status_code == 403)
        r = c.post("/api/v1/sessao", json={"email": ana["email"], "senha": ana["senha_provisoria"]})
        confere("primeira entrada pede troca de senha", r.json().get("etapa") == "trocar_senha", r.text)
        cookie = r.headers.get("set-cookie", "").lower()
        confere("cookie é HttpOnly e SameSite Strict", "httponly" in cookie and "samesite=strict" in cookie, cookie)
        confere("sem concluir a entrada não vê a fila", c.get("/api/v1/tela/chamados").status_code == 403)
        confere("senha curta recusada", c.post("/api/v1/sessao/senha", json={"senha_atual": ana["senha_provisoria"], "senha_nova": "curta"}).status_code == 422)
        confere("não pula para o cadastro do segundo fator", c.get("/api/v1/sessao/mfa").status_code == 409)
        r = c.post("/api/v1/sessao/senha", json={"senha_atual": ana["senha_provisoria"], "senha_nova": SENHA})
        confere("troca a senha", r.json().get("etapa") == "cadastrar_mfa", r.text)
        confere("ainda sem segundo fator não vê a fila", c.get("/api/v1/tela/chamados").status_code == 403)
        d = c.get("/api/v1/sessao/mfa").json()
        segredo = d["segredo"]
        confere("mostra QR", d["qr"].startswith("data:image/svg+xml"))
        confere("código errado no cadastro recusado", c.post("/api/v1/sessao/mfa/ativar", json={"codigo": "000000"}).status_code == 401)
        r = c.post("/api/v1/sessao/mfa/ativar", json={"codigo": pyotp.TOTP(segredo).now()})
        codigos = r.json().get("codigos_recuperacao", [])
        confere("ativa o segundo fator e entrega 10 códigos", r.status_code == 200 and len(codigos) == 10, r.text)
        confere("entrada completa vê a fila", c.get("/api/v1/tela/chamados").status_code == 200)

        c2 = cliente_http()
        r = c2.post("/api/v1/sessao", json={"email": ana["email"], "senha": SENHA})
        confere("nova entrada pede o código", r.json().get("etapa") == "codigo_mfa", r.text)
        confere("sem o código não vê a fila", c2.get("/api/v1/tela/chamados").status_code == 403)
        confere("mesmo código do cadastro não vale de novo", c2.post("/api/v1/sessao/mfa", json={"codigo": pyotp.TOTP(segredo).now()}).status_code == 401)
        r = c2.post("/api/v1/sessao/mfa", json={"codigo": codigos[0]})
        confere("código de recuperação vale", r.json().get("etapa") == "pronto", r.text)
        c3 = cliente_http()
        c3.post("/api/v1/sessao", json={"email": ana["email"], "senha": SENHA})
        confere("código de recuperação não vale duas vezes", c3.post("/api/v1/sessao/mfa", json={"codigo": codigos[0]}).status_code == 401)
        c2.delete("/api/v1/sessao")
        confere("sair encerra a sessão", c2.get("/api/v1/tela/chamados").status_code == 401)

        ct = cliente_http()
        for _ in range(5):
            ct.post("/api/v1/sessao", json={"email": travada["email"], "senha": "errada"})
        r = ct.post("/api/v1/sessao", json={"email": travada["email"], "senha": travada["senha_provisoria"]})
        confere("cinco erros bloqueiam a conta, mesmo com a senha certa", r.status_code == 429, r.text)

        # 15 pessoas abrindo chamados ao mesmo tempo
        print("15 pessoas entrando e abrindo chamados...", flush=True)

        def abrir_pela_pessoa(par):
            i, dados = par
            sessao, _s, _c = entrar_pela_primeira_vez(dados)
            r = sessao.post("/api/v1/tela/chamados", json={"titulo": f"Pedido da pessoa {i}", "descricao": "Preciso de ajuda",
                                                           "criticidade": "media"})
            return sessao, r
        with ThreadPoolExecutor(max_workers=15) as grupo:
            resultado = list(grupo.map(abrir_pela_pessoa, enumerate(do_cliente)))
        confere("as 15 abrem chamado", all(r.status_code == 201 for _, r in resultado), [r.text for _, r in resultado if r.status_code != 201])
        confere("números distintos", len({r.json()["numero"] for _, r in resultado}) == 15)
        confere("SLA nasce da criticidade", all(r.json()["sla"]["prazo"] for _, r in resultado))
        confere("solicitante é a pessoa", resultado[3][1].json()["solicitante"] == "Pessoa 3" and resultado[3][1].json()["origem"] == "portal")

        s0, r0 = resultado[0]
        s1, r1 = resultado[1]
        num0 = r0.json()["numero"]
        cid_a = r0.json()["cliente_id"]
        p0 = f"/api/v1/tela/clientes/{cid_a}/chamados/{num0}"
        f = s0.get("/api/v1/tela/chamados").json()["chamados"]
        confere("solicitante só vê o próprio na fila", [x["numero"] for x in f] == [num0], f)
        confere("solicitante não abre chamado de outra pessoa", s1.get(p0).status_code == 404)
        confere("solicitante não lê histórico de outra pessoa", s1.get(p0 + "/historico").status_code == 404)
        confere("solicitante não comenta em chamado de outra pessoa", s1.post(p0 + "/comentar", json={"texto": "oi"}).status_code == 404)
        confere("pessoa do cliente não assume", s0.post(p0 + "/assumir").status_code == 403)
        confere("pessoa do cliente não encerra", s0.post(p0 + "/encerrar").status_code == 403)

        cg, _s, _c = entrar_pela_primeira_vez(gestora)
        confere("gestor vê os 15 do cliente", len(cg.get("/api/v1/tela/chamados").json()["chamados"]) == 15)
        confere("gestor lê chamado de outra pessoa", cg.get(p0).status_code == 200)

        # Fila única do analista
        cb, _s, _c = entrar_pela_primeira_vez(beto)
        r = cb.post("/api/v1/tela/chamados", json={"titulo": "Pedido crítico de Beta", "descricao": "Urgente", "criticidade": "critica"})
        confere("pessoa do outro cliente abre", r.status_code == 201, r.text)
        num_b, cid_b = r.json()["numero"], r.json()["cliente_id"]
        confere("outro cliente não vê os de Alfa", [x["cliente"] for x in cb.get("/api/v1/tela/chamados").json()["chamados"]] == [nome_b])
        confere("outro cliente não alcança chamado de Alfa", cb.get(p0).status_code == 404)

        d = c.get("/api/v1/tela/chamados").json()
        confere("analista vê os dois clientes na mesma fila", {x["cliente"] for x in d["chamados"]} == {nome_a, nome_b}, d["clientes"])
        confere("fila põe na frente o mais perto de estourar", d["chamados"][0]["cliente"] == nome_b and d["chamados"][0]["criticidade"] == "critica", d["chamados"][0])
        confere("mesmo número em clientes distintos não se confunde", len({(x["cliente_id"], x["numero"]) for x in d["chamados"]}) == 16)

        cc, _s, _c = entrar_pela_primeira_vez(caio)
        f = cc.get("/api/v1/tela/chamados").json()
        confere("analista só vê os clientes vinculados", {x["cliente"] for x in f["chamados"]} == {nome_a})
        confere("analista não alcança cliente sem vínculo", cc.get(f"/api/v1/tela/clientes/{cid_b}/chamados/{num_b}").status_code == 404)
        confere("analista não opera cliente sem vínculo", cc.post(f"/api/v1/tela/clientes/{cid_b}/chamados/{num_b}/assumir").status_code == 404)

        # Ciclo com autor real
        confere("não encerra sem resolução", c.post(p0 + "/encerrar").status_code == 409)
        r = c.post(p0 + "/assumir")
        confere("analista assume em nome próprio", r.status_code == 200 and r.json()["atendente"] == "Ana Rocha", r.text)
        confere("analista registra andamento", c.post(p0 + "/andamento", json={"texto": "Analisando o pedido"}).status_code == 200)
        confere("analista pausa", c.post(p0 + "/pausar", json={"texto": "Falta um dado"}).json().get("status") == "aguardando_cliente")
        r = s0.post(p0 + "/comentar", json={"texto": "Segue o dado <script>x</script>"})
        confere("cliente comenta na pausa", r.status_code == 200, r.text)
        confere("analista retoma", c.post(p0 + "/retomar").json().get("status") == "em_andamento")
        confere("analista resolve", c.post(p0 + "/resolver", json={"texto": "Pedido atendido"}).json().get("status") == "resolvido")
        confere("cliente não comenta em resolvido", s0.post(p0 + "/comentar", json={"texto": "x"}).status_code == 409)
        r = c.post(p0 + "/encerrar")
        confere("analista encerra", r.json().get("status") == "encerrado", r.text)
        h = s0.get(p0 + "/historico")
        autores = {i["conteudo"]: i["autor"] for i in h.json()["interacoes"]}
        confere("cliente vê o histórico do próprio chamado", h.status_code == 200)
        confere("registro do motor traz o autor real do analista", any(v == "Ana Rocha" and "Analisando" in k for k, v in autores.items()), autores)
        confere("registro do motor traz o autor real do cliente", any(v == "Pessoa 0" and "Segue o dado" in k for k, v in autores.items()), autores)
        confere("comentário é escapado", not any("<script>" in k for k in autores), autores)
        confere("evento traz o autor", {e["autor"] for e in h.json()["eventos"]} == {"Pessoa 0", "Ana Rocha"}, h.json()["eventos"])
        confere("resposta não revela a ferramenta nem o usuário-sombra", "glpi" not in h.text.lower() and not re.search(r"\bp[0-9a-f]{16}\b", h.text), h.text)

        # Telefone
        r = c.post("/api/v1/tela/chamados", json={"titulo": "Ligação do plantão", "descricao": "Cliente ligou", "criticidade": "alta",
                                                  "cliente_id": cid_a, "solicitante": "João, por telefone"})
        confere("analista registra chamado por telefone", r.status_code == 201 and r.json()["origem"] == "telefone", r.text)
        r = cc.post("/api/v1/tela/chamados", json={"titulo": "x", "descricao": "x", "criticidade": "alta", "cliente_id": cid_b, "solicitante": "y"})
        confere("não registra em cliente sem vínculo", r.status_code == 404, r.text)
        r = c.post("/api/v1/tela/chamados", json={"titulo": "x", "descricao": "x", "criticidade": "alta"})
        confere("telefone exige cliente e quem pediu", r.status_code == 422)

        # Banco e instância fora do ar
        with psycopg.connect(os.environ["TRACK_BANCO_DSN"]) as conn:
            try:
                conn.execute("INSERT INTO pessoas (email, nome, tipo, senha_hash) VALUES ('x@x', 'x', 'analista', 'x')")
                confere("API não cria pessoa no banco", False)
            except psycopg.errors.InsufficientPrivilege:
                confere("API não cria pessoa no banco", True)
        provisao._compose(provisao.CLIENTES / b["identificador"], f"track-{b['identificador']}", "stop", "glpi")
        r = c.get("/api/v1/tela/chamados")
        d = r.json()
        confere("cliente fora do ar não derruba a fila", r.status_code == 200 and d["indisponiveis"] == [nome_b] and len(d["chamados"]) > 0, r.text)
        pessoas.desativar(caio["email"])
        confere("pessoa desativada perde a sessão", cc.get("/api/v1/tela/chamados").status_code == 401)
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
