from datetime import datetime, timezone

# Mesmos níveis de urgência, impacto e prioridade, para a matriz do motor não recalcular outro valor.
CRITICIDADES = {"critica": 5, "alta": 4, "media": 3, "baixa": 2, "consulta": 1}

ABERTO, EM_ANDAMENTO, AGUARDANDO_CLIENTE, RESOLVIDO, ENCERRADO = 1, 2, 4, 5, 6
STATUS = {
    ABERTO: "aberto",
    EM_ANDAMENTO: "em_andamento",
    3: "em_andamento",
    AGUARDANDO_CLIENTE: "aguardando_cliente",
    RESOLVIDO: "resolvido",
    ENCERRADO: "encerrado",
}


# Aviso antes de estourar: resta um quarto do prazo.
_FRACAO_DE_RISCO = 0.25


def _data(texto):
    return datetime.fromisoformat(texto) if texto else None


def _iso(data):
    return data.astimezone(timezone.utc).isoformat(timespec="seconds") if data else None


def calcular_sla(chamado, agora=None):
    """Horas corridas descontando só o tempo aguardando o cliente.

    O motor só adia o prazo quando o chamado sai da pausa, então a pausa em curso é somada aqui.
    """
    agora = agora or datetime.now(timezone.utc)
    status = chamado["status"]["id"]
    abertura = _data(chamado["date"])
    prazo = _data(chamado.get("resolution_date"))
    resolvido_em = _data(chamado.get("date_solve"))
    pausa = int(chamado.get("sla_waiting_duration") or 0)

    pausado = status == AGUARDANDO_CLIENTE
    pausa_em_curso = 0
    if pausado and chamado.get("begin_waiting_date"):
        pausa_em_curso = max(0, int((agora - _data(chamado["begin_waiting_date"])).total_seconds()))

    fim = resolvido_em or agora
    corridos = max(0, int((fim - abertura).total_seconds()) - pausa - pausa_em_curso)

    restante = estourado = None
    em_risco = False
    if prazo:
        prazo_efetivo = prazo.timestamp() + pausa_em_curso
        restante = int(prazo_efetivo - fim.timestamp())
        estourado = restante < 0
        total = prazo_efetivo - abertura.timestamp() - pausa - pausa_em_curso
        em_risco = not resolvido_em and not estourado and restante <= total * _FRACAO_DE_RISCO
    return {
        "prazo": _iso(datetime.fromtimestamp(prazo_efetivo, timezone.utc)) if prazo else None,
        "segundos_corridos": corridos,
        "segundos_pausados": pausa + pausa_em_curso,
        "segundos_restantes": restante,
        "pausado": pausado,
        "estourado": estourado,
        "em_risco": em_risco,
    }


def representar(linha, chamado):
    return {
        "numero": linha["numero"],
        "id_externo": linha["id_externo"],
        "origem": linha["origem"],
        "titulo": chamado["name"],
        "descricao": chamado["content"],
        "criticidade": linha["criticidade"],
        "status": STATUS[chamado["status"]["id"]],
        "solicitante": linha["solicitante"],
        "atendente": linha["atendente"],
        "aberto_em": _iso(_data(chamado["date"])),
        "resolvido_em": _iso(_data(chamado.get("date_solve"))),
        "encerrado_em": _iso(_data(chamado.get("date_close"))),
        "sla": calcular_sla(chamado),
    }
