# Roadmap: Passo 3 — Expressões FFmpeg para easing

## Objetivo
Preparar uma camada de **expressões FFmpeg** para traduzir easing de keyframes
em valores contínuos (x/y/scale/opacity) dentro dos filtros de vídeo.

## Entregas deste passo
1. **Gerador de expressão** para interpolar valores com easing.
2. **Catálogo de easing** compatível com expressões FFmpeg.
3. **Formato unificado** para ligar `start/end` e expressão gerada.

## Implementação
O módulo `ffmpeg_expressions.py` expõe:
- `build_eased_value_expr(...)` → gera expressão FFmpeg para interpolar valores.
- `EASING_EXPRESSIONS` → mapa de easing para expressões.
- `EasedExpression` → encapsula a expressão e o intervalo.

## Próximos passos
- Conectar o gerador aos keyframes do `TimelineSpec`.
- Compor expressões por track (imagem/texto/sfx).
- Aplicar essas expressões nos filtros `overlay` e `drawtext`.
