#!/usr/bin/env python3
import os
import asyncio
import logging
import random
import httpx
import tempfile
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
PEXELS_KEY = os.environ.get("PEXELS_KEY", "").strip()

HORAS_ENVIO = [10, 21]
MODO_TESTE = False  # coloque True para testar rodando o pipeline imediatamente

TMP = Path(tempfile.gettempdir())

# caminho de fonte para o drawtext (ajuste se necessario no seu container)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# controle de repeticao de tema por execucao do bot
MAX_REPETICOES_TEMA = 2
historico_temas = {}

# fallback simples caso a geracao de tema falhe
FALLBACK_TEMA = {
    "titulo": "Uma historia curiosa e sombria que quase ninguem conhece",
    "palavras": "dark mystery story night",
}


def checar_variaveis():
    ok = True
    for nome, val in [
        ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("PEXELS_KEY", PEXELS_KEY),
    ]:
        if val:
            log.info(f" OK: {nome} = {val[:8]}...")
        else:
            log.error(f" AUSENTE: {nome}")
            ok = False
    return ok


def limpar_texto_overlay(texto, max_len=60):
    """Pega primeira linha/frase, limita tamanho e remove chars que quebram o drawtext."""
    if not texto:
        return ""
    t = texto.replace("\n", " ").strip()
    if "." in t:
        t = t.split(".", 1)[0]
    t = t[:max_len]
    for ch in ["'", ":", "\\", "%"]:
        t = t.replace(ch, "")
    return t.strip()


def gerar_nome_video(titulo: str) -> Path:
    """Gera um nome de arquivo seguro a partir do titulo."""
    if not titulo:
        base = "video_tiktok"
    else:
        permitido = []
        for ch in titulo:
            if ch.isalnum() or ch in " -_.":
                permitido.append(ch)
            else:
                permitido.append("_")
        base = "".join(permitido).strip()
        base = "_".join(base.split())
        if not base:
            base = "video_tiktok"
    base = base[:60].strip("_")
    return TMP / f"{base}.mp4"


def normalizar_tema(bruto):
    """
    Garante que sempre volte um dict com 'titulo' e 'palavras'.
    Aceita dict, lista de dicts ou qualquer outra coisa -> fallback.
    """
    tema = None
    if isinstance(bruto, dict):
        tema = bruto
    elif isinstance(bruto, list) and bruto and isinstance(bruto[0], dict):
        tema = bruto[0]

    if not isinstance(tema, dict):
        return FALLBACK_TEMA.copy()

    titulo = str(tema.get("titulo") or "").strip()
    palavras = tema.get("palavras")
    if isinstance(palavras, list):
        palavras = " ".join(str(p) for p in palavras)
    else:
        palavras = str(palavras or "").strip()

    if not titulo:
        titulo = FALLBACK_TEMA["titulo"]
    if not palavras:
        palavras = FALLBACK_TEMA["palavras"]

    return {"titulo": titulo, "palavras": palavras}


def extrair_conteudo_chat(resp_json):
    """
    Extrai o campo content da resposta da API de chat de forma segura.
    Nunca faz lista['chave'].
    """
    data = resp_json
    if isinstance(data, list):
        if not data:
            return ""
        data = data[0]

    if not isinstance(data, dict):
        return str(data)

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        item = choices[0]
        if isinstance(item, dict):
            msg = item.get("message") or {}
            if isinstance(msg, dict):
                conteudo = msg.get("content")
                if isinstance(conteudo, str):
                    return conteudo
                return str(conteudo or "")
    return ""


