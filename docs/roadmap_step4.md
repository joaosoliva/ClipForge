# Roadmap: Passo 4 — Expressões por track (keyframes → FFmpeg)

## Objetivo
Transformar keyframes de cada track em expressões FFmpeg que possam ser
usadas diretamente em `overlay` e `drawtext` (posição, escala, opacidade).

## Entregas deste passo
1. **Gerador de expressão por segmento** usando `if(between(t,...))`.
2. **Fallback seguro** quando não há keyframes suficientes.
3. **Ponte entre keyframes e overlay** para posição X/Y.

## Implementação
O módulo `timeline_expressions.py` expõe:
- `build_piecewise_expr(...)` → gera expressão piecewise por chave (`x`, `y`, etc.).

## Próximos passos
- Aplicar as expressões no renderer (`overlay` e `drawtext`).
- Adicionar UI para editar keyframes no editor.
