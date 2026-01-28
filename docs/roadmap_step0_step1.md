# Roadmap: Passo 0 (Diagnóstico) e início do Passo 1 (Base estrutural)

## Passo 0 — Diagnóstico técnico (executado)

### 0.1 Mapa do pipeline atual

**Entrada de imagens e animações existentes**
- `ImageLayer` hoje suporta apenas `zoom_enabled` e `slide_direction`, sem detalhes de efeito local ou por janela de tempo. Isso limita a implementação de blur/motion blur somente em transições.  
- `ClipSpec` é o ponto central de configuração do render, mas não possui timeline nem keyframes.  

**Renderização e pontos de extensão**
- O slide-in é aplicado em `renderer_v2._apply_slide()` e usado no `render_clip()` ao montar `x_expr`/`y_expr` por imagem.  
- A janela temporal de slide é derivada de `SLIDE_DURATION * fps` e pode ser reutilizada como base para blur local (somente durante a entrada).
- A arquitetura já utiliza filtros FFmpeg por camada, o que facilita inserir filtros condicionais com `enable=between(t, start, end)`.

**Conclusão técnica**
- O blur/motion blur deve ser inserido no filtro da própria camada de imagem, ativado apenas na janela do slide-in.  
- É necessário evoluir o schema de dados para representar:
  - efeitos com janela temporal (ex.: blur local)
  - keyframes de posição/escala/opacidade
  - trilhas (tracks) para composição com triggers

### 0.2 Decisões iniciais
- Blur/motion blur **não será global**; só durante o trecho de entrada.
- O “start” do blur será alinhado ao `slide_direction` e ao tempo de entrada (ou trigger futuro).
- A base dos próximos passos é adicionar estrutura de timeline e keyframes.

---

## Passo 1 — Base estrutural (iniciado)

### 1.1 Estruturas adicionadas (stubs de dados)
Foram criadas estruturas em `clip_specs.py` para iniciar a base de timeline:
- `BlurEntrySpec`: descreve blur local da entrada (duração, força, método).
- `KeyframeSpec`: valor + easing por tempo.
- `EffectWindowSpec`: janela de efeito por tempo (start/end).
- `TrackSpec`: trilha de edição (imagem/texto/sfx).
- `TimelineSpec`: container de trilhas.

### 1.2 Integração inicial de blur de entrada
- O `guide.json` agora pode declarar `effects.blur_entry` por item, que é propagado para cada imagem gerada pelo `build_timeline`.
- O renderer aplica o blur **apenas durante a janela do slide-in** quando `slide_direction` está ativo.

### 1.3 Próximos passos imediatos do Passo 1
1. Especificar JSON de timeline para o editor (incluindo triggers e spans de texto).
2. Atualizar o parser de `ClipSpec` para aceitar essas estruturas.
3. Criar validadores simples (ex.: start < end).
4. Expandir o `renderer_v2` para efeitos adicionais além do blur local (easing, reposicionamento por keyframes).

---

## Observações
- O pipeline atual já permite interpolação via expressões FFmpeg; a camada de timeline será usada para gerar essas expressões com easing.
- Com os stubs de dados já adicionados, o próximo incremento é conectar o renderer com `BlurEntrySpec` e `TimelineSpec`.
