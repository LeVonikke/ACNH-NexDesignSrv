# É viável integrar isso à Nextendo Network?

Sim, arquiteturalmente — e preenche uma lacuna real que existe hoje na organização. Levantamento
feito em 25/08/2026 contra os repositórios públicos de
[github.com/NextendoNetwork](https://github.com/NextendoNetwork).

## O que a Nextendo Network já tem

A organização separa dois tipos de serviço:

- **Servidores de jogo NEX/PRUDP** — um por título (`mario-kart-8-deluxe`, `splatoon-2`,
  `super-smash-bros-ultimate`, `animal-crossing-new-horizons`, etc.), todos construídos sobre o
  framework comum `nextendo-nex` (Go, do zero, reimplementando o protocolo NEX/PRUDP da Nintendo).
  Isso cobre **amigos, presença, matchmaking e portões de sessão** — a camada de "jogar online
  junto".
- **Microsserviços HTTP isolados**, fora do NEX: `nextendo-account` (identidade/BAAS),
  `baas-jwks` (chaves de verificação de token), `nx-dauth` (autenticação de dispositivo),
  `nx-scsi` (cloud save), `sni-router` (roteamento TLS por SNI), `nextendo-nncs`
  (NAT-check). Cada um resolve **um** serviço de conta específico do Switch, sem depender do NEX.

O `Ryujinx-Nextendo` (client) só redireciona hostnames e assina tokens de conta localmente; ele
não implementa nenhum serviço por si — cada host redirecionado precisa de um backend real do
lado servidor.

## Onde este projeto se encaixa

O repositório `animal-crossing-new-horizons` já existe na organização, mas **só** cobre a camada
NEX (arquivos: `friends.go`, `presence.go`, `gates.go`, `dashboard.go` — 2 commits, sem PRs). Ele
não toca no Custom Designs Portal porque **esse não é um serviço NEX**: é a API HTTP/MessagePack
de `api.hac.lp1.acbaa.srv.nintendo.net`, arquiteturalmente idêntica em espírito a `nx-scsi`
(cloud save) — um microsserviço HTTP isolado, específico de um recurso, sem PRUDP.

Ou seja: este projeto não compete com o repositório ACNH existente, ele **completa** a peça que
falta nele, seguindo exatamente o padrão que a própria organização já usa para
`nx-scsi`/`baas-jwks`/`nx-dauth`. Um caminho natural de integração seria um novo microsserviço
irmão (`nx-acnh-designs` ou uma pasta dentro do repo `animal-crossing-new-horizons` mesmo,
paralela ao código NEX).

## O que pesa contra propor isso como PR agora

- **Licença.** `Ryujinx-Nextendo` é PolyForm Shield 1.0.0 (source-available, proíbe uso
  competitivo). Isso não impede um fork/serviço privado próprio, mas qualquer coisa que vire PR
  para o repositório oficial passa a estar sob os termos deles, não sob uma licença própria.
- **Sem guia de contribuição.** Nenhum dos repositórios da organização tem `CONTRIBUTING.md` ou
  processo de PR documentado — o caminho certo é abrir conversa (issue ou comunidade deles) antes
  de mandar código, não simplesmente abrir um PR de ~700 linhas do nada.
- **Linguagem diferente.** O protótipo atual é Python (`http.server` + SQLite); toda a família de
  microsserviços da organização é Go. Adotar oficialmente exigiria reescrever para Go para ficar
  consistente com o resto do parque (`nextendo-nex`, `nx-scsi`, etc.) — o protocolo já
  documentado em [`protocol.md`](protocol.md) facilita isso, mas é trabalho novo, não portar.
- **Cliente não termina de validar.** Como descrito em [`save-flags.md`](save-flags.md), ainda
  falta confirmar que o fluxo completo (`design_players` → `resort_planners/.../profile` →
  Creator ID não-zero) realmente fecha; propor upstream antes disso é propor algo não comprovado
  ponta a ponta.

## Recomendação

Manter como projeto próprio e privado por enquanto (é o que este repositório é), terminar de
validar o fluxo end-to-end pelo caminho descrito em `save-flags.md`, e só então considerar levar
a proposta — como conversa, não como PR surpresa — para a comunidade da Nextendo Network,
propondo o novo microsserviço HTTP como complemento ao `animal-crossing-new-horizons` existente.
