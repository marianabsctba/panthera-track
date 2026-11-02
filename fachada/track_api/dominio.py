from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator

# Mesmos níveis de urgência, impacto e prioridade, para a matriz do motor não recalcular outro valor.
CRITICIDADES = {"critica": 5, "alta": 4, "media": 3, "baixa": 2, "consulta": 1}
TIPOS = {"incidente": 1, "requisicao": 2}
_TIPO_POR_ID = {v: k for k, v in TIPOS.items()}
# Criadas no motor pela provisão, onde as regras e o e-mail também as enxergam.
CATEGORIAS = {"acesso": "Acesso", "malware": "Malware", "rede": "Rede", "vazamento": "Vazamento de dados",
              "vulnerabilidade": "Vulnerabilidade", "disponibilidade": "Disponibilidade", "duvida": "Dúvida", "outro": "Outro"}
# N1 é o atendimento normal, sem grupo: atribuir grupo na abertura tiraria o chamado de Aberto.
NIVEIS = ("N1", "N2", "N3")
PADRAO_TIPO = "^(incidente|requisicao)$"
PADRAO_CATEGORIA = "^(" + "|".join(CATEGORIAS) + ")$"
PADRAO_CRITICIDADE = "^(critica|alta|media|baixa|consulta)$"

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


class Contexto(BaseModel):
    """O que o SIEM sabe do incidente. Campo desconhecido é recusado, para não virar depósito de dados pessoais."""
    model_config = ConfigDict(extra="forbid")
    ativo: Optional[str] = Field(default=None, max_length=255)
    ip_origem: Optional[IPvAnyAddress] = None
    ip_destino: Optional[IPvAnyAddress] = None
    usuario: Optional[str] = Field(default=None, max_length=255)
    regra: Optional[str] = Field(default=None, max_length=255)
    tecnica_mitre: Optional[str] = Field(default=None, pattern=r"^T\d{4}(\.\d{3})?$")
    url_alerta: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("url_alerta")
    @classmethod
    def _so_web(cls, valor):
        if valor is not None and not valor.startswith(("https://", "http://")):
            raise ValueError("endereço precisa ser http ou https")
        return valor


def _data(texto):
    return datetime.fromisoformat(texto) if texto else None


def _iso(data):
    return data.astimezone(timezone.utc).isoformat(timespec="seconds") if data else None


def _resposta(abertura, minutos, respondido_em, agora):
    if minutos is None:
        return None
    prazo = abertura + timedelta(minutes=minutos)
    return {
        "prazo": _iso(prazo),
        "em": _iso(respondido_em),
        "segundos_restantes": int((prazo - (respondido_em or agora)).total_seconds()),
        "cumprida": respondido_em <= prazo if respondido_em else None,
        "estourada": (respondido_em or agora) > prazo,
    }


def calcular_sla(chamado, agora=None, prazo_resposta_min=None, primeira_resposta_em=None):
    """Horas corridas descontando só o tempo aguardando o cliente.

    Prazo de resolução e pausa vêm do motor. Aqui se soma a pausa em curso, porque o motor adia o prazo apenas
    quando o chamado sai dela, e se conta a primeira resposta: a API não informa esse prazo, e a hora que o motor
    marca também muda ao atribuir grupo, o que faria um escalonamento automático parecer resposta.
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

    restante = estourado = percentual = None
    em_risco = False
    if prazo:
        prazo_efetivo = prazo.timestamp() + pausa_em_curso
        restante = int(prazo_efetivo - fim.timestamp())
        estourado = restante < 0
        total = prazo_efetivo - abertura.timestamp() - pausa - pausa_em_curso
        em_risco = not resolvido_em and not estourado and restante <= total * _FRACAO_DE_RISCO
        percentual = round(corridos * 100 / total, 1) if total > 0 else None
    return {
        "prazo": _iso(datetime.fromtimestamp(prazo_efetivo, timezone.utc)) if prazo else None,
        "segundos_corridos": corridos,
        "segundos_pausados": pausa + pausa_em_curso,
        "segundos_restantes": restante,
        "percentual": percentual,
        "pausado": pausado,
        "estourado": estourado,
        "em_risco": em_risco,
        "resposta": _resposta(abertura, prazo_resposta_min, primeira_resposta_em, agora),
    }


def nivel_do_chamado(chamado):
    grupos = {(m.get("group") or {}).get("name") for m in chamado.get("team") or [] if m.get("role") == "assigned"}
    return next((n for n in reversed(NIVEIS) if n in grupos), NIVEIS[0])


def representar(linha, chamado):
    """Não leva telefone nem e-mail de contato: isto fica guardado nos eventos."""
    return {
        "numero": linha["numero"],
        "codigo": linha["codigo"],
        "id_externo": linha["id_externo"],
        "origem": linha["origem"],
        "tipo": _TIPO_POR_ID.get(chamado.get("type"), "incidente"),
        "categoria": (chamado.get("category") or {}).get("name"),
        "titulo": chamado["name"],
        "descricao": chamado["content"],
        "criticidade": linha["criticidade"],
        "status": STATUS[chamado["status"]["id"]],
        "nivel": nivel_do_chamado(chamado),
        "solicitante": linha["solicitante"],
        "atendente": linha["atendente"],
        "contexto": linha["contexto"],
        "aberto_em": _iso(_data(chamado["date"])),
        "resolvido_em": _iso(_data(chamado.get("date_solve"))),
        "encerrado_em": _iso(_data(chamado.get("date_close"))),
        "sla": calcular_sla(chamado, prazo_resposta_min=linha["prazo_resposta_min"], primeira_resposta_em=linha["primeira_resposta_em"]),
    }


def tempos_do_chamado(linha, chamado):
    """As durações que o motor já mede, e o tempo até uma pessoa assumir."""
    assumido = linha["primeira_resposta_em"]
    return {
        "ate_assumir": int((assumido - _data(chamado["date"])).total_seconds()) if assumido else None,
        "aguardando_cliente": int(chamado.get("waiting_duration") or 0),
        "ate_resolver": int(chamado.get("resolution_duration") or 0),
        "ate_encerrar": int(chamado.get("close_duration") or 0),
    }


def pausas_dos_eventos(eventos, agora=None):
    """O motor guarda a soma das pausas; cada uma, com o motivo e quem pausou, vem dos eventos."""
    agora = agora or datetime.now(timezone.utc)
    pausas = []
    mudancas = [e for e in eventos if e["tipo"] in ("aberto", "em_andamento", "aguardando_cliente", "resolvido", "encerrado")]
    for i, e in enumerate(mudancas):
        if e["tipo"] == "aguardando_cliente":
            fim = mudancas[i + 1]["criado_em"] if i + 1 < len(mudancas) else agora
            pausas.append({"motivo": e["detalhe"], "autor": e["autor"], "em": _iso(e["criado_em"]),
                           "segundos": int((fim - e["criado_em"]).total_seconds()), "em_curso": i + 1 == len(mudancas)})
    return pausas
