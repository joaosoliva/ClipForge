import os
import time
import random
import json
import re
import io
import requests
import undetected_chromedriver as uc
from PIL import Image, UnidentifiedImageError
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys

# =========================================================
# Helpers
# =========================================================

TOPIC_RE = re.compile(r'<topico="?([^">]*)"?', re.IGNORECASE)

def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[\\/:*?"<>|]+', '_', text)
    text = re.sub(r'\s+', '_', text)
    return text[:80]

def parse_search_terms(txt_path: str):
    """
    Retorna:
    - topic_from_txt (str|None)
    - list[dict]: {term, tag}
    """
    terms = []
    topic = None

    with open(txt_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            m = TOPIC_RE.search(line)
            if m:
                topic = sanitize(m.group(1))
                continue

            tag = "[STOCK]"
            term = line

            if "[" in line and "]" in line:
                term = line[:line.find("[")].strip()
                tag = line[line.find("["):].strip().upper()
                if tag not in ["[MEME]", "[STOCK]", "[TECH]"]:
                    tag = "[STOCK]"

            terms.append({
                "term": term,
                "tag": tag
            })

    return topic, terms


def normalize_ext(ext: str) -> str:
    ext = ext.lower().strip(".")
    if ext == "jpeg":
        return "jpg"
    return ext


def build_filename(term_idx0: int, img_idx1: int, term: str, ext: str) -> str:
    return f"{term_idx0+1:02d}_{img_idx1:02d}_{sanitize(term)}.{normalize_ext(ext)}"


def extract_image_url(img):
    src = img.get_attribute("src")
    if src and src.startswith("http"):
        return src

    data_src = img.get_attribute("data-src") or img.get_attribute("data-iurl")
    if data_src and data_src.startswith("http"):
        return data_src

    srcset = img.get_attribute("srcset")
    if srcset:
        candidates = [
            part.strip().split(" ")[0]
            for part in srcset.split(",")
            if part.strip()
        ]
        for candidate in reversed(candidates):
            if candidate.startswith("http"):
                return candidate

    return None


def sleep_with_jitter(min_s: float, max_s: float, extra_s: float = 0.0) -> None:
    """Pausa com variação aleatória para simular comportamento humano"""
    time.sleep(random.uniform(min_s, max_s) + extra_s)


def human_like_delay() -> float:
    """Gera delays mais naturais com distribuição similar ao comportamento humano"""
    # Usa distribuição exponencial para simular tempos de reação humanos
    base = random.expovariate(0.5)  # média ~2s
    jitter = random.uniform(-0.3, 0.8)
    return max(0.8, min(base + jitter, 6.0))


def add_stealth_overrides(driver, platform: str, languages: list[str]) -> None:
    langs_json = json.dumps(languages)
    script = f"""
    Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});
    Object.defineProperty(navigator, 'languages', {{get: () => {langs_json}}});
    Object.defineProperty(navigator, 'plugins', {{get: () => [1, 2, 3, 4, 5]}});
    Object.defineProperty(navigator, 'platform', {{get: () => '{platform}'}});
    Object.defineProperty(navigator, 'hardwareConcurrency', {{get: () => {random.choice([4, 8, 16])}}});
    Object.defineProperty(navigator, 'deviceMemory', {{get: () => {random.choice([4, 8, 16])}}});
    if (!window.chrome) {{
        window.chrome = {{ runtime: {{}} }};
    }}
    """
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": script},
    )


def human_scroll(driver, extra_delay_s: float = 0.0) -> None:
    """Scroll mais natural com pausas e variações"""
    scroll_count = random.randint(2, 4)
    for i in range(scroll_count):
        # Varia a direção ocasionalmente (scroll pra cima também)
        if random.random() < 0.15:
            scroll_amount = -random.randint(100, 300)
        else:
            scroll_amount = random.randint(250, 550)
        
        driver.execute_script(
            "window.scrollBy(0, arguments[0]);",
            scroll_amount,
        )
        
        # Pausas mais naturais entre scrolls
        pause = human_like_delay()
        time.sleep(pause + extra_delay_s)
        
        # Às vezes para por mais tempo (como se estivesse lendo)
        if random.random() < 0.3:
            time.sleep(random.uniform(1.5, 3.5))


def human_mouse_movement(driver, element) -> None:
    """Movimento de mouse mais natural"""
    actions = ActionChains(driver)
    
    # Às vezes move para perto do elemento antes
    if random.random() < 0.4:
        # Move para uma posição aleatória próxima
        offset_x = random.randint(-50, 50)
        offset_y = random.randint(-50, 50)
        actions.move_to_element_with_offset(element, offset_x, offset_y)
        actions.pause(random.uniform(0.1, 0.3))
    
    # Move para o elemento
    actions.move_to_element(element)
    actions.pause(random.uniform(0.3, 0.8))
    actions.perform()


# =========================================================
# Main downloader (GUI-ready)
# =========================================================

def download_google_images(
    search_terms_txt: str,
    dest_root: str,
    images_per_term: int = 3,
    manual_topic: str | None = None,
    extra_query_tags: list[str] | None = None,
    resume: bool = True,
    extra_delay_s: float = 0.0,
    cooldown_every: int = 5,  # Reduzido de 8 para 5
    cooldown_min_s: float = 25.0,  # Aumentado de 18
    cooldown_max_s: float = 45.0,  # Aumentado de 30
    on_log=lambda s: print(s),
    on_progress=lambda *args: None,
    stop_flag=lambda: False,
):
    """
    - Tudo vai para UMA pasta: dest_root / topic
    - Nome: XX_XX_termo.ext
    - Configurações anti-CAPTCHA melhoradas
    """

    topic_from_txt, terms = parse_search_terms(search_terms_txt)

    topic = sanitize(manual_topic) if manual_topic else topic_from_txt
    if not topic:
        raise ValueError("Nenhum tópico definido (nem no TXT nem manualmente).")

    final_dir = os.path.join(dest_root, topic)
    os.makedirs(final_dir, exist_ok=True)

    state_path = os.path.join(final_dir, ".download_state.json")

    # -----------------------------------------------------
    # Resume
    # -----------------------------------------------------
    start_term_idx = 0
    start_img_idx = 1

    if resume and os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
            start_term_idx = st.get("term_index", 0)
            start_img_idx = st.get("img_index", 1)
        on_log(f"[RESUME] Retomando do termo {start_term_idx+1}, imagem {start_img_idx}")

    # -----------------------------------------------------
    # Chrome setup com mais headers realistas
    # -----------------------------------------------------
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]
    ua = random.choice(user_agents)
    if "Macintosh" in ua:
        platform = "MacIntel"
        languages = ["pt-BR", "pt", "en-US", "en"]
    elif "Linux" in ua:
        platform = "Linux x86_64"
        languages = ["pt-BR", "pt", "en-US", "en"]
    else:
        platform = "Win32"
        languages = ["pt-BR", "pt", "en-US", "en"]

    opts = Options()
    opts.add_argument(f"user-agent={ua}")
    
    # Tamanho de janela mais comum
    width = random.choice([1366, 1920, 1440, 1536])
    height = random.choice([768, 1080, 900, 864])
    opts.add_argument(f"window-size={width},{height}")
    
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--lang=pt-BR,pt")
    opts.add_argument("--disable-dev-shm-usage")

    # undetected_chromedriver já cuida dessas opções automaticamente
    driver = uc.Chrome(options=opts, version_main=143, use_subprocess=True)
    add_stealth_overrides(driver, platform, languages)

    # Adiciona cookies/comportamento inicial mais natural
    driver.execute_cdp_cmd('Network.setUserAgentOverride', {
        "userAgent": ua,
        "acceptLanguage": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    })

    def keep_main_tab():
        if len(driver.window_handles) > 1:
            main = driver.window_handles[0]
            for h in driver.window_handles[1:]:
                driver.switch_to.window(h)
                driver.close()
                time.sleep(0.4)  # Aumentado ligeiramente
            driver.switch_to.window(main)

    def get_valid_thumbnails():
        """
        Retorna apenas thumbnails válidos (não sugestões do Google).
        Filtra elementos cujo pai é <a> (sugestões relacionadas).
        """
        try:
            results_container = driver.find_element(By.CSS_SELECTOR, 'div.MjjYud')
            all_thumbs = results_container.find_elements(By.CSS_SELECTOR, 'img.YQ4gaf')
        except Exception:
            on_log("[AVISO] Não foi possível encontrar container de resultados")
            return []

        valid_thumbs = []
        for thumb in all_thumbs:
            try:
                parent = thumb.find_element(By.XPATH, '..')
                if parent.tag_name != 'a':
                    valid_thumbs.append(thumb)
            except Exception:
                pass

        return valid_thumbs

    # -----------------------------------------------------
    # Loop principal com delays aumentados
    # -----------------------------------------------------
    try:
        total_downloaded = 0
        
        # Pausa inicial ao abrir o navegador
        on_log("[INICIALIZANDO] Aguardando para parecer mais natural...")
        time.sleep(random.uniform(2.5, 4.5))
        
        for term_idx in range(start_term_idx, len(terms)):
            if stop_flag():
                break

            term_obj = terms[term_idx]
            term = term_obj["term"]
            tag = term_obj["tag"]

            query_parts = [term]
            if tag in ["[MEME]", "[TECH]"]:
                query_parts.append("gif")
            if tag in ["[TECH]", "[STOCK]"] and extra_query_tags:
                query_parts.extend(extra_query_tags)
            query = " ".join(query_parts)

            url = f"https://www.google.com/search?tbm=isch&q={query}"
            on_log(f"\n[BUSCA] {term} {tag}")
            
            # Pausa entre buscas (mais longa)
            if term_idx > start_term_idx:
                pause_between_searches = random.uniform(8.0, 15.0) + extra_delay_s
                on_log(f"[PAUSA] Aguardando {pause_between_searches:.1f}s antes da próxima busca...")
                time.sleep(pause_between_searches)
            
            driver.get(url)
            
            # Espera mais longa após carregar a página
            initial_load = random.uniform(6.0, 9.0) + extra_delay_s
            time.sleep(initial_load)
            
            # Comportamento humano: às vezes rola a página antes de clicar
            if random.random() < 0.7:
                human_scroll(driver, extra_delay_s)

            thumb_idx = 0
            img_idx = start_img_idx if term_idx == start_term_idx else 1
            valid_thumbs = []
            consecutive_failures = 0  # Contador de falhas consecutivas

            while img_idx <= images_per_term:
                if stop_flag():
                    break

                keep_main_tab()

                # Busca thumbnails
                if not valid_thumbs or thumb_idx >= len(valid_thumbs):
                    valid_thumbs = get_valid_thumbnails()
                    
                    if not valid_thumbs:
                        on_log(f"[AVISO] Nenhum thumbnail válido encontrado")
                        break

                if thumb_idx >= len(valid_thumbs):
                    on_log(f"[INFO] Apenas {len(valid_thumbs)} thumbnails disponíveis")
                    break

                thumb = valid_thumbs[thumb_idx]
                thumb_idx += 1

                try:
                    # Scroll até o elemento de forma mais natural
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", 
                        thumb
                    )
                    time.sleep(random.uniform(1.2, 2.0) + extra_delay_s)

                    # Movimento de mouse mais natural
                    human_mouse_movement(driver, thumb)
                    time.sleep(random.uniform(0.4, 1.0) + extra_delay_s)

                    # Clique
                    driver.execute_script("arguments[0].click();", thumb)
                    on_log(f"[CLICK] Thumbnail {thumb_idx}/{len(valid_thumbs)}")
                    
                    # Espera mais longa após o clique
                    click_wait = random.uniform(4.5, 6.5) + extra_delay_s
                    time.sleep(click_wait)

                    keep_main_tab()

                    # Busca imagem grande
                    big_imgs = driver.find_elements(By.CSS_SELECTOR, "img.iPVvYb")
                    success = False

                    for big in big_imgs:
                        src = extract_image_url(big)
                        if not src:
                            continue

                        try:
                            # Headers mais completos na requisição
                            headers = {
                                'User-Agent': ua,
                                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                                'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
                                'Referer': 'https://www.google.com/',
                            }
                            
                            r = requests.get(src, timeout=20, headers=headers)
                            content_type = (r.headers.get("Content-Type") or "").lower()
                            ext = normalize_ext(src.split(".")[-1].split("?")[0])

                            image_bytes = r.content
                            detected_ext = None

                            try:
                                with Image.open(io.BytesIO(image_bytes)) as img:
                                    fmt = (img.format or "").upper()
                                    detected_ext = {
                                        "JPEG": "jpg",
                                        "JPG": "jpg",
                                        "PNG": "png",
                                        "GIF": "gif",
                                        "WEBP": "webp",
                                        "AVIF": "avif",
                                    }.get(fmt)

                                    if detected_ext in ["webp", "avif"]:
                                        filename = build_filename(term_idx, img_idx, term, "jpg")
                                        path = os.path.join(final_dir, filename)

                                        if os.path.exists(path):
                                            on_log(f"[SKIP] {filename} já existe")
                                            success = True
                                            break

                                        img = img.convert("RGB")
                                        img.save(path, "JPEG", quality=95)
                                        success = True
                                        on_log(f"[OK] {filename} (convertido de {detected_ext})")
                                        break
                            except UnidentifiedImageError:
                                detected_ext = None

                            if "image/webp" in content_type or "image/avif" in content_type:
                                if not detected_ext:
                                    on_log("[ERRO] Conteúdo WebP/AVIF não reconhecido para conversão")
                                    continue
                                target_ext = "jpg"
                            else:
                                target_ext = ext
                                if target_ext not in ["jpg", "jpeg", "png", "gif"]:
                                    target_ext = detected_ext or "jpg"

                            filename = build_filename(term_idx, img_idx, term, target_ext)
                            path = os.path.join(final_dir, filename)

                            if os.path.exists(path):
                                on_log(f"[SKIP] {filename} já existe")
                                success = True
                                break

                            with open(path, "wb") as f:
                                f.write(image_bytes)
                            success = True
                            on_log(f"[OK] {filename}")
                            
                            # Pausa após download bem-sucedido
                            time.sleep(random.uniform(2.5, 4.5) + extra_delay_s)
                            break
                            
                        except Exception as e:
                            on_log(f"[ERRO] Falha ao baixar: {e}")
                            time.sleep(random.uniform(1.0, 2.0))

                    # Fecha painel lateral
                    try:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(random.uniform(1.0, 1.8) + extra_delay_s)
                    except:
                        pass

                    if success:
                        total_downloaded += 1
                        img_idx += 1
                        consecutive_failures = 0  # Reset contador

                        # Cooldown mais frequente e mais longo
                        if cooldown_every > 0 and total_downloaded % cooldown_every == 0:
                            cooldown = random.uniform(cooldown_min_s, cooldown_max_s) + extra_delay_s
                            on_log(f"[PAUSA] Cooldown de {cooldown:.1f}s após {total_downloaded} downloads")
                            time.sleep(cooldown)
                        
                        # Salva estado
                        with open(state_path, "w", encoding="utf-8") as f:
                            json.dump(
                                {
                                    "topic": topic,
                                    "term_index": term_idx,
                                    "img_index": img_idx,
                                },
                                f,
                                indent=2,
                                ensure_ascii=False,
                            )

                        on_progress(
                            term_idx + 1,
                            len(terms),
                            img_idx - 1,
                            images_per_term,
                            term,
                        )
                    else:
                        consecutive_failures += 1
                        # Se falhar muito seguido, faz uma pausa mais longa
                        if consecutive_failures >= 3:
                            on_log("[AVISO] Múltiplas falhas consecutivas, fazendo pausa longa...")
                            time.sleep(random.uniform(15.0, 25.0))
                            consecutive_failures = 0

                except Exception as e:
                    on_log(f"[ERRO] Exceção ao processar thumbnail: {e}")
                    keep_main_tab()
                    time.sleep(random.uniform(2.0, 4.0))

            start_img_idx = 1

    finally:
        driver.quit()
        on_log("\n[FINALIZADO]")