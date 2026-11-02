<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Cria o cliente OAuth da fachada e devolve identificador e segredo em JSON.
// O GLPI só oferece isso pela tela, e o segredo precisa ser cifrado com a chave dele.
require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel();
$kernel->boot();

$cliente = new OAuthClient();
$id = $cliente->add([
    'name'            => 'fachada',
    'redirect_uri'    => [],
    'grants'          => ['password'],
    'scopes'          => ['api'],
    'is_active'       => 1,
    'is_confidential' => 1,
]);
if (!$id) {
    fwrite(STDERR, "não foi possível criar o cliente OAuth\n");
    exit(1);
}
$cliente->getFromDB($id);
echo json_encode([
    'id'     => $cliente->fields['identifier'],
    'segredo' => (new GLPIKey())->decrypt($cliente->fields['secret']),
]), "\n";
