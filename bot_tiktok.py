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
MODO_TESTE = True  # coloque True para testar rodando o pipeline imediatamente

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
    # pega primeira linha/frase, limita tamanho e remove chars que quebram o drawtext
    if not texto:
        return ""
    t = texto.replace("\n", " ").strip()
    if "." in t:
        t = t.split(".", 1)[0]
    t = t[:max_len]
    for ch in ["'", ":", "\\", "%"]:
        t = t.replace(ch, "")
    return t.strip()


async def gerar_tema_curioso_sombrio():
    log.info("Gerando tema curioso/sombrio via OpenAI...")
    prompt = (
        "Gere APENAS 1 ideia de tema curioso e sombrio para um video curto do TikTok "
        "em portugues do Brasil.\n"
        "O tema deve ser misterioso ou macabro, mas sem violencia grafica.\n"
        "Responda EXATAMENTE neste formato JSON, em uma unica linha:\n"
        '{"titulo": "TITULO EM PORTUGUES", "palavras": "keywords em ingles separadas por espaco"}\n'
        "As keywords devem ser em ingles para busca no Pexels "
        "(ex: 'dark forest mystery night')."
    )

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
        conteudo = r.json()["choices"][0]["message"]["content"].strip()

    # tenta remover cercas de codigo ``` se vierem
    if conteudo.startswith("```"):
        linhas = conteudo.splitlines()
        if linhas and linhas[0].startswith("```"):
            linhas = linhas[1:]
        if linhas and linhas[-1].strip().startswith("```"):
            linhas = linhas[:-1]
        conteudo = "\n".join(linhas).strip()

    try:
        tema = json.loads(conteudo)
        log.info(
            f"Tema gerado: {tema.get('titulo', '')} / {tema.get('palavras', '')}"
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

    # se nao achou nenhum abaixo do limite, usa o ultimo gerado mesmo
    log.info("Usando tema mesmo acima do limite de repeticoes.")
    return ultimo_tema or FALLBACK_TEMA.copy()


async def gerar_roteiro(tema):
    log.info("Gerando roteiro...")
    prompt = (
        "Crie um roteiro narrado em portugues brasileiro para um video curto do TikTok (60-90 segundos).\n"
        f"Tema: {tema['titulo']}\n"
        "O roteiro deve:\n"
        "- Comecar com uma frase de impacto nos primeiros 3 segundos\n"
        "- Ser narrado em primeira pessoa ou como narrador\n"
        "- Ter linguagem simples e envolvente\n"
        "- Terminar com call to action (Segue para mais historias assim)\n"
        "Retorne APENAS o texto da narracao, sem indicacoes de cena."
    )

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600,
            },
        )
        r.raise_for_status()
        texto = r.json()["choices"][0]["message"]["content"].strip()
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
        videos = r.json().get("videos", [])
    if not videos:
        raise Exception("Nenhum video encontrado no Pexels")

    video = random.choice(videos)
    url_video = None
    for f in video["video_files"]:
        if f.get("quality") == "sd" and f.get("width", 9999) <= 640:
            url_video = f["link"]
            break
    if not url_video:
        url_video = video["video_files"][-1]["link"]

    log.info(f"Baixando video SD: {url_video[:60]}...")
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(url_video, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        video_path = TMP / "fundo.mp4"
        video_path.write_bytes(resp.content)
    log.info(f"Video salvo: {video_path} ({len(resp.content)//1024}KB)")
    return video_path


def montar_video(video_path, audio_path, titulo, headline):
    log.info("Montando video final com ffmpeg direto...")
    output_path = TMP / "video_final.mp4"

    # base: crop 9:16
    vf = "scale=540:960:force_original_aspect_ratio=increase,crop=540:960"

    # tenta adicionar texto se tiver fonte e texto limpo
    texto_overlay = limpar_texto_overlay(headline or titulo, max_len=60)
    if texto_overlay and os.path.exists(FONT_PATH):
        log.info(f"Aplicando overlay de texto: {texto_overlay}")
        vf += (
            f",drawtext=fontfile='{FONT_PATH}':"
            f"text='{texto_overlay}':"
            "fontcolor=white:fontsize=36:"
            "x=(w-text_w)/2:y=80:"
            "box=1:boxcolor=0x000000aa:boxborderw=12"
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
        "4",
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
        "-crf",
        "28",
        "-preset",
        "ultrafast",
        "-vf",
        vf,
        "-r",
        "24",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
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
                files={"video": ("video.mp4", f, "video/mp4")},
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
        # usa primeira frase do roteiro como headline para overlay
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
