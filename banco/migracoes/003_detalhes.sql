-- Cada instância conta do 1, então o número só é único dentro do cliente. O código é o que as pessoas leem.
ALTER TABLE clientes
    ADD COLUMN sigla text UNIQUE CHECK (sigla ~ '^[A-Z0-9]{2,8}$'),
    ADD COLUMN categorias jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN prazos_resposta jsonb NOT NULL DEFAULT '{}',
    -- A entrega ao SIEM passou para o webhook nativo do motor.
    DROP COLUMN webhook_url,
    DROP COLUMN webhook_segredo;
UPDATE clientes SET sigla = upper(left(md5(id::text), 6));
ALTER TABLE clientes ALTER COLUMN sigla SET NOT NULL;

-- Só o que o motor não guarda por chamado: contexto do SIEM e contato de quem pediu.
ALTER TABLE chamados
    ADD COLUMN codigo text,
    ADD COLUMN contexto jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN contato_telefone text,
    ADD COLUMN contato_email text,
    -- A API do motor informa o SLA e a hora da resposta, mas não o prazo calculado.
    ADD COLUMN prazo_resposta_min integer;
ALTER TABLE chamados DISABLE ROW LEVEL SECURITY;
UPDATE chamados ch SET codigo = c.sigla || '-' || lpad(ch.numero::text, 4, '0') FROM clientes c WHERE c.id = ch.cliente_id;
ALTER TABLE chamados ENABLE ROW LEVEL SECURITY;
ALTER TABLE chamados ALTER COLUMN codigo SET NOT NULL;

ALTER TABLE eventos
    ADD COLUMN detalhe text,
    DROP COLUMN entregue_em,
    DROP COLUMN tentativas,
    DROP COLUMN proxima_tentativa;