async def gerar_tema_curioso_sombrio():
    log.info("Gerando tema curioso/sombrio via OpenAI...")
    prompt = """
Gere APENAS 1 ideia de tema curioso e sombrio para um video curto do TikTok em portugues do Brasil.
O tema deve ser misterioso ou macabro, mas sem violencia grafica.
Responda EXATAMENTE neste formato JSON, em uma unica linha:
{"titulo": "TITULO EM PORTUGUES", "palavras": "keywords em ingles separadas por espaco"}
As keywords devem ser em ingles para busca no Pexels (ex: "dark forest mystery night").
""".strip()

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
            },
        )
        r.raise_for_status()
        data = r.json()
        conteudo = extrair_conteudo_chat(data).strip()

    if conteudo.startswith("```"):
        linhas = conteudo.splitlines()
        if linhas and linhas.startswith("```"):
            linhas = linhas[1:]
        if linhas and linhas[-1].strip().startswith("```"):
            linhas = linhas[:-1]
        conteudo = "\n".join(linhas).strip()

    try:
        bruto = json.loads(conteudo)
        tema = normalizar_tema(bruto)
        log.info(
            f"Tema normalizado: {tema.get('titulo', '')} / {tema.get('palavras', '')}"
        )
        return tema
    except Exception as e:
        log.error(f"Falha ao parsear JSON do tema: {e} | conteudo={conteudo!r}")
        return FALLBACK_TEMA.copy()


async def escolher_tema():
    global historico_temas
    ultimo_tema = None
    for _ in range(8):
        tema = await gerar_tema_curioso_sombrio()
        ultimo_tema = tema
        titulo = tema.get("titulo", "").strip()
        if not titulo:
            continue

        qtd = historico_temas.get(titulo, 0)
        if qtd < MAX_REPETICOES_TEMA:
            historico_temas[titulo] = qtd + 1
            log.info(f"Tema escolhido (repeticao {historico_temas[titulo]}): {titulo}")
            return tema

    log.info("Usando tema mesmo acima do limite de repeticoes.")
    return ultimo_tema or FALLBACK_TEMA.copy()


async def gerar_roteiro(tema):
    log.info("Gerando roteiro...")
    prompt = f"""
Crie um roteiro narrado em portugues brasileiro para um video curto do TikTok (60-90 segundos).
Tema: {tema['titulo']}
O roteiro deve:
- Comecar com uma frase de impacto nos primeiros 3 segundos
- Ser narrado em primeira pessoa ou como narrador
- Ter linguagem simples e envolvente
- Terminar com call to action (Segue para mais historias assim)
Retorne APENAS o texto da narracao, sem indicacoes de cena.
""".strip()

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 700,
            },
        )
        r.raise_for_status()
        data = r.json()
        texto = extrair_conteudo_chat(data).strip()

    if not texto:
        raise RuntimeError("Resposta vazia ao gerar roteiro")

    log.info(f"Roteiro gerado: {len(texto)} chars")
    return texto


async def gerar_audio(texto):
    log.info("Gerando narracao com OpenAI TTS (voz nova)...")
    audio_path = TMP / "naracao.mp3"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "tts-1",
                "input": texto,
                "voice": "nova",
                "response_format": "mp3",
                "speed": 1.05,
            },
        )
        r.raise_for_status()
        audio_path.write_bytes(r.content)
    log.info(f"Audio salvo: {audio_path} ({len(r.content)//1024}KB)")
    return audio_path


