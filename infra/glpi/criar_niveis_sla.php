<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Cria os níveis de escalonamento de um SLA. A API do GLPI cria o nível, mas não as ações dele.
// Recebe em JSON: {"sla": id, "niveis": [{"nome": "...", "segundos": 0, "grupo": id}]}
require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel();
$kernel->boot();

$dados = json_decode($argv[1], true);
foreach ($dados['niveis'] as $nivel) {
    $id = (new SlaLevel())->add([
        'name'           => $nivel['nome'],
        'slas_id'        => $dados['sla'],
        'execution_time' => $nivel['segundos'],
        'is_active'      => 1,
        'is_recursive'   => 1,
        'entities_id'    => 0,
        'match'          => 'AND',
    ]);
    if (!$id) {
        fwrite(STDERR, "não foi possível criar o nível de escalonamento\n");
        exit(1);
    }
    $acao = (new SlaLevelAction())->add([
        'slalevels_id' => $id,
        'action_type'  => 'assign',
        'field'        => '_groups_id_assign',
        'value'        => $nivel['grupo'],
    ]);
    if (!$acao) {
        fwrite(STDERR, "não foi possível criar a ação do nível\n");
        exit(1);
    }
}
echo "ok\n";
