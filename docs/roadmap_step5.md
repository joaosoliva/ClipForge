# Roadmap: Passo 5 — Keyframes por imagem no renderer

## Objetivo
Permitir que cada imagem receba **keyframes de posição** (x/y) e use essas
curvas diretamente no `overlay` do FFmpeg.

## Entregas deste passo
1. Parse de `effects.keyframes` no `guide.json`.
2. Suporte a `ImageLayer.keyframes` com `KeyframeSpec`.
3. Expressões piecewise para `x` e `y` usando easing.

## Implementação
- `main.py` converte `effects.keyframes` em `KeyframeSpec`.
- `renderer_v2.py` aplica `build_piecewise_expr(...)` para `x`/`y`.
- `guide_keyframes_schema.md` documenta o formato esperado.

## Próximos passos
- Permitir keyframes para `scale` e `opacity`.
- Integrar keyframes em textos e stickman.
