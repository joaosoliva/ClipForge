# Guia de efeitos (Passo 1) — `guide.json`

Este documento descreve os campos novos do `guide.json` usados no Passo 1
para aplicar blur/motion blur **apenas durante a entrada (slide-in)**.

## Estrutura básica por item

```json
{
  "trigger": "sua frase",
  "mode": "image-only",
  "layout": "legacy_single",
  "image_id": "001",
  "effects": {
    "zoom": true,
    "slide": "left",
    "blur_entry": {
      "enabled": true,
      "duration": 0.6,
      "strength": 0.7,
      "method": "tblend"
    }
  }
}
```

## Campos de `effects.blur_entry`

| Campo | Tipo | Descrição |
|---|---|---|
| `enabled` | boolean | ativa/desativa o blur local da entrada |
| `duration` | number | duração em segundos (usa `SLIDE_DURATION` se omitido) |
| `strength` | number | intensidade (interpretação depende de `method`) |
| `method` | string | `tblend` (default), `tmix` ou `boxblur` |

### Interpretação de `strength`
- `tblend`: opacidade entre 0.05 e 1.0 (padrão 0.7).
- `tmix`: número de frames misturados (mínimo 2).
- `boxblur`: raio do blur (padrão 4.0).

## Observações
- O blur só é aplicado quando `slide` está ativo (`slide_direction` definido).
- A janela de blur segue o intervalo `between(t, 0, duration)`.