async def baixar_video_fundo(palavras):
    log.info(f"Buscando video de fundo: {palavras}")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": palavras, "per_page": 15, "orientation": "portrait"},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            # pexels sempre deveria devolver dict; se vier lista, pega o primeiro dict
            data = data if data else {}
        videos = data.get("videos", [])
    if not videos:
        raise Exception("Nenhum video encontrado no Pexels")

    video = random.choice(videos)
    url_video = None
    for f in video.get("video_files", []):
        if f.get("quality") == "sd" and f.get("width", 9999) <= 640:
            url_video = f.get("link")
            break
    if not url_video:
        arquivos = video.get("video_files") or []
        if arquivos:
            url_video = arquivos[-1].get("link")
    if not url_video:
        raise Exception("Nao foi possivel determinar URL do video do Pexels")

    log.info(f"Baixando video SD: {url_video[:60]}...")
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(url_video, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        video_path = TMP / "fundo.mp4"
        video_path.write_bytes(resp.content)
    log.info(f"Video salvo: {video_path} ({len(resp.content)//1024}KB)")
    return video_path


def _render_video(video_path, audio_path, titulo, headline, width, height, crf, preset):
    log.info(f"Renderizando video com resolucao {width}x{height}, crf={crf}, preset={preset}...")

    output_path = gerar_nome_video(titulo)

    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

    texto_overlay = limpar_texto_overlay(headline or titulo, max_len=60)
    if texto_overlay and os.path.exists(FONT_PATH):
        log.info(f"Aplicando overlay de texto: {texto_overlay}")
        vf += (
            f",drawtext=fontfile='{FONT_PATH}':"
            f"text='{texto_overlay}':"
            "fontcolor=white:fontsize=42:"
            "x=(w-text_w)/2:y=100:"
            "box=1:boxcolor=0x000000aa:boxborderw=14"
        )
    else:
        log.warning(
            "Sem overlay de texto (fonte inexistente ou texto vazio). "
            "Verifique FONT_PATH se quiser o texto no video."
        )

    cmd = [
        FFMPEG,
        "-y",
        "-stream_loop",
        "8",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-vf",
        vf,
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-threads",
        "1",
        str(output_path),
    ]

    log.info("Executando ffmpeg...")
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        log.error(f"FFMPEG stdout: {result.stdout[-800:]}")
        log.error(f"FFMPEG stderr: {result.stderr[-800:]}")
        raise Exception(f"FFMPEG falhou com codigo {result.returncode}")

    log.info(f"Video montado: {output_path}")
    return output_path


def montar_video(video_path, audio_path, titulo, headline):
    try:
        return _render_video(
            video_path=video_path,
            audio_path=audio_path,
            titulo=titulo,
            headline=headline,
            width=720,
            height=1280,
            crf=22,
            preset="veryfast",
        )
    except Exception as e:
        log.error(f"Falha ao gerar em 720p: {e} -> tentando versao mais leve 540x960")

    return _render_video(
        video_path=video_path,
        audio_path=audio_path,
        titulo=titulo,
        headline=headline,
        width=540,
        height=960,
        crf=24,
        preset="ultrafast",
    )


async def enviar_telegram_texto(msg):
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        )


async def enviar_telegram_video(video_path, caption):
    log.info("Enviando video pelo Telegram...")
    async with httpx.AsyncClient(timeout=120) as c:
        with open(video_path, "rb") as f:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"video": (video_path.name, f, "video/mp4")},
            )
            r.raise_for_status()
    log.info("Video enviado com sucesso!")


async def pipeline():
    tema = await escolher_tema()
    log.info(f"Tema escolhido: {tema['titulo']}")
    await enviar_telegram_texto(
        f"Gerando video TikTok...\nTema: {tema['titulo']}"
    )
    try:
        roteiro = await gerar_roteiro(tema)
        audio = await gerar_audio(roteiro)
        video_bg = await baixar_video_fundo(tema["palavras"])
        headline = limpar_texto_overlay(roteiro, max_len=60)
        video_out = montar_video(video_bg, audio, tema["titulo"], headline)
        caption = f"{tema['titulo']}\n\nPoste no TikTok agora!"
        await enviar_telegram_video(video_out, caption)
    except Exception as e:
        log.error(f"Erro no pipeline: {e}")
        await enviar_telegram_texto(f"Erro ao gerar video: {e}")


async def main():
    log.info("Bot TikTok Automatico iniciando...")
    if not checar_variaveis():
        log.error("VARIAVEL AUSENTE - abortando.")
        return

    if MODO_TESTE:
        log.info("MODO TESTE - rodando pipeline agora!")
        await pipeline()
        return

    log.info(f"Aguardando horarios de envio: {HORAS_ENVIO} BRT")
    horas_disparadas = set()
    while True:
        agora = datetime.now(timezone.utc)
        hora_brt = (agora.hour - 3) % 24
        chave = f"{agora.date()}-{hora_brt}"
        if (
            hora_brt in HORAS_ENVIO
            and agora.minute == 0
            and chave not in horas_disparadas
        ):
            horas_disparadas.add(chave)
            await pipeline()
            if len(horas_disparadas) > 10:
                horas_disparadas = set(list(horas_disparadas)[-5:])
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
