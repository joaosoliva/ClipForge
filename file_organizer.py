import os
import shutil
from pathlib import Path

def encontrar_arquivo(pasta, extensao, palavra_chave=None):
    """Procura por um arquivo com a extensão e palavra-chave especificadas."""
    arquivos = list(Path(pasta).glob(f"*.{extensao}"))
    
    if palavra_chave:
        arquivos = [f for f in arquivos if palavra_chave.lower() in f.name.lower()]
    
    return arquivos[0] if arquivos else None

def renomear_arquivos(pasta, callback_log=None, callback_escolha=None):
    """
    Procura e renomeia os arquivos conforme especificado.
    
    Args:
        pasta: Caminho da pasta a processar
        callback_log: Função para registrar mensagens (opcional)
        callback_escolha: Função para escolha manual de arquivo (opcional)
    
    Returns:
        tuple: (sucesso: bool, mensagem: str)
    """
    def log(msg):
        if callback_log:
            callback_log(msg)
        else:
            print(msg)
    
    pasta = Path(pasta)
    
    if not pasta.exists() or not pasta.is_dir():
        msg = f"Erro: A pasta '{pasta}' não existe ou não é um diretório válido."
        log(msg)
        return False, msg
    
    # Configuração dos arquivos a procurar
    arquivos_config = [
        {"extensao": "json", "palavra_chave": "stickman", "destino": "stickman.json"},
        {"extensao": "json", "palavra_chave": "guide", "destino": "guia.json"},
        {"extensao": "txt", "palavra_chave": None, "destino": "search_terms.txt"},
        {"extensao": "srt", "palavra_chave": None, "destino": "audio.srt"}
    ]
    
    log(f"Procurando arquivos na pasta: {pasta}\n")
    
    total_processados = 0
    total_faltando = 0
    
    for config in arquivos_config:
        extensao = config["extensao"]
        palavra_chave = config["palavra_chave"]
        destino = config["destino"]
        
        # Procura o arquivo
        arquivo_encontrado = encontrar_arquivo(pasta, extensao, palavra_chave)
        
        if arquivo_encontrado:
            log(f"✓ Encontrado: {arquivo_encontrado.name} → {destino}")
            # Renomeia o arquivo
            novo_caminho = pasta / destino
            try:
                shutil.move(str(arquivo_encontrado), str(novo_caminho))
                total_processados += 1
            except Exception as e:
                log(f"  ✗ Erro ao mover: {e}")
        else:
            desc = f"{extensao.upper()}"
            if palavra_chave:
                desc += f" com '{palavra_chave}'"
            
            log(f"✗ Não encontrado: {desc} ({destino})")
            
            # Se há callback de escolha, usa ele
            if callback_escolha:
                arquivo_manual = callback_escolha(extensao, destino)
                if arquivo_manual and os.path.isfile(arquivo_manual):
                    novo_caminho = pasta / destino
                    try:
                        shutil.copy(str(arquivo_manual), str(novo_caminho))
                        log(f"  ✓ Copiado: {Path(arquivo_manual).name} → {destino}")
                        total_processados += 1
                    except Exception as e:
                        log(f"  ✗ Erro ao copiar: {e}")
                        total_faltando += 1
                else:
                    log(f"  ✗ Arquivo '{destino}' não foi configurado.")
                    total_faltando += 1
            else:
                total_faltando += 1
    
    log("\nProcesso concluído!")
    
    if total_faltando == 0:
        msg = f"Sucesso! {total_processados} arquivos processados."
        return True, msg
    else:
        msg = f"Parcialmente concluído: {total_processados} processados, {total_faltando} faltando."
        return False, msg

if __name__ == "__main__":
    pasta_input = input("Digite o caminho da pasta: ").strip()
    sucesso, msg = renomear_arquivos(pasta_input)
    print(f"\n{msg}")