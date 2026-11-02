<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Dá nome e e-mail ao usuário-sombra de uma pessoa, para o motor avisar e reconhecer quem escreve.
// A API só permite isso ao próprio usuário, com outro escopo. Recebe em JSON: {"usuario": "...", "nome": "...", "email": "..."}, com e-mail opcional
require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel();
$kernel->boot();

$dados = json_decode($argv[1], true);
$usuario = new User();
if (!$usuario->getFromDBbyName($dados['usuario'])) {
    fwrite(STDERR, "usuário não encontrado\n");
    exit(1);
}
// Sem sobrenome, o motor mostra o login no lugar do nome.
$usuario->update(['id' => $usuario->getID(), 'realname' => $dados['nome'], 'firstname' => '']);
if (!empty($dados['email'])
    && !(new UserEmail())->add(['users_id' => $usuario->getID(), 'email' => $dados['email'], 'is_default' => 1])) {
    fwrite(STDERR, "não foi possível gravar o e-mail\n");
    exit(1);
}
echo json_encode(['id' => $usuario->getID()]), "\n";
