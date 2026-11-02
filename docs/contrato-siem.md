# Contrato com o SIEM

O cliente vem sempre da credencial, nunca do corpo. A provisão entrega uma credencial de papel `siem` e o segredo do webhook.

## Assinatura dos pedidos

| Cabeçalho | Valor |
|---|---|
| `X-Track-Chave` | Identificador da credencial |
| `X-Track-Data` | Hora atual em segundos Unix, com tolerância de 5 minutos |
| `X-Track-Assinatura` | HMAC-SHA256 em hexadecimal, com o segredo da credencial |

A mensagem assinada é `{data}.{MÉTODO}.{caminho com query}.{sha256 do corpo em hexadecimal}`.

## Abrir chamado

`POST /api/v1/siem/chamados`

```json
{
  "id_externo": "incidente-123",
  "titulo": "Força bruta em servidor",
  "descricao": "Texto do incidente",
  "criticidade": "critica",
  "solicitante": "SIEM",
  "tipo": "incidente",
  "categoria": "acesso",
  "contexto": {
    "ativo": "SRV-WEB-01",
    "ip_origem": "203.0.113.45",
    "ip_destino": "10.0.1.10",
    "usuario": "administrador",
    "regra": "Múltiplas falhas seguidas de sucesso",
    "tecnica_mitre": "T1110.001",
    "url_alerta": "https://roar.exemplo.org/alertas/1042"
  }
}
```

`tipo`, `categoria`, `contexto` e cada campo dentro de `contexto` são opcionais. Campo desconhecido em `contexto` é recusado com `422`.

- `id_externo` é a chave de idempotência, com até 128 caracteres entre letras, números e `. _ : -`.
- `criticidade` é `critica`, `alta`, `media`, `baixa` ou `consulta`.
- `tipo` é `incidente` (padrão) ou `requisicao`.
- `categoria` é `acesso`, `malware`, `rede`, `vazamento`, `vulnerabilidade`, `disponibilidade`, `duvida` ou `outro` (padrão). A resposta traz o nome da categoria, como `Acesso`.
- `url_alerta` precisa ser `http` ou `https`, e `tecnica_mitre` segue o formato `T1110` ou `T1110.001`.
- Devolve `201` ao criar e `200` com o mesmo chamado em reenvio.

## Consultar

`GET /api/v1/siem/chamados/{id_externo}` traz o estado completo. Use depois de cada webhook, ou para reconciliar se um se perder.

```json
{
  "numero": 1,
  "codigo": "ALFA-0001",
  "id_externo": "incidente-123",
  "origem": "siem",
  "tipo": "incidente",
  "categoria": "Acesso",
  "contexto": { "ativo": "SRV-WEB-01" },
  "titulo": "Força bruta em servidor",
  "descricao": "<p>Texto do incidente</p>",
  "criticidade": "critica",
  "status": "em_andamento",
  "nivel": "N1",
  "solicitante": "SIEM",
  "atendente": "Nome",
  "aberto_em": "2026-11-02T12:27:33+00:00",
  "resolvido_em": null,
  "encerrado_em": null,
  "sla": {
    "prazo": "2026-11-02T16:27:33+00:00",
    "segundos_corridos": 120,
    "segundos_pausados": 0,
    "segundos_restantes": 14280,
    "percentual": 0.8,
    "pausado": false,
    "estourado": false,
    "em_risco": false,
    "resposta": {
      "prazo": "2026-11-02T12:42:33+00:00",
      "em": null,
      "segundos_restantes": 780,
      "cumprida": null,
      "estourada": false
    }
  }
}
```

`status` é `aberto`, `em_andamento`, `aguardando_cliente`, `resolvido` ou `encerrado`.

## Webhook de volta

É o webhook nativo do motor. A cada alteração de um chamado, inclusive as feitas fora da fachada, ele faz `POST` na URL configurada para o cliente:

```json
{
  "numero": 1,
  "id_externo": "incidente-123",
  "status": 4,
  "atualizado_em": "2026-11-02T12:30:00+00:00"
}
```

| `status` | Situação |
|---|---|
| 1 | aberto |
| 2 ou 3 | em_andamento |
| 4 | aguardando_cliente |
| 5 | resolvido |
| 6 | encerrado |

- `X-GLPI-timestamp` traz a hora em segundos Unix, e `X-GLPI-signature` traz o HMAC-SHA256 em hexadecimal de `{corpo bruto}{hora}`, com o segredo do webhook. Confira a assinatura e recuse hora muito antiga.
- O envio sai por uma fila que roda a cada minuto, com até 5 tentativas. Espere até cerca de um minuto de atraso.
- A ordem não é garantida e pode haver repetição. Trate como um aviso de que algo mudou: leia o estado completo na consulta.
- Chamados que não vieram do SIEM chegam com `id_externo` vazio. Descarte esses.
- O motor recusa destino em rede privada. Se o SIEM estiver na mesma rede, crie o cliente com `--webhook-interno`.
- Só vão número, status e data. Motivo de pausa, anexos e contato não vão ao SIEM.
