# nextendo-acnh-designs

![Python](https://img.shields.io/badge/Python-blue) ![Status](https://img.shields.io/badge/status-ativo-brightgreen) ![Privado](https://img.shields.io/badge/-privado-grey)

Backend privado, auto-hospedado, para o Custom Designs Portal de Animal Crossing: New Horizons
(3.0.3) rodando via [Ryujinx-Nextendo](https://github.com/NextendoNetwork/Ryujinx-Nextendo).
Objetivo: parar de ver `MO-0000-0000-0000`/`MA-0000-0000-0000` e conseguir enviar/baixar designs
de verdade. Projeto pessoal, não afiliado à Nintendo nem à Nextendo Network.

## Estado atual

- ✅ Servidor (`services/acnh-designs`) implementa toda a superfície de API conhecida do cliente
  3.0.3 — autenticação, status de perfil, mensagens, perfil/land/ícone do usuário, perfil do
  "resort planner", identidade de `design_players`, upload/listagem/download/exclusão de designs.
  16 testes automatizados, todos passando.
- ✅ Upload e download de designs **funcionam** e persistem (validado em jogo:
  `MO-1F0V-HWR5-JTC2` e mais dois códigos).
- ⚠️ **Creator ID (`MA-…`) ainda trava em zero.** Não é mais um problema de servidor — é uma
  janela de onboarding do lado do cliente que só existe uma vez por save. Ver
  [`docs/save-flags.md`](docs/save-flags.md) para a causa identificada e o próximo passo exato.
- 🧩 Patch de dois arquivos para o cliente (`patches/`) resolve identidade de conta instável entre
  reinicializações e roteamento de DNS do host do portal — não resolve sozinho o Creator ID.

## Estrutura

```
services/acnh-designs/   servidor HTTP/MessagePack (Python, stdlib + msgpack + cryptography)
patches/                 diff de 2 arquivos para o Ryujinx-Nextendo + instruções de aplicação
tools/                   inspeção e reset (só os dois flags de onboarding) do save do ACNH
docs/
  protocol.md                    toda a API reversa, endpoint a endpoint
  save-flags.md                  por que o Creator ID trava em zero, e o próximo passo concreto
  nextendo-network-integration.md  viabilidade de integrar isto à organização Nextendo Network
```

## Rodar o servidor

```bash
cd services/acnh-designs
python -m pip install -r requirements.txt
python -m unittest test_server -v          # 16 testes, sem dependências externas

export ACNH_DESIGNS_AUTH_SECRET="<pelo menos 32 bytes aleatórios>"
python server.py --host 0.0.0.0 --port 443 --certfile portal.pem --keyfile portal-key.pem
```

No lado do Ryujinx-Nextendo, aponte `NEXTENDO_ACNH_DESIGNS_IP` para o serviço (ou use uma entrada
exata em `sdcard/atmosphere/hosts/default.txt` apontando
`api.hac.lp1.acbaa.srv.nintendo.net` para o IP do serviço). Sem essa variável, o host cai na rota
padrão do Nextendo (`NEXTENDO_SERVER_IP`).

## Como isto surgiu

Reconstruído a partir de uma sessão real de depuração — mais de duas horas indo e voltando entre
capturar o erro em jogo, ler o log do serviço, e corrigir exatamente o próximo campo que o
cliente rejeitava (alfabeto de código, `display_id`, formato de `meta`, envelope de download,
`expire_at` do token, etc. — cada um documentado em [`docs/protocol.md`](docs/protocol.md)). O
código e os testes vieram direto do protótipo validado nessa sessão; a documentação e a análise
de causa do Creator ID são a parte nova, escrita depois de vasculhar o estado real do projeto
(banco SQLite com o rastro de requisições, diffs de git contra o upstream, e os offsets de save
já testados) em vez de repetir tentativa e erro.

## Aviso

Interopera com um serviço online da Nintendo por engenharia reversa dinâmica, para uso pessoal
com hardware/software próprios. Não redistribui nenhum binário ou código extraído do jogo — as
ferramentas de save leem/escrevem apenas dois flags de evento documentados publicamente pelo
projeto NHSE, e o patch do emulador é um diff textual para aplicar sobre um clone próprio do
Ryujinx-Nextendo, não uma cópia da árvore-fonte dele.
