<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Liga o envio de e-mail, as tarefas e o modelo dos avisos. A API não alcança a configuração global nem o modelo.
// Recebe em JSON: {"smtp_host", "smtp_port", "smtp_usuario", "smtp_senha", "smtp_tls", "remetente", "equipe", "tela"}
require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel();
$kernel->boot();

$dados = json_decode($argv[1], true);
Config::setConfigurationValues('core', [
    'use_notifications'      => 1,
    'notifications_mailing'  => 1,
    'smtp_mode'              => empty($dados['smtp_tls']) ? MAIL_SMTP : MAIL_SMTPTLS,
    'smtp_host'              => $dados['smtp_host'],
    'smtp_port'              => $dados['smtp_port'],
    'smtp_username'          => $dados['smtp_usuario'] ?? '',
    'smtp_passwd'            => empty($dados['smtp_senha']) ? '' : (new GLPIKey())->encrypt($dados['smtp_senha']),
    'smtp_sender'            => $dados['remetente'],
    'smtp_check_certificate' => empty($dados['smtp_tls']) ? 0 : 1,
    'admin_email'            => $dados['equipe'],
    'admin_email_name'       => 'Atendimento',
    'language'               => 'pt_BR',
    // O rodapé do e-mail usa este nome.
    'app_name'               => 'Panthera Track',
]);

// De fábrica, coleta e envio rodam em modo interno e dependem de alguém acessar a tela do GLPI.
foreach (['MailCollector' => 'mailgate', 'QueuedNotification' => 'queuednotification'] as $tipo => $nome) {
    $tarefa = new CronTask();
    if (!$tarefa->getFromDBbyName($tipo, $nome)
        || !$tarefa->update(['id' => $tarefa->getID(), 'mode' => CronTask::MODE_EXTERNAL, 'frequency' => 60])) {
        fwrite(STDERR, "não foi possível ajustar a tarefa $nome\n");
        exit(1);
    }
}

// O modelo de fábrica leva o endereço interno, que o cliente não alcança, e o nome da ferramenta.
$link = empty($dados['tela']) ? '' : "Acompanhe em: {$dados['tela']}\n\n";
$texto = "##ticket.action##\n\n"
    . "Título: ##ticket.title##\n"
    . "Situação: ##ticket.status##\n"
    . "Criticidade: ##ticket.priority##\n"
    . "Solicitante: ##ticket.authors##\n"
    . "Abertura: ##ticket.creationdate##\n"
    . "Descrição: ##ticket.description##\n\n"
    . "##IFticket.solution.description##Solução: ##ticket.solution.description##\n\n##ENDIFticket.solution.description##"
    . "##FOREACHfollowups##[##followup.date##] ##followup.author##: ##followup.description##\n\n##ENDFOREACHfollowups##"
    . $link
    . "Para responder, escreva acima da linha indicada no início desta mensagem.\n";
$modelo = new NotificationTemplate();
foreach ($modelo->find(['name' => ['Tickets', 'Tickets (Simple)']]) as $linha) {
    $traducoes = new NotificationTemplateTranslation();
    foreach ($traducoes->find(['notificationtemplates_id' => $linha['id']]) as $traducao) {
        $traducoes->update([
            'id'           => $traducao['id'],
            'subject'      => '##ticket.action## ##ticket.title##',
            'content_text' => $texto,
            'content_html' => nl2br(htmlescape($texto)),
        ]);
    }
}
echo "ok\n";
