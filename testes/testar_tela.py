"""Percorre a console num navegador de verdade, da primeira entrada ao encerramento. Precisa da demonstração no ar.

Usa contas descartáveis e não imprime senha nem segredo. A captura só acontece depois da entrada.
pip install playwright && playwright install --with-deps chromium
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; python testes/testar_tela.py --capturas /tmp/capturas'
"""
import argparse
import re
import sys
import uuid
from pathlib import Path

import pyotp
from playwright.sync_api import expect, sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "fachada"))

from track_api import pessoas  # noqa: E402

TELA = "http://127.0.0.1:8720"
falhas = []


def confere(nome, verificar):
    try:
        verificar()
        print("ok    " + nome, flush=True)
    except Exception as e:  # noqa: BLE001
        falhas.append(nome)
        print("FALHA " + nome + " | " + str(e).splitlines()[0][:300], flush=True)


def _verdadeiro(valor):
    if not valor:
        raise AssertionError("condição falsa")


def primeira_entrada(pagina, conta, senha_nova):
    pagina.goto(TELA)
    pagina.get_by_label("E-mail").fill(conta["email"])
    pagina.get_by_label("Senha", exact=True).fill(conta["senha_provisoria"])
    pagina.get_by_role("button", name="Entrar").click()
    pagina.get_by_label("Senha provisória").fill(conta["senha_provisoria"])
    pagina.get_by_label("Nova senha").fill(senha_nova)
    pagina.get_by_role("button", name="Trocar senha").click()
    expect(pagina.get_by_test_id("segredo")).not_to_be_empty()
    pagina.get_by_label("Código de 6 números").fill(pyotp.TOTP(pagina.get_by_test_id("segredo").inner_text()).now())
    pagina.get_by_role("button", name="Ativar").click()
    pagina.get_by_role("button", name="Já guardei").click()


def escolher(pagina, rotulo, opcao, dentro=None):
    alvo = dentro or pagina
    alvo.locator("input").and_(alvo.get_by_label(rotulo, exact=True)).click()
    pagina.get_by_role("option", name=opcao, exact=True).click()


