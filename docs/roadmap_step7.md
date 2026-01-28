# Roadmap: Passo 7 — Validação por duração do clip

## Objetivo
Garantir que keyframes não extrapolem a duração do clip, evitando
expressões FFmpeg inválidas ou movimentações fora de janela.

## Entregas deste passo
1. **Validação por duração** dos keyframes ao construir `ImageLayer`.
2. **Warnings claros** quando tempos excedem a duração.

## Implementação
- `main.py` valida `time > duration` e emite aviso.

## Próximos passos
- Ajustar automaticamente keyframes fora de range (clamp opcional).
- Expandir validações para `scale` e `opacity`.
