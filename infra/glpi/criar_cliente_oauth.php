<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Cria os acessos da fachada e devolve identificador e segredo do OAuth em JSON.
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

// A API nova não recebe arquivo; o envio de anexo usa a antiga, aberta só para a rede interna do Docker.
$api = (new APIClient())->add([
    'name'             => 'fachada',
    'is_active'        => 1,
    'ipv4_range_start' => '172.16.0.0',
    'ipv4_range_end'   => '172.31.255.255',
    'dolog_method'     => 0,
]);
if (!$api) {
    fwrite(STDERR, "não foi possível liberar a API de anexos\n");
    exit(1);
}

echo json_encode([
    'id'      => $cliente->fields['identifier'],
    'segredo' => (new GLPIKey())->decrypt($cliente->fields['secret']),
]), "\n";
