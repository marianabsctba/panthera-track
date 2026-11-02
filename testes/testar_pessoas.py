"""Login, MFA, permissões e 15 pessoas do cliente abrindo chamados, em instâncias de verdade.

sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_pessoas.py'
"""
import hashlib
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

from psycopg.rows import dict_row  # noqa: E402
from track_api import pessoas, provisao  # noqa: E402
from track_api.banco import decifrar  # noqa: E402
from track_api.motor import Motor  # noqa: E402

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
        confere("código nunca repete na fila", len({x["codigo"] for x in d["chamados"]}) == 16, [x["codigo"] for x in d["chamados"]])
        confere("clientes têm siglas diferentes", a["sigla"] != b["sigla"], (a["sigla"], b["sigla"]))
        confere("fila não leva dado de contato", all("contato" not in x for x in d["chamados"]))

        cc, _s, _c = entrar_pela_primeira_vez(caio)
        f = cc.get("/api/v1/tela/chamados").json()
        confere("analista só vê os clientes vinculados", {x["cliente"] for x in f["chamados"]} == {nome_a})
        confere("analista não alcança cliente sem vínculo", cc.get(f"/api/v1/tela/clientes/{cid_b}/chamados/{num_b}").status_code == 404)
        confere("analista não opera cliente sem vínculo", cc.post(f"/api/v1/tela/clientes/{cid_b}/chamados/{num_b}/assumir").status_code == 404)

        # Ciclo com autor real
        confere("não encerra sem resolução", c.post(p0 + "/encerrar").status_code == 409)

        # Detalhes
        r = s1.post("/api/v1/tela/chamados", json={"titulo": "Com detalhes", "descricao": "x", "criticidade": "baixa", "tipo": "requisicao", "categoria": "acesso",
                                                   "ativo": "NOTE-12", "contato_telefone": "+55 41 99999-0000"})
        det = r.json()
        confere("portal guarda tipo, categoria e ativo", r.status_code == 201 and det["tipo"] == "requisicao" and det["categoria"] == "Acesso" and det["contexto"]["ativo"] == "NOTE-12", r.text)
        pd = f"/api/v1/tela/clientes/{cid_a}/chamados/{det['numero']}"
        v = s1.get(pd).json()
        confere("contato vem do formulário e da conta", v["contato"]["telefone"] == "+55 41 99999-0000" and v["contato"]["email"] == do_cliente[1]["email"], v.get("contato"))
        confere("telefone inválido recusado", s1.post("/api/v1/tela/chamados", json={"titulo": "x", "descricao": "x", "criticidade": "baixa", "contato_telefone": "<b>"}).status_code == 422)
        confere("cliente não reclassifica", s1.post(pd + "/classificar", json={"tipo": "incidente", "categoria": "rede"}).status_code == 403)
        r = c.post(pd + "/classificar", json={"tipo": "incidente", "categoria": "rede"})
        confere("analista reclassifica", r.status_code == 200 and r.json()["categoria"] == "Rede" and r.json()["tipo"] == "incidente", r.text)
        confere("reclassificar fica no histórico", any(e["tipo"] == "classificado" and e["detalhe"] == "Incidente, Rede" for e in c.get(pd + "/historico").json()["eventos"]))
        confere("analista vê a resposta pendente", c.get(pd).json()["sla"]["resposta"]["em"] is None)

        conteudo = b"evidencia de teste\n" * 100
        r = s1.post(pd + "/anexos", files={"arquivo": ("../../prova final.txt", conteudo, "text/plain")})
        confere("anexa arquivo e limpa o caminho do nome", r.status_code == 201 and r.json()["nome"] == "prova final.txt", r.text)
        confere("anexo registra o hash", r.json().get("sha256") == hashlib.sha256(conteudo).hexdigest())
        confere("extensão perigosa recusada", s1.post(pd + "/anexos", files={"arquivo": ("x.html", b"<script>", "text/html")}).status_code == 415)
        confere("arquivo sem extensão recusado", s1.post(pd + "/anexos", files={"arquivo": ("x", b"a")}).status_code == 415)
        confere("arquivo vazio recusado", s1.post(pd + "/anexos", files={"arquivo": ("x.txt", b"")}).status_code == 422)
        confere("arquivo acima de 10 MB recusado", s1.post(pd + "/anexos", files={"arquivo": ("g.txt", b"x" * (10 * 1024 * 1024 + 1))}).status_code == 413)
        anexos = c.get(pd).json()["anexos"]
        confere("analista vê o anexo com o autor real", len(anexos) == 1 and anexos[0]["autor"] == "Pessoa 1" and anexos[0]["nome"] == "prova final.txt", anexos)
        confere("motor calculou o próprio hash", anexos[0]["sha1"] == hashlib.sha1(conteudo).hexdigest(), anexos)
        confere("captura de rede é aceita", s1.post(pd + "/anexos", files={"arquivo": ("captura.pcap", b"\xd4\xc3\xb2\xa1")}).status_code == 201)
        confere("log é aceito", s1.post(pd + "/anexos", files={"arquivo": ("servidor.log", b"linha")}).status_code == 201)
        pa = f"{pd}/anexos/{anexos[0]['id']}"
        r = c.get(pa)
        confere("baixa o conteúdo igual", r.content == conteudo)
        confere("download nunca abre na tela", r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment"), r.headers)
        confere("outra pessoa não baixa", s0.get(pa).status_code == 404)
        confere("outra pessoa não anexa", s0.post(pd + "/anexos", files={"arquivo": ("a.txt", b"a")}).status_code == 404)
        confere("outro cliente não baixa", cb.get(pa).status_code == 404)
        confere("anexo de outro chamado não vale nesta rota", c.get(f"{p0}/anexos/{anexos[0]['id']}").status_code == 404)
        confere("anexo fica no histórico", any(e["tipo"] == "anexo" and e["detalhe"] == "prova final.txt, SHA-256 " + hashlib.sha256(conteudo).hexdigest() for e in c.get(pd + "/historico").json()["eventos"]))

        r = c.post(p0 + "/assumir")
        confere("analista assume em nome próprio", r.status_code == 200 and r.json()["atendente"] == "Ana Rocha", r.text)
        confere("analista registra andamento", c.post(p0 + "/andamento", json={"texto": "Analisando o pedido"}).status_code == 200)
        confere("primeira resposta cumprida ao assumir", c.get(p0).json()["sla"]["resposta"]["cumprida"] is True)
        confere("analista pausa", c.post(p0 + "/pausar", json={"texto": "Falta um dado"}).json().get("status") == "aguardando_cliente")
        v = c.get(p0).json()
        confere("mostra o motivo da pausa em curso", v["pausas"] and v["pausas"][-1]["motivo"] == "Falta um dado" and v["pausas"][-1]["em_curso"] is True and v["pausas"][-1]["autor"] == "Ana Rocha", v["pausas"])
        confere("mostra o percentual do prazo", v["sla"]["percentual"] is not None, v["sla"])
        time.sleep(2)
        r = s0.post(p0 + "/comentar", json={"texto": "Segue o dado <script>x</script>"})
        confere("cliente comenta na pausa", r.status_code == 200, r.text)
        confere("resposta do cliente tira da pausa, para Em andamento", r.json().get("status") == "em_andamento" and r.json()["sla"]["pausado"] is False, r.text)
        confere("analista segue como atendente", r.json().get("atendente") == "Ana Rocha", r.text)
        c.post(p0 + "/pausar", json={"texto": "Falta outro dado"}).raise_for_status()
        confere("analista retoma", c.post(p0 + "/retomar").json().get("status") == "em_andamento")
        v = c.get(p0).json()
        confere("tempo por situação conta a pausa", v["tempos"]["aguardando_cliente"] >= 2, v["tempos"])
        confere("as duas pausas ficam registradas", len(v["pausas"]) == 2 and v["pausas"][0]["motivo"] == "Falta um dado", v["pausas"])
        confere("pausa encerrada fica com a duração", not v["pausas"][0]["em_curso"] and v["pausas"][0]["segundos"] >= 2 and not v["pausas"][1]["em_curso"], v["pausas"])
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

        # Escalonamento manual
        r = c.post("/api/v1/tela/chamados", json={"titulo": "Para escalar", "descricao": "x", "criticidade": "alta", "cliente_id": cid_a, "solicitante": "Rui"})
        esc = r.json()
        confere("chamado nasce em N1 e Aberto", esc["nivel"] == "N1" and esc["status"] == "aberto", esc)
        pe = f"/api/v1/tela/clientes/{cid_a}/chamados/{esc['numero']}"
        confere("pessoa do cliente não escala", cg.post(pe + "/escalar", json={"nivel": "N2", "motivo": "x"}).status_code == 403)
        confere("analista sem vínculo não escala", cc.post(f"/api/v1/tela/clientes/{cid_b}/chamados/{num_b}/escalar", json={"nivel": "N2", "motivo": "x"}).status_code == 404)
        confere("escalar exige motivo", c.post(pe + "/escalar", json={"nivel": "N2", "motivo": ""}).status_code == 422)
        confere("nível inexistente recusado", c.post(pe + "/escalar", json={"nivel": "N9", "motivo": "x"}).status_code == 422)
        r = c.post(pe + "/escalar", json={"nivel": "N2", "motivo": "Precisa de análise forense"})
        d2 = r.json()
        confere("escala para N2", r.status_code == 200 and d2["nivel"] == "N2", r.text)
        confere("escalar não muda criticidade nem prazo", d2["criticidade"] == "alta" and d2["sla"]["prazo"] == esc["sla"]["prazo"], d2["sla"])
        confere("não volta nem repete o nível", c.post(pe + "/escalar", json={"nivel": "N2", "motivo": "x"}).status_code == 409)
        confere("escalar não conta como primeira resposta", d2["atendente"] is None and d2["sla"]["resposta"]["em"] is None, d2["sla"])
        r = c.post(pe + "/assumir")
        confere("escalado sem atendente ainda pode ser assumido", r.status_code == 200 and r.json()["atendente"] == "Ana Rocha", r.text)
        confere("assumir conta como primeira resposta", r.json()["sla"]["resposta"]["cumprida"] is True, r.json()["sla"])
        confere("quem já tem atendente não é assumido de novo", c.post(pe + "/assumir").status_code == 409)
        r = c.post(pe + "/escalar", json={"nivel": "N3", "motivo": "Risco alto"})
        confere("escala para N3", r.status_code == 200 and r.json()["nivel"] == "N3", r.text)
        h = c.get(pe + "/historico").json()
        confere("motivo fica no histórico com o autor", any(e["tipo"] == "escalado" and e["detalhe"] == "N3: Risco alto" and e["autor"] == "Ana Rocha" for e in h["eventos"]), h["eventos"])
        confere("fila mostra o nível", next(x["nivel"] for x in c.get("/api/v1/tela/chamados").json()["chamados"] if x["numero"] == esc["numero"] and x["cliente_id"] == cid_a) == "N3")

        # Escalonamento automático, nativo, com um prazo curto de verdade
        pasta_a, projeto_a = provisao.CLIENTES / a["identificador"], f"track-{a['identificador']}"
        tarefa = provisao._compose(pasta_a, projeto_a, "exec", "-T", "db", "sh", "-c",
                                   'mariadb -uglpi -p"$MARIADB_PASSWORD" glpi -N -e "select mode, frequency from glpi_crontasks where name=\'slaticket\'"').stdout.split()
        confere("tarefa de escalonamento roda sozinha a cada minuto", tarefa == ["2", "60"], tarefa)
        with psycopg.connect(os.environ["TRACK_BANCO_DONO_DSN"], row_factory=dict_row) as conn:
            la = conn.execute("SELECT * FROM clientes WHERE id = %s", (cid_a,)).fetchone()
        senha_admin = next(x.split("=", 1)[1] for x in (pasta_a / ".env").read_text().splitlines() if x.startswith("SENHA_ADMIN="))
        admin = Motor(la["motor_url"], la["motor_oauth_id"], decifrar(la["motor_oauth_segredo"]), "glpi", senha_admin)
        slm = admin.pedir("GET", "/Setup/SLM")[0]["id"]
        curto = admin.pedir("POST", "/Setup/SLA", json={"name": "Curto", "slm": slm, "entity": 0, "is_recursive": True, "type": 0, "time": 1,
                                                        "time_unit": "minute", "use_ticket_calendar": False})["id"]
        provisao.criar_niveis_de_sla(pasta_a, projeto_a, curto, la["grupos"], 60)
        r = c.post("/api/v1/tela/chamados", json={"titulo": "Vai vencer", "descricao": "x", "criticidade": "critica", "cliente_id": cid_a, "solicitante": "Rui"})
        auto = r.json()
        pu = f"/api/v1/tela/clientes/{cid_a}/chamados/{auto['numero']}"
        admin.pedir("PATCH", f"/Assistance/Ticket/{auto['numero']}", json={"sla_ttr": curto})
        print("esperando o prazo vencer e a tarefa rodar...", flush=True)
        limite = time.time() + 240
        visto = None
        while time.time() < limite:
            visto = c.get(pu).json()
            if visto["nivel"] == "N3":
                break
            time.sleep(5)
        confere("prazo vencido escala sozinho até N3", visto["nivel"] == "N3", visto)
        confere("escalonamento automático não muda a criticidade", visto["criticidade"] == "critica" and admin.chamado(auto["numero"])["priority"] == 5, visto)
        confere("escalonamento automático não finge primeira resposta", visto["sla"]["resposta"]["em"] is None, visto["sla"])
        confere("chamado estourado aparece como tal", visto["sla"]["estourado"] is True, visto["sla"])
        r = c.post(pu + "/assumir")
        confere("chamado escalado sozinho pode ser assumido", r.status_code == 200 and r.json()["atendente"] == "Ana Rocha", r.text)

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
