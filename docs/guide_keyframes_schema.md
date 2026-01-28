# Guia de keyframes (Passo 5) — `guide.json`

Este documento descreve como declarar keyframes por imagem para animar
posições (x/y) e outros valores básicos.

## Estrutura básica por item

```json
{
  "trigger": "sua frase",
  "mode": "image-only",
  "layout": "legacy_single",
  "image_id": "001",
  "effects": {
    "slide": "left",
    "keyframes": [
      { "time": 0.0, "x": 120, "y": 80, "easing": "linear" },
      { "time": 0.6, "x": 320, "y": 80, "easing": "ease_out" }
    ]
  }
}
```

## Campos de `effects.keyframes`

- `time` (number): tempo em segundos no clip.
- `x`, `y` (number): posição absoluta em pixels.
- `scale` (number, opcional): reservado para uso futuro.
- `opacity` (number, opcional): reservado para uso futuro.
- `easing` (string): `linear`, `ease_in`, `ease_out`, `ease_in_out`,
  `cubic_in`, `cubic_out`, `cubic_in_out`.

## Observações
- Se `keyframes` estiver definido, ele tem prioridade sobre `slide`.
- Os valores `x` e `y` substituem o posicionamento calculado pelo layout.
