#!/usr/bin/env python3
"""
Bot TikTok Automático — Gera vídeo diário e envia pelo Telegram
Roda no Railway. Requer: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY,
                          PEXELS_KEY
"""

import os, asyncio, logging, random, httpx, json, re, tempfile, textwrap
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ——————————————— CONFIG ———————————————
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "").strip()
PEXELS_KEY       = os.environ.get("PEXELS_KEY", "").strip()

HORA_ENVIO  = 10        # gera e envia às 10h BRT
MODO_TESTE  = True      # True = roda imediatamente ao iniciar

TMP = Path(tempfile.gettempdir())

TEMAS = [
    {"titulo": "O homem que viveu anos fingindo ser médico no Brasil",       "palavras": "hospital doctor crime"},
    {"titulo": "O serial killer que trabalhava como palhaço de festas infantis", "palavras": "clown dark mystery"},
    {"titulo": "O país onde dormir no trabalho é sinal de dedicação",         "palavras": "japan office work"},
    {"titulo": "O homem que sobreviveu a dois ataques nucleares",             "palavras": "explosion nuclear history"},
    {"titulo": "A mulher que descobriu que era irmã do próprio marido",       "palavras": "family drama shock"},
    {"titulo": "O avião que ficou 37 anos esquecido em um aeroporto",        "palavras": "airplane airport abandoned"},
    {"titulo": "O país onde é ilegal sorrir para a polícia",                  "palavras": "police law bizarre"},
    {"titulo": "O homem que ganhou na loteria 7 vezes",                      "palavras": "money lottery winner"},
    {"titulo": "A cidade submersa que reaparece quando a represa seca",      "palavras": "underwater city lake"},
    {"titulo": "O crime perfeito que foi resolvido por uma selfie",          "palavras": "crime investigation phone"},
    {"titulo": "O bebê que nasceu duas vezes",                               "palavras": "baby hospital miracle"},
    {"titulo": "O homem que foi enterrado vivo e sobreviveu",                "palavras": "cemetery survival dark"},
    {"titulo": "A ilha que aparece e desaparece no mapa",                    "palavras": "island ocean mystery"},
    {"titulo": "O prisioneiro que escapou da prisão três vezes pelo correio", "palavras": "prison escape crime"},
]

# ——————————————— HELPERS ———————————————
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

async def gerar_roteiro(tema: dict) -> str:
    log.info("Gerando roteiro...")
    prompt = f"""Crie um roteiro narrado em português brasileiro para um vídeo curto do TikTok (60-90 segundos).
Tema: {tema['titulo']}
O roteiro deve:
- Começar com uma frase de impacto nos primeiros 3 segundos
- Ser narrado em primeira pessoa ou como narrador
- Ter linguagem simples e envolvente
- Terminar com call to action ("Segue para mais histórias assim")
Retorne APENAS o texto da narração, sem indicações de cena."""

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 600},
        )
        r.raise_for_status()
        texto = r.json()["choices"][0]["message"]["content"].strip()
        log.info(f"Roteiro gerado: {len(texto)} chars")
        return texto

async def gerar_audio(texto: str) -> Path:
    log.info("Gerando narração com gTTS...")
    from gtts import gTTS
    tts = gTTS(text=texto, lang="pt", slow=False)
    audio_path = TMP / "naracao.mp3"
    tts.save(str(audio_path))
    log.info(f"Áudio salvo: {audio_path}")
    return audio_path

async def baixar_video_fundo(palavras: str) -> Path:
    log.info(f"Buscando vídeo de fundo: {palavras}")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": palavras, "per_page": 10, "orientation": "portrait"},
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            raise Exception("Nenhum vídeo encontrado no Pexels")
        video = random.choice(videos)
        url_video = None
        for f in video["video_files"]:
            if f.get("quality") in ("hd", "sd") and f.get("width", 0) <= 1080:
                url_video = f["link"]
                break
        if not url_video:
            url_video = video["video_files"][0]["link"]
        log.info(f"Baixando vídeo: {url_video[:60]}...")
        resp = await c.get(url_video, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        video_path = TMP / "fundo.mp4"
        video_path.write_bytes(resp.content)
        log.info(f"Vídeo salvo: {video_path}")
        return video_path

def montar_video(video_path: Path, audio_path: Path, titulo: str) -> Path:
    log.info("Montando vídeo final...")
    import subprocess
    from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip
    from moviepy.editor import concatenate_videoclips

    audio = AudioFileClip(str(audio_path))
    duracao = audio.duration

    video_raw = VideoFileClip(str(video_path))
    if video_raw.duration < duracao:
        repeticoes = int(duracao / video_raw.duration) + 1
        video_loop = concatenate_videoclips([video_raw] * repeticoes)
    else:
        video_loop = video_raw

    video_clip = video_loop.subclip(0, duracao).resize((1080, 1920))
    video_final = video_clip.set_audio(audio)

    output_path = TMP / "video_final.mp4"
    video_final.write_videofile(
        str(output_path),
        fps=24,
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )
    log.info(f"Vídeo montado: {output_path}")
    return output_path

async def enviar_telegram_texto(msg: str):
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
        )

async def enviar_telegram_video(video_path: Path, caption: str):
    log.info("Enviando vídeo pelo Telegram...")
    async with httpx.AsyncClient(timeout=120) as c:
        with open(video_path, "rb") as f:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"video": ("video.mp4", f, "video/mp4")},
            )
        r.raise_for_status()
        log.info("Vídeo enviado com sucesso!")

async def pipeline():
    tema = random.choice(TEMAS)
    log.info(f"Tema escolhido: {tema['titulo']}")
    await enviar_telegram_texto(f"🎬 <b>Gerando vídeo TikTok...</b>\n📌 Tema: {tema['titulo']}")
    try:
        roteiro   = await gerar_roteiro(tema)
        audio     = await gerar_audio(roteiro)
        video_bg  = await baixar_video_fundo(tema["palavras"])
        video_out = montar_video(video_bg, audio, tema["titulo"])
        caption   = f"🎬 {tema['titulo']}\n\n📲 Poste no TikTok agora!"
        await enviar_telegram_video(video_out, caption)
    except Exception as e:
        log.error(f"Erro no pipeline: {e}")
        await enviar_telegram_texto(f"❌ Erro ao gerar vídeo: {e}")

async def main():
    log.info("Bot TikTok Automático iniciando...")
    if not checar_variaveis():
        log.error("VARIAVEL AUSENTE — abortando.")
        return

    if MODO_TESTE:
        log.info("MODO TESTE — rodando pipeline agora!")
        await pipeline()
        return

    log.info(f"Aguardando horário de envio: {HORA_ENVIO}:00 BRT")
    while True:
        agora = datetime.utcnow()
        hora_brt = (agora.hour - 3) % 24
        if hora_brt == HORA_ENVIO and agora.minute == 0:
            await pipeline()
            await asyncio.sleep(61)
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
