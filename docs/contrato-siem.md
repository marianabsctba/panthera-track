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
  "solicitante": "SIEM"
}
```

- `id_externo` é a chave de idempotência, com até 128 caracteres entre letras, números e `. _ : -`.
- `criticidade` é `critica`, `alta`, `media`, `baixa` ou `consulta`.
- Devolve `201` ao criar e `200` com o mesmo chamado em reenvio.

## Consultar

`GET /api/v1/siem/chamados/{id_externo}` serve para reconciliar, caso um webhook se perca.

```json
{
  "numero": 1,
  "id_externo": "incidente-123",
  "origem": "siem",
  "titulo": "Força bruta em servidor",
  "descricao": "<p>Texto do incidente</p>",
  "criticidade": "critica",
  "status": "em_andamento",
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
    "pausado": false,
    "estourado": false,
    "em_risco": false
  }
}
```

`status` é `aberto`, `em_andamento`, `aguardando_cliente`, `resolvido` ou `encerrado`.

## Webhook de volta

A cada mudança, a fachada faz `POST` na URL configurada para o cliente:

```json
{
  "evento_id": "uuid",
  "sequencia": 2,
  "tipo": "em_andamento",
  "autor": "Nome",
  "em": "2026-11-02T12:30:00+00:00",
  "chamado": { "o mesmo objeto da consulta": "" }
}
```

- `X-Track-Data` traz a hora em segundos Unix, e `X-Track-Assinatura` traz o HMAC-SHA256 em hexadecimal de `{data}.{corpo bruto}`, com o segredo do webhook.
- Qualquer resposta 2xx confirma a entrega. Fora disso, a fachada tenta de novo com espera crescente, até 1 hora entre tentativas.
- A ordem por chamado é garantida: o evento seguinte só sai depois de o anterior ser aceito.
- A entrega é pelo menos uma vez. O receptor deve descartar `evento_id` repetido.

`tipo` é `aberto`, `em_andamento`, `andamento`, `aguardando_cliente`, `resolvido` ou `encerrado`.
