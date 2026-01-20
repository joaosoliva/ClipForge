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
    on_log=lambda s: print(s),
    on_progress=lambda *args: None,
    stop_flag=lambda: False,
):
    """
    - Tudo vai para UMA pasta: dest_root / topic
    - Nome: XX_XX_termo.ext
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
    # Chrome setup
    # -----------------------------------------------------
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64)",
    ]
    ua = random.choice(user_agents)

    opts = Options()
    opts.add_argument(f"user-agent={ua}")
    opts.add_argument("window-size=1200,900")

    driver = uc.Chrome(options=opts)

    def keep_main_tab():
        if len(driver.window_handles) > 1:
            main = driver.window_handles[0]
            for h in driver.window_handles[1:]:
                driver.switch_to.window(h)
                driver.close()
                time.sleep(0.3)
            driver.switch_to.window(main)

    def get_valid_thumbnails():
        """
        Retorna apenas thumbnails válidos (não sugestões do Google).
        Filtra elementos cujo pai é <a> (sugestões relacionadas).
        """
        try:
            # Busca o container principal de resultados
            results_container = driver.find_element(By.CSS_SELECTOR, 'div.MjjYud')
            all_thumbs = results_container.find_elements(By.CSS_SELECTOR, 'img.YQ4gaf')
        except Exception:
            on_log("[AVISO] Não foi possível encontrar container de resultados")
            return []

        # Filtra apenas thumbnails reais (pai != <a>)
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
    # Loop principal
    # -----------------------------------------------------
    try:
        for term_idx in range(start_term_idx, len(terms)):
            if stop_flag():
                break

            term_obj = terms[term_idx]
            term = term_obj["term"]
            tag = term_obj["tag"]

            query_parts = [term]
            if tag in ["[MEME]", "[TECH]"]:
                query_parts.append("gif")
            if extra_query_tags:
                query_parts.extend(extra_query_tags)
            query = " ".join(query_parts)

            url = f"https://www.google.com/search?tbm=isch&q={query}"
            on_log(f"\n[BUSCA] {term} {tag}")
            driver.get(url)
            time.sleep(5)

            thumb_idx = 0
            img_idx = start_img_idx if term_idx == start_term_idx else 1
            valid_thumbs = []  # Cache dos thumbnails

            while img_idx <= images_per_term:
                if stop_flag():
                    break

                keep_main_tab()

                # Busca thumbnails apenas uma vez (ou quando necessário)
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
                    # Scroll até o elemento
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", thumb
                    )
                    time.sleep(random.uniform(0.8, 1.2))
                    
                    # Clique via JavaScript (evita interceptação)
                    driver.execute_script("arguments[0].click();", thumb)
                    on_log(f"[CLICK] Thumbnail {thumb_idx}/{len(valid_thumbs)}")
                    time.sleep(random.uniform(3, 4))

                    keep_main_tab()

                    # Busca imagem grande
                    big_imgs = driver.find_elements(By.CSS_SELECTOR, "img.iPVvYb")
                    success = False

                    for big in big_imgs:
                        src = extract_image_url(big)
                        if not src:
                            continue

                        try:
                            r = requests.get(src, timeout=15)
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

                            # Não sobrescrever se já existe
                            if os.path.exists(path):
                                on_log(f"[SKIP] {filename} já existe")
                                success = True
                                break

                            with open(path, "wb") as f:
                                f.write(image_bytes)
                            success = True
                            on_log(f"[OK] {filename}")
                            break
                        except Exception as e:
                            on_log(f"[ERRO] Falha ao baixar: {e}")

                    # Fecha painel lateral (ESC)
                    try:
                        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                        time.sleep(1)
                    except:
                        pass

                    if success:
                        img_idx += 1
                        
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

                except Exception as e:
                    on_log(f"[ERRO] Exceção ao processar thumbnail: {e}")
                    keep_main_tab()

            start_img_idx = 1  # reset for next term

    finally:
        driver.quit()
        on_log("\n[FINALIZADO]")
