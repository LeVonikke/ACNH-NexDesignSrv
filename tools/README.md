# Ferramentas de save (ACNH 3.0.3)

Ambas operam sobre `personal.dat`/`personalHeader.dat` de um slot `Villager0`/`Villager1` da
revisão de save 34 (3.0.3), usando o mesmo formato de par criptografado e hash Murmur3 de
integridade que o NHSE.

- **`inspect_acnh_save.py`** — só leitura. Imprime os flags relevantes do portal
  (`MyDesignExchangeFirstAccess`, `MyDesignExchangeUploadOnce`, `MyDesignExchangeDiscloseAuthorID`)
  e os IDs de vila/jogador.

  ```bash
  python inspect_acnh_save.py "<pasta-do-save>"
  ```

- **`reset_acnh_portal_access.py`** — zera só os dois flags de onboarding do portal
  (`FirstAccess`/`UploadOnce`), recalcula o hash de integridade e faz um self-check de
  criptografia antes de escrever qualquer coisa. **Modo dry-run por padrão** — precisa de
  `--apply` para gravar.

  ```bash
  # sempre rodar sem --apply primeiro
  python reset_acnh_portal_access.py "<pasta-do-save>/Villager0"

  # só depois de conferir a saída
  python reset_acnh_portal_access.py "<pasta-do-save>/Villager0" --apply
  ```

## Antes de usar `--apply`

1. Feche o emulador completamente.
2. Faça uma cópia da pasta do slot inteira (não só os dois arquivos).
3. Rode sem `--apply` e confira que a mensagem final diz "hash verified".
4. Só então rode com `--apply`.

Isso opera exclusivamente no seu próprio arquivo de save local — não envia nem recebe nada pela
rede. Veja [`../docs/save-flags.md`](../docs/save-flags.md) para o motivo de resetar esses dois
flags especificamente.
