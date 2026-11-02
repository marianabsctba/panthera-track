# Panthera Track

Ferramenta de chamados com SLA em horas corridas, isolamento real entre clientes e integração com SIEM. Usa o [GLPI](https://github.com/glpi-project/glpi) sem modificação como motor, com API e tela próprias por cima.

Este projeto não é afiliado ao GLPI nem à Teclib. GLPI é marca de seus titulares.

## Estado

Prontos: abertura pelo SIEM, pelo portal e por telefone; fila única do analista; login com segundo fator; pessoas do cliente com autor real no histórico; código único, tipo, categoria, contexto do incidente, contato e anexos; prazos de primeira resposta e de resolução, com tempo por situação e motivo das pausas; status devolvido ao SIEM por webhook. O que vem depois está em [docs/arquitetura.md](docs/arquitetura.md).

## Documentos

- [Arquitetura e decisões](docs/arquitetura.md)
- [Contrato de integração com o SIEM](docs/contrato-siem.md)

## Testar

Requer Linux com Docker e Python 3.12, rodando como root ou com `sudo`. O teste sobe duas instâncias de cliente de verdade e as remove no fim.

```bash
sudo sh testes/preparar.sh
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_fatia.py'
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/testar_pessoas.py'
```

## Ver a tela

A demonstração cria dois clientes de exemplo, chamados e pessoas, e abre a tela em http://127.0.0.1:8720. As senhas provisórias ficam em `/opt/panthera-track/demonstracao.json`, com permissão 600. O cookie sem `Secure` da demonstração só serve para endereço local.

```bash
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python testes/demonstracao.py'
```

Com a demonstração no ar, `testes/testar_tela.py` percorre a tela num navegador de verdade. Ele precisa do Playwright e cria chamados de teste nela.

## Criar um cliente

As credenciais vão só para o arquivo indicado, com permissão 600.

```bash
cd fachada
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python -m track_api.provisao criar --nome acme --prazos "{\"critica\":4,\"alta\":8,\"media\":24,\"baixa\":48,\"consulta\":72}" --saida /root/acme.json'
```

## Criar uma pessoa

A pessoa recebe uma senha provisória e, na primeira entrada, troca a senha e cadastra o aplicativo autenticador.

```bash
cd fachada
sudo sh -c 'set -a; . /opt/panthera-track/.env; set +a; /opt/panthera-track/venv/bin/python -m track_api.pessoas criar --nome "Ana" --email ana@exemplo.org --tipo analista --clientes acme --saida /root/ana.json'
```

## Licença

[AGPL-3.0](LICENSE) para a fachada. O código que roda dentro do motor, como `infra/glpi/criar_cliente_oauth.php`, é GPL-3.0-or-later, a licença do GLPI. O GLPI não é redistribuído aqui: a provisão usa a imagem oficial.
