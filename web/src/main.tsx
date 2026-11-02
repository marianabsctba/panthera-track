import "@fontsource-variable/inter";
import "@mantine/core/styles.css";
import "./estilo.css";

import { MantineProvider } from "@mantine/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Aplicacao } from "./Aplicacao";
import { tema } from "./tema";

// A política de segurança só aceita a tag de estilo do Mantine com a chave desta resposta.
const chave = document.querySelector<HTMLMetaElement>('meta[name="track-chave-de-estilo"]')?.content ?? "";
// A trava de rolagem dos modais cria a própria tag de estilo e busca a chave aqui.
(globalThis as { __webpack_nonce__?: string }).__webpack_nonce__ = chave;

createRoot(document.getElementById("raiz")!).render(
  <StrictMode>
    <MantineProvider theme={tema} defaultColorScheme="dark" forceColorScheme="dark" getStyleNonce={() => chave}>
      <Aplicacao />
    </MantineProvider>
  </StrictMode>,
);
