# Roadmap: Passo 2 — Curvas de animação e interpolação

## Objetivo
Introduzir a base de **curvas de animação** (easing) e interpolação de keyframes
para que, no próximo passo, possamos transformar esses valores em expressões
FFmpeg (x/y/scale/opacity) por tempo.

## Entregas deste passo
1. **Catálogo de easing** com funções conhecidas (linear, ease-in/out, cubic).
2. **Interpolador genérico** para keyframes (valores numéricos por chave).
3. **Validador** para detectar keyframes fora de ordem ou easing inválido.

## Implementação
O módulo `animation_curves.py` expõe:
- `interpolate_keyframes(...)` → retorna os valores interpolados para um tempo `t`.
- `validate_keyframes(...)` → retorna erros de ordenação ou easing desconhecido.
- `EASING_MAP` → catálogo de curvas suportadas.

## Próximos passos
- Conectar o interpolador ao renderer para produzir expressões FFmpeg.
- Expandir o schema do `guide.json` para definir keyframes por camada.
- Adicionar camada de timeline no editor (GUI).
