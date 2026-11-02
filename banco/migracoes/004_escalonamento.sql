-- Grupos de escalonamento de cada instância. O nível do chamado vem da equipe dele no motor.
ALTER TABLE clientes ADD COLUMN grupos jsonb NOT NULL DEFAULT '{}';

-- O motor marca a hora de tomada de conhecimento ao atribuir grupo, mesmo no escalonamento automático.
-- Para o SLA, a resposta é uma pessoa assumir.
ALTER TABLE chamados ADD COLUMN primeira_resposta_em timestamptz;
