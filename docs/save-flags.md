# A causa mais provável do Creator ID preso em zero

## O que já foi eliminado

O rastro de requisições em `acnh-designs.sqlite3` (tabela `request_trace`) mostra, na sessão de
diagnóstico mais recente, **só** duas chamadas por acesso ao portal:

```
POST /api/v1/auth_token
GET  /api/v1/users/{id}/profile_status
```

Nunca `message_cards`, `notification_tokens`, `design_players` ou
`resort_planners/{id}/profile` — mesmo depois de o servidor já implementar todas essas rotas
corretamente (ver [`protocol.md`](protocol.md)). Isso descarta o servidor como causa: não há mais
nenhuma resposta HTTP "errada" para adivinhar. O bloqueio está do lado do cliente, antes de
qualquer chamada de rede acontecer.

Também foi descartado que o Creator ID viesse do token de conta do Ryubing: a tela "Check
Creator ID info" e o Passport funcionam **totalmente offline** — leem um valor já persistido no
save, não algo que o servidor possa corrigir depois que o jogo já iniciou.

## A pista que sobrou: flags de evento do save

`tools/inspect_acnh_save.py` lê (somente leitura) os flags de evento do `personal.dat` da
revisão 34 (3.0.3), usando os nomes e offsets documentados publicamente pelo NHSE:

| Índice do flag | Nome (NHSE) | Offset |
|---|---|---|
| `0x34D` | `MyDesignExchangeFirstAccess` | `0x110 + 0xC170 + 0x34D*2` |
| `0x34E` | `MyDesignExchangeUploadOnce` | `0x110 + 0xC170 + 0x34E*2` |
| `0x41A` | `MyDesignExchangeDiscloseAuthorID` | `0x110 + 0xC170 + 0x41A*2` |

O terceiro (`DiscloseAuthorID`) é só a opção "Leave it visible" do Passport — já confirmado
inofensivo. Os dois primeiros são exatamente o par que controla se o jogo trata o quiosque do
Custom Designs Portal como "primeiro acesso" (fluxo de onboarding, que é quando o cliente
efetivamente chama `design_players`/`resort_planners/.../profile`) ou como "já configurado"
(fluxo de rotina, que é só `auth_token` + `profile_status`).

Isso explica perfeitamente os dois resultados observados na sessão de 31/07:

1. **Zerar os dois flags** (com o Murmur3 corrigido — a primeira tentativa tinha overflow errado
   e foi corretamente revertida) fez **"Posted Designs" sumir do menu** — sintoma direto de o
   jogo voltar a tratar o portal como nunca acessado.
2. Depois desse reset, o fluxo avançou de verdade: apareceu o 404 de `message_cards`, depois a
   alternância de `profile_status`, depois o registro de `notification_tokens`/`expire_at` — ou
   seja, o cliente **só chama essas rotas dentro da janela de "primeiro acesso"**.

O problema é que, uma vez que o jogador passa por essa janela de onboarding (mesmo sem o
servidor estar 100% pronto ainda), o próprio jogo marca os flags como concluídos de novo. A
sessão de diagnóstico de 02/08 (a que só mostra `auth_token`+`profile_status` no trace) rodou
**depois** dessa janela já ter se fechado — por isso nenhuma correção de servidor daquele dia
conseguia ser exercitada pelo cliente.

## Próximo passo concreto

1. Fechar o Ryubing.
2. Rodar `tools/reset_acnh_portal_access.py <slot>/Villager0` sem `--apply` primeiro (dry-run,
   já valida hash e criptografia); depois com `--apply`. O script já tem o Murmur3 corrigido e o
   auto-check de integridade que faltava na primeira tentativa.
3. Abrir o jogo **uma única vez**, ir direto ao terminal, **Access the Portal**, e — decisivo —
   **Post** um design (não visualizar um já existente: o registro de identidade do portal parece
   disparar no fluxo de upload, não no de simples acesso).
4. Consultar `request_trace` imediatamente depois. Se `design_players` e
   `resort_planners/{id}/profile` aparecerem dessa vez, o Creator ID deve deixar de ser zero na
   consulta seguinte — e o servidor já sabe responder corretamente a ambos.
5. Se o Creator ID ainda ficar em zero mesmo com essas rotas tendo sido chamadas e respondidas
   com sucesso, o valor lido pela UI provavelmente vem de um terceiro campo do save ainda não
   mapeado (os offsets `0x13538`, `0x13540`, `0x36640` e `0x36648` já foram testados e
   descartados — ver o histórico da conversa original). Nesse caso o próximo alvo é comparar um
   dump do save **imediatamente antes e depois** desse Post, byte a byte, para achar o campo real
   em vez de continuar testando offsets às cegas.

Este é o item de maior probabilidade de sucesso que não foi tentado: todas as correções de
servidor desde 31/07 nunca foram testadas *dentro* da janela de onboarding, porque essa janela
só existiu por poucos minutos naquele dia antes de o próprio jogo fechá-la de novo.
