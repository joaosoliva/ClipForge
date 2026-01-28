# Roadmap: Passo 6 — Validação e normalização de keyframes

## Objetivo
Adicionar validação e feedback de keyframes para garantir curvas consistentes
antes de aplicar as expressões FFmpeg.

## Entregas deste passo
1. **Validação de easing e ordenação** usando utilitários de animação.
2. **Avisos claros no console** para keyframes inválidos.

## Implementação
- `main.py` chama `validate_keyframes(...)` ao construir `ImageLayer`.
- Erros são emitidos como warnings para facilitar depuração do `guide.json`.

## Próximos passos
- Validar ranges por duração do clip.
- Aplicar keyframes em `scale` e `opacity`.
