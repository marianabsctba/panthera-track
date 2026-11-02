"""Percorre a tela num navegador de verdade, da primeira entrada ao encerramento. Precisa da demonstração no ar.

Usa uma conta descartável e não imprime senha nem segredo. A captura só acontece depois da entrada.
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


def principal():
    p = argparse.ArgumentParser()
    p.add_argument("--capturas", required=True)
    capturas = Path(p.parse_args().capturas)
    capturas.mkdir(parents=True, exist_ok=True)

    email = f"verif-{uuid.uuid4().hex[:8]}@exemplo.org"
    conta = pessoas.criar("Verificador", email, "analista", ["Cliente Alfa", "Cliente Beta"])
    senha_nova = "senha-" + uuid.uuid4().hex
    erros = []
    try:
        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            pagina = nav.new_page(viewport={"width": 1280, "height": 800})
            pagina.on("pageerror", lambda e: erros.append(str(e)))
            pagina.on("console", lambda m: erros.append(m.text) if m.type == "error" and "401" not in m.text else None)
            pagina.goto(TELA)

            confere("mostra a entrada com o símbolo", lambda: expect(pagina.locator("#tela-entrada img").first).to_be_visible())
            f = pagina.locator("#f-entrar")
            f.locator("[name=email]").fill(email)
            f.locator("[name=senha]").fill("errada")
            f.locator("button").click()
            confere("senha errada mostra aviso", lambda: expect(pagina.locator("#erro-entrada")).to_contain_text("incorretos"))
            f.locator("[name=senha]").fill(conta["senha_provisoria"])
            f.locator("button").click()

            f = pagina.locator("#f-senha")
            confere("pede a troca de senha", lambda: expect(f).to_be_visible())
            f.locator("[name=senha_atual]").fill(conta["senha_provisoria"])
            f.locator("[name=senha_nova]").fill(senha_nova)
            f.locator("button").click()

            f = pagina.locator("#f-mfa-cadastro")
            confere("mostra o QR", lambda: expect(pagina.locator("#qr")).to_have_attribute("src", re.compile("^data:image/svg")))
            expect(pagina.locator("#segredo")).not_to_be_empty()
            pagina.wait_for_timeout(300)
            confere("o QR carrega apesar da CSP", lambda: _verdadeiro(pagina.evaluate("document.getElementById('qr').naturalWidth > 0")))
            segredo = pagina.locator("#segredo").inner_text()
            f.locator("[name=codigo]").fill(pyotp.TOTP(segredo).now())
            f.locator("button").click()
            confere("entrega os códigos de recuperação", lambda: expect(pagina.locator("#recuperacao")).to_be_visible())
            pagina.locator("#guardei").click()

            linhas = pagina.locator("#linhas tr")
            confere("mostra a fila", lambda: expect(linhas.first).to_be_visible())
            confere("fila traz os dois clientes", lambda: (expect(pagina.locator("#linhas")).to_contain_text("Cliente Alfa"), expect(pagina.locator("#linhas")).to_contain_text("Cliente Beta")))
            confere("cabeçalho traz a coluna de cliente", lambda: expect(pagina.locator("#cabecalho")).to_contain_text("Cliente"))
            confere("código não repete na fila", lambda: _verdadeiro(len(set(c := pagina.locator("#linhas td.codigo").all_inner_texts())) == len(c) > 0))
            linhas.filter(has_text="Força bruta").click()
            confere("mostra o contexto do incidente", lambda: (expect(pagina.locator("#detalhe")).to_contain_text("203.0.113.45"), expect(pagina.locator("#detalhe")).to_contain_text("T1110.001")))
            confere("técnica leva ao site da MITRE", lambda: expect(pagina.get_by_role("link", name="T1110.001")).to_have_attribute("href", "https://attack.mitre.org/techniques/T1110/001/"))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "0-contexto.png")
            confere("não revela a ferramenta", lambda: expect(pagina.locator("body")).not_to_contain_text("GLPI"))
            pagina.wait_for_timeout(500)
            pagina.screenshot(path=capturas / "1-fila.png")

            pagina.locator("#abrir").click()
            n = pagina.locator("#novo")
            n.locator("[name=solicitante]").fill("João, por telefone")
            n.locator("[name=titulo]").fill("Ligação sobre acesso indevido")
            n.locator("[name=descricao]").fill("Cliente ligou relatando acesso estranho.")
            n.locator("[name=criticidade]").select_option("critica")
            n.locator("[name=categoria]").select_option("acesso")
            n.locator("[name=ativo]").fill("SRV-WEB-01")
            n.locator("[name=contato_telefone]").fill("+55 41 99999-0000")
            pagina.wait_for_timeout(200)
            pagina.screenshot(path=capturas / "2-telefone.png")
            n.locator("button.forte").click()

            detalhe = pagina.locator("#detalhe")
            confere("abre o chamado registrado", lambda: expect(detalhe).to_contain_text("Ligação sobre acesso indevido"))
            confere("mostra a origem telefone", lambda: expect(detalhe).to_contain_text("Telefone"))
            confere("mostra o código com a sigla", lambda: expect(detalhe.locator("h2")).to_contain_text(re.compile(r"ALFA-\d{4}")))
            confere("mostra classificação, ativo e contato", lambda: (expect(detalhe).to_contain_text("Acesso"), expect(detalhe).to_contain_text("SRV-WEB-01"), expect(detalhe).to_contain_text("99999-0000")))
            confere("mostra os dois prazos", lambda: (expect(detalhe).to_contain_text("Primeira resposta"), expect(detalhe).to_contain_text("Resolução")))
            pagina.locator("#arquivo").set_input_files({"name": "evidencia.txt", "mimeType": "text/plain", "buffer": b"prova"})
            pagina.get_by_role("button", name="Anexar").click()
            confere("anexa pela tela", lambda: expect(detalhe.get_by_role("link", name="evidencia.txt")).to_be_visible())
            confere("histórico guarda o SHA-256 do anexo", lambda: expect(detalhe).to_contain_text("SHA-256"))
            pagina.locator("#nova-categoria").select_option("malware")
            pagina.get_by_role("button", name="Salvar classificação").click()
            confere("reclassifica pela tela", lambda: expect(detalhe).to_contain_text("Malware"))
            pagina.locator("#novo-nivel").select_option("N2")
            pagina.locator("#motivo-escala").fill("Precisa de análise forense")
            pagina.get_by_role("button", name="Escalar").click()
            confere("escala pela tela", lambda: expect(detalhe.locator(".dados").first).to_contain_text("N2"))
            confere("motivo do escalonamento aparece", lambda: expect(detalhe.get_by_text("N2: Precisa de análise forense", exact=True)).to_be_visible())
            confere("fila mostra o nível", lambda: expect(pagina.locator("#linhas .nivel").first).to_have_text("N2"))
            pagina.get_by_role("button", name="Assumir atendimento").click()
            confere("assume em nome próprio", lambda: expect(detalhe.locator(".dados").first).to_contain_text("Verificador"))
            pagina.locator("#texto").fill("Cliente precisa confirmar o horário do acesso.")
            pagina.get_by_role("button", name="Aguardar cliente").click()
            confere("pausa o SLA", lambda: expect(detalhe).to_contain_text("Aguardando cliente"))
            confere("mostra o motivo da pausa", lambda: expect(detalhe.get_by_text("Cliente precisa confirmar o horário do acesso.").first).to_be_visible())
            confere("a fila mostra o SLA pausado", lambda: expect(pagina.locator("#linhas")).to_contain_text("Pausado"))
            pagina.get_by_role("button", name="Retomar").click()
            expect(pagina.get_by_role("button", name="Resolver")).to_be_visible()
            pagina.get_by_role("button", name="Resolver").click()
            confere("resolver sem texto mostra aviso", lambda: expect(pagina.locator("#erro")).not_to_be_empty())
            pagina.locator("#texto").fill("Acesso legítimo confirmado com o cliente.")
            pagina.get_by_role("button", name="Resolver").click()
            expect(pagina.get_by_role("button", name="Encerrar")).to_be_visible()
            pagina.get_by_role("button", name="Encerrar").click()
            confere("encerra", lambda: expect(detalhe.locator(".s-encerrado")).to_be_visible())
            confere("histórico traz o autor real", lambda: expect(detalhe).to_contain_text("Verificador"))
            confere("registro traz a solução", lambda: expect(detalhe).to_contain_text("Acesso legítimo confirmado"))
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=capturas / "3-encerrado.png", full_page=True)

            pagina.set_viewport_size({"width": 390, "height": 800})
            pagina.wait_for_timeout(300)
            confere("cabe no celular sem rolagem lateral", lambda: _verdadeiro(pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")))
            pagina.screenshot(path=capturas / "4-celular.png")
            pagina.set_viewport_size({"width": 1280, "height": 800})

            pagina.locator("#sair").click()
            confere("sair volta para a entrada", lambda: expect(pagina.locator("#f-entrar")).to_be_visible())
            pagina.reload()
            confere("depois de sair, recarregar não entra", lambda: expect(pagina.locator("#f-entrar")).to_be_visible())
            confere("sem erro no console", lambda: _sem_erros(erros))
            nav.close()
    finally:
        pessoas.desativar(email)

    print(f"\n{len(falhas)} falha(s)" if falhas else "\ntudo certo")
    sys.exit(1 if falhas else 0)


def _verdadeiro(valor):
    if not valor:
        raise AssertionError("condição falsa")


def _sem_erros(erros):
    # A tentativa de senha errada e o resolver sem texto respondem com erro de propósito.
    reais = [e for e in erros if "Failed to load resource" not in e]
    if reais:
        raise AssertionError(reais[0])


if __name__ == "__main__":
    principal()
