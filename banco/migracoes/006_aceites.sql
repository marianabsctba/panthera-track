-- Mudança de criticidade e encerramento sem resolução só valem com o aceite do cliente.
CREATE TABLE aceites (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cliente_id   uuid NOT NULL,
    numero       bigint NOT NULL,
    tipo         text NOT NULL CHECK (tipo IN ('criticidade', 'encerramento')),
    criticidade  text CHECK (criticidade IN ('critica', 'alta', 'media', 'baixa', 'consulta')),
    motivo       text NOT NULL,
    pedido_por   text NOT NULL,
    pedido_em    timestamptz NOT NULL DEFAULT now(),
    estado       text NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente', 'aceito', 'recusado')),
    decidido_por text,
    pessoa_decisao uuid REFERENCES pessoas(id),
    decidido_em  timestamptz,
    comentario   text,
    FOREIGN KEY (cliente_id, numero) REFERENCES chamados(cliente_id, numero),
    CHECK ((tipo = 'criticidade') = (criticidade IS NOT NULL))
);
-- Um pedido aberto por chamado, mesmo com dois analistas pedindo ao mesmo tempo.
CREATE UNIQUE INDEX um_aceite_pendente ON aceites (cliente_id, numero) WHERE estado = 'pendente';

ALTER TABLE aceites ENABLE ROW LEVEL SECURITY;
ALTER TABLE aceites FORCE ROW LEVEL SECURITY;
CREATE POLICY isolamento ON aceites TO track_api
    USING (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid)
    WITH CHECK (cliente_id = NULLIF(current_setting('track.cliente', true), '')::uuid);
GRANT SELECT, INSERT ON aceites TO track_api;
GRANT UPDATE (estado, decidido_por, pessoa_decisao, decidido_em, comentario) ON aceites TO track_api;
