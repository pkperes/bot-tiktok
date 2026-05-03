#!/usr/bin/env python3
"""
Bot TikTok Automático - Gera vídeo diário e envia pelo Telegram
Roda no Railway. Requer: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY, PEXELS_KEY
"""
import os
import asyncio
import logging
import random
import httpx
import tempfile
from datetime import datetime
from pathlib import Path

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

HORA_ENVIO = 10
MODO_TESTE = True

TMP = Path(tempfile.gettempdir())

TEMAS = [
    {"titulo": "O homem que viveu anos fingindo ser medico no Brasil", "palavras": "hospital doctor crime"},
    {"titulo": "O serial killer que trabalhava como palhaco de festas infantis", "palavras": "clown dark mystery"},
    {"titulo": "O pais onde dormir no trabalho e sinal de dedicacao", "palavras": "japan office work"},
    {"titulo": "O homem que sobreviveu a dois ataques nucleares", "palavras": "explosion nuclear history"},
    {"titulo": "A mulher que descobriu que era irma do proprio marido", "palavras": "family drama shock"},
    {"titulo": "O aviao que ficou 37 anos esquecido em um aeroporto", "palavras": "airplane airport abandoned"},
    {"titulo": "O pais onde e ilegal sorrir para a policia", "palavras": "police law bizarre"},
    {"titulo": "O homem que ganhou na loteria 7 vezes", "palavras": "money lottery winner"},
    {"titulo": "A cidade submersa que reaparece quando a represa seca", "palavras": "underwater city lake"},
    {"titulo": "O crime perfeito que foi resolvido por uma selfie", "palavras": "crime investigation phone"},
    {"titulo": "O bebe que nasceu duas vezes", "palavras": "baby hospital miracle"},
    {"titulo": "O homem que foi enterrado vivo e sobreviveu", "palavras": "cemetery survival dark"},
    {"titulo": "A ilha que aparece e desaparece no mapa", "palavras": "island ocean mystery"},
    {"titulo": "O prisioneiro que escapou da prisao tres vezes pelo correio", "palavras": "prison escape crime"},
]


def checar_variaveis():
    ok = True
    for nome, val in [
        ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("PEXELS_KEY", PEXELS_KEY),
    ]:
        if val:
            log.info(f"  OK: {nome} = {val[:8]}...")
        else:
            log.error(f"  AUSENTE: {nome}")
            ok = False
    return ok


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
    log.info("Gerando narracao com gTTS...")
    from gtts import gTTS
    tts = gTTS(text=texto, lang="pt", slow=False)
    audio_path = TMP / "naracao.mp3"
    tts.save(str(audio_path))
    log.info(f"Audio salvo: {audio_path}")
    return audio_path


async def baixar_video_fundo(palavras):
    log.info(f"Buscando video de fundo: {palavras}")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": palavras, "per_page": 10, "orientation": "portrait"},
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            raise Exception("Nenhum video encontrado no Pexels")
        video = random.choice(videos)
        url_video = None
        for f in video["video_files"]:
            if f.get("quality") in ("hd", "sd") and f.get("width", 0) <= 1080:
                url_video = f["link"]
                break
        if not url_video:
            url_video = video["video_files"][0]["link"]
        log.info(f"Baixando video: {url_video[:60]}...")
        resp = await c.get(url_video, follow_redirects=True, timeout=120)
        resp.raise_for_status()
    video_path = TMP / "fundo.mp4"
    video_path.write_bytes(resp.content)
    log.info(f"Video salvo: {video_path}")
    return video_path


def montar_video(video_path, audio_path, titulo):
    log.info("Montando video final...")
    from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips
    audio = AudioFileClip(str(audio_path))
    duracao = audio.duration
    video_raw = VideoFileClip(str(video_path))
    if video_raw.duration < duracao:
        repeticoes = int(duracao / video_raw.duration) + 1
        clips = [video_raw] * repeticoes
        video_loop = concatenate_videoclips(clips)
    else:
        video_loop = video_raw
    video_clip = video_loop.subclipped(0, duracao)
    video_clip = video_clip.resized((1080, 1920))
    video_final = video_clip.with_audio(audio)
    output_path = TMP / "video_final.mp4"
    video_final.write_videofile(
        str(output_path),
        fps=24,
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )
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
    tema = random.choice(TEMAS)
    log.info(f"Tema escolhido: {tema['titulo']}")
    await enviar_telegram_texto(f"Gerando video TikTok...\nTema: {tema['titulo']}")
    try:
        roteiro = await gerar_roteiro(tema)
        audio = await gerar_audio(roteiro)
        video_bg = await baixar_video_fundo(tema["palavras"])
        video_out = montar_video(video_bg, audio, tema["titulo"])
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
    log.info(f"Aguardando horario de envio: {HORA_ENVIO}:00 BRT")
    while True:
        agora = datetime.utcnow()
        hora_brt = (agora.hour - 3) % 24
        if hora_brt == HORA_ENVIO and agora.minute == 0:
            await pipeline()
            await asyncio.sleep(61)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
