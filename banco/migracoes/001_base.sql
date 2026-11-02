-- O papel da API não é dono de nada: o isolamento vale mesmo com bug na aplicação.
CREATE TABLE clientes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nome                text NOT NULL UNIQUE,
    motor_url           text NOT NULL,
    motor_oauth_id      text NOT NULL,
    motor_oauth_segredo text NOT NULL,
    motor_usuario       text NOT NULL,
    motor_senha         text NOT NULL,
    slas                jsonb NOT NULL,
    webhook_url         text,
    webhook_segredo     text,
    criado_em           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE credenciais (
    chave       text PRIMARY KEY,
    cliente_id  uuid NOT NULL REFERENCES clientes(id),
    segredo     text NOT NULL,
    papel       text NOT NULL CHECK (papel IN ('siem', 'atendente')),
    revogada_em timestamptz,
    criado_em   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chamados (
    cliente_id   uuid NOT NULL REFERENCES clientes(id),
    numero       bigint NOT NULL,
    id_externo   text,
    origem       text NOT NULL,
    criticidade  text NOT NULL CHECK (criticidade IN ('critica', 'alta', 'media', 'baixa', 'consulta')),
    solicitante  text NOT NULL,
    atendente    text,
    ultima_seq   integer NOT NULL DEFAULT 0,
    criado_em    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cliente_id, numero),
    UNIQUE (cliente_id, id_externo)
);

CREATE TABLE eventos (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cliente_id         uuid NOT NULL,
    numero             bigint NOT NULL,
    sequencia          integer NOT NULL,
    tipo               text NOT NULL,
    autor              text NOT NULL,
    dados              jsonb NOT NULL,
    criado_em          timestamptz NOT NULL DEFAULT now(),
    entregue_em        timestamptz,
    tentativas         integer NOT NULL DEFAULT 0,
    proxima_tentativa  timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (cliente_id, numero) REFERENCES chamados(cliente_id, numero),
    UNIQUE (cliente_id, numero, sequencia)
);
CREATE INDEX eventos_pendentes ON eventos (cliente_id, proxima_tentativa) WHERE entregue_em IS NULL;

ALTER TABLE chamados ENABLE ROW LEVEL SECURITY;
ALTER TABLE chamados FORCE ROW LEVEL SECURITY;
ALTER TABLE eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE eventos FORCE ROW LEVEL SECURITY;

CREATE POLICY isolamento ON chamados TO track_api
    USING (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid)
    WITH CHECK (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid);
CREATE POLICY isolamento ON eventos TO track_api
    USING (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid)
    WITH CHECK (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid);

GRANT SELECT ON clientes, credenciais TO track_api;
GRANT SELECT, INSERT, UPDATE ON chamados TO track_api;
-- O histórico só cresce: a API marca a entrega, mas não troca o conteúdo nem apaga.
GRANT SELECT, INSERT ON eventos TO track_api;
GRANT UPDATE (entregue_em, tentativas, proxima_tentativa) ON eventos TO track_api;
