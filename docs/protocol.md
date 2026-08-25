# Protocolo do Custom Designs Portal (ACNH 3.0.3)

Animal Crossing: New Horizons não gera um código `MO-…`/`MA-…` localmente. Ele fala com
`api.hac.lp1.acbaa.srv.nintendo.net` por HTTPS/MessagePack; o ID numérico que essa API devolve é
formatado pelo cliente como o código público de 12 caracteres. Sem um backend atrás desse host,
o Ryujinx-Nextendo consegue redirecionar o hostname, mas o jogo nunca recebe um ID — por isso
`MO-0000-0000-0000` e `MA-0000-0000-0000`.

Toda a tabela abaixo foi obtida por engenharia reversa dinâmica: capturando o tráfego real do
cliente 3.0.3 contra um servidor experimental e ajustando a resposta até o próximo passo do jogo
ser liberado. Não foi extraído nenhum endpoint de texto simples do executável — os nomes de rota
já eram conhecidos publicamente; o formato exato de cada corpo, não.

## Rotas implementadas em `services/acnh-designs/server.py`

| Método | Rota | Papel |
|---|---|---|
| `POST` | `/api/v1/auth_token` | Login; devolve `{token, expire_at}`. Sem `expire_at` o cliente autentica mas nunca libera o restante do fluxo. |
| `POST` | `/api/v1/notification_tokens` | Registro de push, chamado logo após autenticar. 404 aqui gera o erro em jogo `2219-2404`. |
| `GET` | `/api/v1/users/{id}/profile_status` | `{"user_profile": "ok"/"ng", "land_profile": "ok"/"ng"}`. Só aceita esses dois literais. |
| `GET` | `/api/v1/message_cards` | Caixa de mensagens paginada. Um 404 aqui interrompe toda a sequência de inicialização antes mesmo da sincronização de perfil. |
| `GET`/`PUT` | `/api/v1/users/{id}/profile` | Perfil do criador: `id`, `digest`, `created_at`, e o campo que o executável 3.0.3 lê para o Creator ID, `mMyDesignAuthorId` (string). |
| `GET`/`PUT` | `/api/v1/users/{id}/land` | Credencial companheira: `{id, password}`. |
| `GET`/`PUT` | `/api/v1/users/{id}/icon` | Corpo binário; vazio é aceito. |
| `PUT` | `/api/v1/web_service_resources/{name}` | Só o status HTTP importa (ex.: `resort_unlock`). |
| `PUT` | `/api/v1/resort_planners/{id}/profile` | Upload do perfil do "resort planner". Ausente, o registro do Creator ID nunca se completa mesmo com autenticação e `profile_status` corretos. |
| `GET`/`POST` | `/api/v1/design_players` | Identidade do portal específica de designs — dona do ID numérico exibido como MA. Distinta do subject de conta usado no token. |
| `POST`/`GET`/`DELETE` | `/api/v1/designs` | Upload, download (`{id, body}`, não o corpo cru) e exclusão do design. |
| `GET` | `/api/v2/designs` | Listagem paginada; cada cabeçalho exige um objeto `address` completo (`user_id`, `id`, `name`, `display_id`, `in_app_id`) e `digest`/`offset`/`total`/`count` — campos ausentes fazem o cliente descartar a lista inteira. |

## Detalhes que custaram uma sessão inteira para descobrir

- **Alfabeto público de 30 caracteres.** O formatador do jogo usa
  `0123456789BCDFGHJKLMNPQRSTVWXY` — **sem `Z`** — para os 12 caracteres do código. Um backend
  que use um alfabeto de 31 caracteres (incluindo `Z`) gera um ID interno válido cujo texto
  publicado não bate com o que o jogo mostra.
- **`display_id` no objeto de endereço da listagem.** Se vier `0`, o jogo mostra `MA-0000-…` na
  tela de designs postados mesmo que o registro tenha um `design_player_id` correto.
- **`meta` deve ser texto, não o blob MessagePack cru.** O cliente decodifica esse campo
  localmente antes de renderizar a prévia; um blob binário direto trava a decodificação (foi a
  causa de um congelamento real do emulador, revertido removendo essa resposta).
- **Download não devolve o corpo cru.** É um envelope `{"id": ..., "body": ...}`.
- **`profile_status = "ng"` não dispara o registro.** A hipótese óbvia — "diga que falta perfil e
  o jogo vai criar um" — está errada para o 3.0.3: `ng` interrompe a sequência de inicialização
  *antes* de qualquer tarefa de perfil rodar. `ok` faz o cliente prosseguir assumindo que nada
  precisa ser registrado. Nenhum dos dois extremos, sozinho, faz o cliente chamar
  `design_players`/`resort_planners/.../profile` de novo — ver [`save-flags.md`](save-flags.md)
  para a explicação real.

## Fronteira de validação atual

Cada rota acima foi validada contra requisições reais do cliente 3.0.3 (não é apenas
"documentação pública" — o histórico de correções em `../README.md` mostra o ciclo de
tentativa/log/ajuste para cada uma). O que **não** foi confirmado ainda com um upload novo desde
a última correção de formato é o ciclo completo pós-registro de `design_players`; ver
[`save-flags.md`](save-flags.md) para o próximo passo concreto.
