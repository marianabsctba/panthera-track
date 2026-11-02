ALTER TABLE clientes ADD COLUMN identificador text UNIQUE;

CREATE TABLE pessoas (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email            text NOT NULL UNIQUE CHECK (email = lower(email)),
    nome             text NOT NULL,
    tipo             text NOT NULL CHECK (tipo IN ('analista', 'cliente')),
    cliente_id       uuid REFERENCES clientes(id),
    papel            text CHECK (papel IN ('solicitante', 'gestor')),
    senha_hash       text NOT NULL,
    trocar_senha     boolean NOT NULL DEFAULT true,
    falhas           integer NOT NULL DEFAULT 0,
    bloqueado_ate    timestamptz,
    mfa_segredo      bytea,
    mfa_ativo        boolean NOT NULL DEFAULT false,
    mfa_ultimo_passo bigint,
    ativo            boolean NOT NULL DEFAULT true,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    CHECK ((tipo = 'cliente' AND cliente_id IS NOT NULL AND papel IS NOT NULL)
        OR (tipo = 'analista' AND cliente_id IS NULL AND papel IS NULL))
);

CREATE TABLE codigos_recuperacao (
    pessoa_id   uuid NOT NULL REFERENCES pessoas(id) ON DELETE CASCADE,
    codigo_hash text NOT NULL,
    usado_em    timestamptz,
    PRIMARY KEY (pessoa_id, codigo_hash)
);

-- Clientes que cada analista atende. Pessoa do cliente só alcança o próprio.
CREATE TABLE vinculos (
    pessoa_id  uuid NOT NULL REFERENCES pessoas(id) ON DELETE CASCADE,
    cliente_id uuid NOT NULL REFERENCES clientes(id),
    PRIMARY KEY (pessoa_id, cliente_id)
);

-- Usuário de cada pessoa na instância do motor, para o histórico nativo registrar o autor real.
CREATE TABLE sombras (
    pessoa_id     uuid NOT NULL REFERENCES pessoas(id) ON DELETE CASCADE,
    cliente_id    uuid NOT NULL REFERENCES clientes(id),
    motor_usuario text NOT NULL,
    motor_senha   text NOT NULL,
    PRIMARY KEY (pessoa_id, cliente_id),
    UNIQUE (cliente_id, motor_usuario)
);

CREATE TABLE sessoes (
    token_hash text PRIMARY KEY,
    pessoa_id  uuid NOT NULL REFERENCES pessoas(id) ON DELETE CASCADE,
    mfa_ok     boolean NOT NULL DEFAULT false,
    expira_em  timestamptz NOT NULL,
    criado_em  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE chamados
    ADD COLUMN pessoa_solicitante uuid REFERENCES pessoas(id),
    ADD COLUMN pessoa_atendente uuid REFERENCES pessoas(id);
ALTER TABLE eventos ADD COLUMN pessoa_id uuid REFERENCES pessoas(id);

-- Criar pessoa e vínculo é só da administração, que usa o papel dono.
GRANT SELECT ON pessoas, vinculos, sombras TO track_api;
GRANT UPDATE (senha_hash, trocar_senha, falhas, bloqueado_ate, mfa_segredo, mfa_ativo, mfa_ultimo_passo) ON pessoas TO track_api;
GRANT SELECT, INSERT, DELETE ON codigos_recuperacao TO track_api;
GRANT UPDATE (usado_em) ON codigos_recuperacao TO track_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON sessoes TO track_api;
