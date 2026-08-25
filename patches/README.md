# Patch para o cliente Ryujinx-Nextendo

`ryujinx-nextendo-acnh-account.patch` é um diff de **dois arquivos** contra
`main` de [NextendoNetwork/Ryujinx-Nextendo](https://github.com/NextendoNetwork/Ryujinx-Nextendo)
(licença PolyForm Shield 1.0.0 — leia os termos deles antes de redistribuir builds compilados).
Este repositório não inclui o restante da árvore-fonte do emulador: aplique o patch em um clone
próprio já autorizado do projeto.

## O que ele muda

- **`DnsMitmResolver.cs`** — adiciona uma rota específica para
  `api.hac.lp1.acbaa.srv.nintendo.net`, configurável por `NEXTENDO_ACNH_DESIGNS_IP` com fallback
  para `NEXTENDO_SERVER_IP` se não for definida. Não altera o roteamento dos outros hosts.
- **`ManagerServer.cs`** (serviço de conta/`acc`) — o `sub` do token BAAS antes era aleatório a
  cada inicialização do emulador. Serviços que guardam estado por conta (como o perfil do Custom
  Designs Portal) enxergavam isso como uma conta Nintendo nova a cada sessão. O patch deriva um
  `sub` estável por SHA-256 do PID vinculado à conta Nextendo, e adiciona a estrutura `nintendo`
  (`dt`, `pc`, `di`, `sn`, `ist`) que o `nnAccount` do 3.0.3 valida antes de aceitar o token. Some
  logs de diagnóstico (`[Nextendo/ACNH] ...`) foram adicionados nos pontos onde o cliente consulta
  identidade de conta, úteis para confirmar se o jogo está de fato chamando
  `GetNintendoAccountUserResourceCacheForApplication` e afins.

## Aplicar

```bash
git clone https://github.com/NextendoNetwork/Ryujinx-Nextendo.git
cd Ryujinx-Nextendo
git apply /caminho/para/ryujinx-nextendo-acnh-account.patch
```

Depois compile normalmente (`COMPILING.md` do próprio projeto). O patch foi validado compilando
com 0 erros contra `main` e contra a atualização 1.7.2.