def principal():
    p = argparse.ArgumentParser()
    p.add_argument("--capturas", required=True)
    capturas = Path(p.parse_args().capturas)
    capturas.mkdir(parents=True, exist_ok=True)

    sufixo = uuid.uuid4().hex[:8]
    conta = pessoas.criar("Verificador", f"verif-{sufixo}@exemplo.org", "analista", ["Cliente Alfa", "Cliente Beta"])
    cliente = pessoas.criar("Cleo Cliente", f"cleo-{sufixo}@exemplo.org", "cliente", ["Cliente Alfa"], "solicitante")
    gestor = pessoas.criar("Gaia Gestora", f"gaia-{sufixo}@exemplo.org", "cliente", ["Cliente Alfa"], "gestor")
    senha_nova = "senha-" + uuid.uuid4().hex
    erros = []
    try:
        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            pagina = nav.new_page(viewport={"width": 1440, "height": 900})
            pagina.on("pageerror", lambda e: erros.append(str(e)))
            # Respostas de erro de propósito não contam, mas violação de CSP conta.
            pagina.on("console", lambda m: erros.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
            pagina.goto(TELA)

            confere("mostra a entrada com o símbolo", lambda: expect(pagina.locator("img[src*='pantera']").first).to_be_visible())
            confere("usa a fonte e o tema da console", lambda: _verdadeiro("Inter" in pagina.evaluate("getComputedStyle(document.body).fontFamily")))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "a-entrada.png")
            pagina.get_by_label("E-mail").fill(conta["email"])
            pagina.get_by_label("Senha", exact=True).fill("errada")
            pagina.get_by_role("button", name="Entrar").click()
            confere("senha errada mostra aviso", lambda: expect(pagina.get_by_role("alert")).to_contain_text("incorretos"))
            pagina.get_by_label("Senha", exact=True).fill(conta["senha_provisoria"])
            pagina.get_by_role("button", name="Entrar").click()

            confere("pede a troca de senha", lambda: expect(pagina.get_by_label("Senha provisória")).to_be_visible())
            pagina.get_by_label("Senha provisória").fill(conta["senha_provisoria"])
            pagina.get_by_label("Nova senha").fill(senha_nova)
            pagina.get_by_role("button", name="Trocar senha").click()

            confere("mostra o QR", lambda: expect(pagina.get_by_test_id("qr")).to_have_attribute("src", re.compile("^data:image/svg")))
            expect(pagina.get_by_test_id("segredo")).not_to_be_empty()
            pagina.wait_for_timeout(300)
            confere("o QR carrega apesar da CSP", lambda: _verdadeiro(pagina.get_by_test_id("qr").evaluate("e => e.naturalWidth > 0")))
            pagina.get_by_label("Código de 6 números").fill(pyotp.TOTP(pagina.get_by_test_id("segredo").inner_text()).now())
            pagina.get_by_role("button", name="Ativar").click()
            confere("entrega os códigos de recuperação", lambda: expect(pagina.get_by_role("button", name="Já guardei")).to_be_visible())
            pagina.get_by_role("button", name="Já guardei").click()

            itens = pagina.get_by_test_id("item-fila")
            detalhe = pagina.locator("main")
            confere("mostra a fila", lambda: expect(itens.first).to_be_visible())
            confere("barra superior é a metálica", lambda: expect(pagina.locator(".barra-metal")).to_be_visible())
            confere("menu lateral marca a tela atual", lambda: expect(pagina.locator(".menu-item[aria-current='page']")).to_contain_text("Fila de chamados"))
            confere("fila traz os dois clientes", lambda: (expect(pagina.locator("main")).to_contain_text("Cliente Alfa"), expect(pagina.locator("main")).to_contain_text("Cliente Beta")))
            confere("código não repete na fila", lambda: _verdadeiro(len(set(c := pagina.get_by_test_id("codigo").all_inner_texts())) == len(c) > 0))
            confere("não revela a ferramenta", lambda: expect(pagina.locator("body")).not_to_contain_text("GLPI"))

            total = itens.count()
            pagina.get_by_role("button", name=re.compile("^Encerrado")).click()
            confere("número filtra o que conta", lambda: expect(itens).to_have_count(1))
            pagina.get_by_role("button", name=re.compile("^Encerrado")).click()
            confere("clicar de novo tira o filtro", lambda: expect(itens).to_have_count(total))
            pagina.get_by_role("searchbox", name="Filtrar a lista").fill("Força bruta")
            confere("busca filtra a lista", lambda: expect(itens).to_have_count(1))
            itens.first.click()
            confere("mostra o contexto do incidente", lambda: (expect(detalhe).to_contain_text("203.0.113.45"), expect(detalhe).to_contain_text("T1110.001")))
            confere("técnica leva ao site da MITRE", lambda: expect(pagina.get_by_role("link", name="T1110.001")).to_have_attribute("href", "https://attack.mitre.org/techniques/T1110/001/"))
            pagina.wait_for_timeout(400)
            pagina.get_by_role("searchbox", name="Filtrar a lista").fill("")
            pagina.wait_for_timeout(500)
            pagina.screenshot(path=capturas / "b-fila.png")

            # Um chamado do Cliente Alfa fica com pedido aberto, para o gestor decidir depois
            pagina.get_by_role("button", name="Registrar chamado").click()
            dialogo = pagina.get_by_role("dialog")
            dialogo.get_by_label("Quem pediu").fill("Rui")
            dialogo.get_by_label("Título").fill("Reduzir criticidade deste")
            dialogo.get_by_label("Descrição").fill("Chamado para o aceite do gestor.")
            dialogo.get_by_role("button", name="Abrir chamado").click()
            expect(pagina.get_by_test_id("titulo-chamado")).to_contain_text("Reduzir criticidade deste")
            pagina.get_by_role("button", name="Mais ações").click()
            pagina.get_by_role("menuitem", name="Pedir mudança de criticidade").click()
            dialogo = pagina.get_by_role("dialog")
            escolher(pagina, "Nova criticidade", "Baixa", dialogo)
            dialogo.get_by_label("Motivo").fill("Impacto menor que o esperado")
            dialogo.get_by_role("button", name="Pedir aceite").click()
            expect(dialogo).to_be_hidden()

            # Registro por telefone
            pagina.get_by_role("button", name="Registrar chamado").click()
            dialogo = pagina.get_by_role("dialog")
            confere("botão fica desligado sem os campos", lambda: expect(dialogo.get_by_role("button", name="Abrir chamado")).to_be_disabled())
            dialogo.get_by_label("Quem pediu").fill("João, por telefone")
            dialogo.get_by_label("Título").fill("Ligação sobre acesso indevido")
            dialogo.get_by_label("Descrição").fill("Cliente ligou relatando acesso estranho.")
            escolher(pagina, "Categoria", "Acesso", dialogo)
            escolher(pagina, "Criticidade", "Crítica", dialogo)
            dialogo.get_by_label("Ativo ou local afetado").fill("SRV-WEB-01")
            dialogo.get_by_label("Telefone para contato").fill("+55 41 99999-0000")
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "c-novo.png")
            dialogo.get_by_role("button", name="Abrir chamado").click()

            titulo = pagina.get_by_test_id("titulo-chamado")
            confere("abre o chamado registrado", lambda: expect(titulo).to_contain_text("Ligação sobre acesso indevido"))
            confere("mostra o código com a sigla", lambda: expect(titulo).to_contain_text(re.compile(r"ALFA-\d{4}")))
            dados = pagina.get_by_test_id("dados")
            confere("mostra origem, classificação e contato", lambda: (expect(dados).to_contain_text("Telefone"), expect(dados).to_contain_text("Acesso"), expect(dados).to_contain_text("99999-0000")))
            confere("mostra o ativo", lambda: expect(detalhe).to_contain_text("SRV-WEB-01"))

            pagina.get_by_role("tab", name="Prazos").click()
            confere("mostra os dois prazos", lambda: (expect(detalhe).to_contain_text("Primeira resposta"), expect(detalhe.get_by_text("Resolução", exact=True)).to_be_visible()))

            pagina.get_by_role("tab", name="Anexos").click()
            pagina.locator("input[type=file]").set_input_files({"name": "evidencia.txt", "mimeType": "text/plain", "buffer": b"prova"})
            confere("anexa pela tela", lambda: expect(pagina.get_by_role("link", name="evidencia.txt")).to_be_visible())
            pagina.locator("input[type=file]").set_input_files({"name": "virus.exe", "mimeType": "application/octet-stream", "buffer": b"x"})
            confere("extensão recusada mostra aviso", lambda: expect(pagina.get_by_role("alert")).to_contain_text("não aceito"))

            pagina.get_by_role("button", name="Mais ações").click()
            pagina.get_by_role("menuitem", name="Classificar").click()
            dialogo = pagina.get_by_role("dialog")
            escolher(pagina, "Categoria", "Malware", dialogo)
            dialogo.get_by_role("button", name="Salvar").click()
            expect(dialogo).to_be_hidden()
            pagina.get_by_role("tab", name="Resumo").click()
            confere("reclassifica pela tela", lambda: expect(dados).to_contain_text("Malware"))

            pagina.get_by_role("button", name="Mais ações").click()
            pagina.get_by_role("menuitem", name="Escalar").click()
            dialogo = pagina.get_by_role("dialog")
            dialogo.get_by_label("Motivo").fill("Precisa de análise forense")
            dialogo.get_by_role("button", name="Escalar").click()
            expect(dialogo).to_be_hidden()
            confere("escala pela tela", lambda: expect(pagina.locator("main").get_by_test_id("nivel").last).to_have_text("N2"))
            confere("fila mostra o nível", lambda: expect(itens.filter(has_text="Ligação sobre").first.get_by_test_id("nivel")).to_have_text("N2"))
            pagina.get_by_role("tab", name="Histórico").click()
            confere("motivo do escalonamento aparece", lambda: expect(detalhe.get_by_text("N2: Precisa de análise forense", exact=True)).to_be_visible())
            confere("histórico guarda o SHA-256 do anexo", lambda: expect(detalhe).to_contain_text("SHA-256"))

            # Aceite do cliente
            pagina.get_by_role("button", name="Mais ações").click()
            pagina.get_by_role("menuitem", name="Pedir mudança de criticidade").click()
            dialogo = pagina.get_by_role("dialog")
            confere("pedir aceite exige criticidade e motivo", lambda: expect(dialogo.get_by_role("button", name="Pedir aceite")).to_be_disabled())
            escolher(pagina, "Nova criticidade", "Alta", dialogo)
            dialogo.get_by_label("Motivo").fill("Afetou menos sistemas do que parecia")
            dialogo.get_by_role("button", name="Pedir aceite").click()
            expect(dialogo).to_be_hidden()
            confere("analista vê que aguarda o cliente", lambda: expect(pagina.get_by_test_id("aceite")).to_contain_text("Aguardando aceite do cliente"))
            confere("analista não tem botão de aceitar", lambda: expect(pagina.get_by_role("button", name="Aceitar")).to_be_hidden())
            confere("fila marca o aguardo", lambda: expect(itens.filter(has_text="Ligação sobre").first).to_contain_text("Aguardando aceite"))
            confere("criticidade não muda antes do aceite", lambda: expect(pagina.get_by_test_id("titulo-chamado").locator("xpath=..")).to_contain_text("Crítica"))

            pagina.get_by_role("button", name="Assumir atendimento").click()
            pagina.get_by_role("tab", name="Resumo").click()
            confere("assume em nome próprio", lambda: expect(dados).to_contain_text("Verificador"))
            pagina.get_by_role("button", name="Aguardar cliente").click()
            dialogo = pagina.get_by_role("dialog")
            dialogo.get_by_label("O que falta do cliente").fill("Cliente precisa confirmar o horário do acesso.")
            dialogo.get_by_role("button", name="Pausar o prazo").click()
            expect(dialogo).to_be_hidden()
            confere("a fila mostra o SLA pausado", lambda: expect(itens.filter(has_text="Ligação sobre").first).to_contain_text("Pausado"))
            pagina.get_by_role("tab", name="Prazos").click()
            confere("mostra o motivo da pausa", lambda: expect(detalhe.get_by_text("Cliente precisa confirmar o horário do acesso.").first).to_be_visible())
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "d-prazos.png")
            pagina.get_by_role("button", name="Retomar").click()
            expect(pagina.get_by_role("button", name="Resolver")).to_be_visible()
            pagina.get_by_role("button", name="Resolver").click()
            dialogo = pagina.get_by_role("dialog")
            confere("resolver exige a solução", lambda: expect(dialogo.get_by_role("button", name="Resolver")).to_be_disabled())
            dialogo.get_by_label("Solução adotada").fill("Acesso legítimo confirmado com o cliente.")
            dialogo.get_by_role("button", name="Resolver").click()
            expect(dialogo).to_be_hidden()
            pagina.get_by_role("button", name="Encerrar").click()
            confere("encerra", lambda: expect(pagina.locator("main").get_by_text("Encerrado", exact=True).last).to_be_visible())
            confere("encerrado não oferece mais ações", lambda: expect(pagina.get_by_role("button", name="Mais ações")).to_be_hidden())
            pagina.get_by_role("tab", name="Histórico").click()
            confere("histórico traz o autor real", lambda: expect(detalhe.get_by_text(re.compile(r", Verificador$")).first).to_be_visible())
            confere("registro traz a solução", lambda: expect(detalhe).to_contain_text("Acesso legítimo confirmado"))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "e-historico.png")

            pagina.set_viewport_size({"width": 390, "height": 800})
            pagina.wait_for_timeout(500)
            confere("cabe no celular sem rolagem lateral", lambda: _verdadeiro(pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")))
            confere("no celular o detalhe abre em gaveta", lambda: expect(pagina.get_by_role("dialog")).to_be_visible())
            pagina.screenshot(path=capturas / "g-gaveta.png")
            pagina.keyboard.press("Escape")
            expect(pagina.get_by_role("dialog")).to_be_hidden()
            pagina.wait_for_timeout(300)
            confere("fila cabe no celular sem rolagem lateral", lambda: _verdadeiro(pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")))
            pagina.screenshot(path=capturas / "f-celular.png")
            pagina.set_viewport_size({"width": 1440, "height": 900})

            pagina.get_by_role("button", name="Menu da conta").click()
            pagina.get_by_role("menuitem", name="Sair").click()
            confere("sair volta para a entrada", lambda: expect(pagina.get_by_role("button", name="Entrar")).to_be_visible())
            pagina.reload()
            confere("depois de sair, recarregar não entra", lambda: expect(pagina.get_by_role("button", name="Entrar")).to_be_visible())

            # Gestor decide o aceite pedido lá em cima, num chamado ainda aberto
            primeira_entrada(pagina, gestor, senha_nova)
            pagina.get_by_role("button", name=re.compile("^Encerrado")).wait_for()
            confere("gestor vê o chamado que aguarda a decisão dele", lambda: expect(itens.filter(has_text="Reduzir criticidade")).to_have_count(1))
            itens.filter(has_text="Reduzir criticidade").click()
            confere("gestor vê o pedido com os botões", lambda: (expect(pagina.get_by_test_id("aceite")).to_contain_text("Sua decisão é necessária"), expect(pagina.get_by_role("button", name="Aceitar")).to_be_visible()))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "i-aceite.png")
            pagina.get_by_role("button", name="Aceitar").click()
            confere("aceite aplica a nova criticidade", lambda: expect(pagina.get_by_test_id("titulo-chamado").locator("xpath=..")).to_contain_text("Baixa"))
            confere("pedido some depois de decidido", lambda: expect(pagina.get_by_test_id("aceite")).to_be_hidden())
            pagina.get_by_role("button", name="Menu da conta").click()
            pagina.get_by_role("menuitem", name="Sair").click()
            expect(pagina.get_by_role("button", name="Entrar")).to_be_visible()

            # Pessoa do cliente
            primeira_entrada(pagina, cliente, senha_nova)
            confere("pessoa do cliente abre chamado", lambda: expect(pagina.get_by_role("button", name="Novo chamado")).to_be_visible())
            confere("pessoa do cliente começa sem chamados", lambda: expect(pagina.get_by_text("Nenhum chamado aqui.")).to_be_visible())
            pagina.get_by_role("button", name="Novo chamado").click()
            dialogo = pagina.get_by_role("dialog")
            confere("formulário do cliente não pede cliente nem quem pediu", lambda: (expect(dialogo.get_by_label("Quem pediu")).to_be_hidden(), expect(dialogo.get_by_label("E-mail para contato")).to_have_value(cliente["email"])))
            dialogo.get_by_label("Título").fill("Sem acesso à pasta")
            dialogo.get_by_label("Descrição").fill("Preciso da pasta do financeiro.")
            dialogo.get_by_role("button", name="Abrir chamado").click()
            confere("cliente vê o chamado que abriu", lambda: expect(pagina.get_by_test_id("titulo-chamado")).to_contain_text("Sem acesso à pasta"))
            confere("cliente não vê ações de atendimento", lambda: (expect(pagina.get_by_role("button", name="Assumir atendimento")).to_be_hidden(), expect(pagina.get_by_role("button", name="Mais ações")).to_be_hidden()))
            pagina.get_by_role("button", name="Escrever").click()
            dialogo = pagina.get_by_role("dialog")
            dialogo.get_by_label("Mensagem").fill("É urgente para o fechamento.")
            dialogo.get_by_role("button", name="Enviar").click()
            expect(dialogo).to_be_hidden()
            pagina.get_by_role("tab", name="Histórico").click()
            confere("comentário do cliente aparece com o nome", lambda: (expect(detalhe).to_contain_text("É urgente para o fechamento."), expect(detalhe.get_by_text(re.compile(r", Cleo Cliente$")).first).to_be_visible()))
            confere("cliente só vê o próprio chamado", lambda: expect(itens).to_have_count(1))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "h-cliente.png")

            confere("sem erro no console nem violação de CSP", lambda: _verdadeiro(not erros) if not erros else (_ for _ in ()).throw(AssertionError(erros[0])))
            nav.close()
    finally:
        pessoas.desativar(conta["email"])
        pessoas.desativar(cliente["email"])
        pessoas.desativar(gestor["email"])

    print(f"\n{len(falhas)} falha(s)" if falhas else "\ntudo certo")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    principal()
