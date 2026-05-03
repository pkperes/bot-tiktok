#!/usr/bin/env python3
"""
Bot TikTok Automático — Gera vídeo diário e envia pelo Telegram
Roda no Railway. Requer: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY,
                          ELEVENLABS_KEY, PEXELS_KEY
"""

import os, asyncio, logging, random, httpx, json, re
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ──────────────────────────── CONFIG ────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "").strip()
ELEVENLABS_KEY   = os.environ.get("ELEVENLABS_KEY", "").strip()
PEXELS_KEY       = os.environ.get("PEXELS_KEY", "").strip()

HORA_ENVIO   = 10          # gera e envia às 10h BRT
MODO_TESTE   = True        # True = roda imediatamente ao iniciar

# Voice ID padrão ElevenLabs (Rachel — voz feminina en/pt)
VOICE_ID     = "21m00Tcm4TlvDq8ikWAM"

# Temas do calendário
TEMAS = [
    {"titulo": "O homem que viveu anos fingindo ser médico no Brasil",
     "gancho": "Esse homem operou dezenas de pessoas sem ter formação nenhuma",
     "busca_video": "hospital crime dark"},
    {"titulo": "O caso Escola Base: a maior injustiça da TV brasileira",
     "gancho": "A mídia destruiu vidas inocentes ao vivo e nunca pediu desculpa",
     "busca_video": "news camera television"},
    {"titulo": "Por que você não consegue fazer cócegas em si mesmo",
     "gancho": "Seu próprio cérebro te impede — e o motivo é assustador",
     "busca_video": "brain science neuroscience"},
    {"titulo": "O país onde dormir no trabalho é sinal de dedicação",
     "gancho": "No Japão, quem dorme na mesa recebe elogio do chefe",
     "busca_video": "japan office work"},
    {"titulo": "O homem que ficou 17 anos num aeroporto sem poder sair",
     "gancho": "Ele não tinha para onde ir — e o aeroporto virou sua casa",
     "busca_video": "airport terminal night"},
    {"titulo": "O roubo da Mona Lisa que levou 28 horas para ser notado",
     "gancho": "O maior museu do mundo levou mais de um dia para notar o roubo",
     "busca_video": "museum art louvre"},
    {"titulo": "O homem que sobreviveu a dois ataques nucleares",
     "gancho": "Ele estava em Hiroshima, escapou, e pegou o trem para Nagasaki",
     "busca_video": "explosion atomic bomb history"},
    {"titulo": "A floresta onde as bússolas não funcionam",
     "gancho": "As bússolas enlouquecem nessa floresta — e ninguém explicou por quê",
     "busca_video": "dark forest mystery fog"},
    {"titulo": "O golpe de 1 bilhão que começou com um e-mail mal escrito",
     "gancho": "O e-mail tinha erros de português — e mesmo assim funcionou",
     "busca_video": "hacker computer dark"},
    {"titulo": "A cidade que proibiu morrer por falta de cemitério",
     "gancho": "Se você for doente grave nessa cidade precisa sair antes de morrer",
     "busca_video": "cemetery snow cold"},
    {"titulo": "Por que sua voz gravada soa diferente",
     "gancho": "A voz que você acha sua não é a que os outros ouvem",
     "busca_video": "microphone sound wave"},
    {"titulo": "O serial killer que trabalhava como palhaço de festas infantis",
     "gancho": "Os vizinhos disseram que ele era o homem mais gentil da rua",
     "busca_video": "clown dark circus"},
    {"titulo": "Por que você esquece seus sonhos em segundos",
     "gancho": "Tem um mecanismo no cérebro que apaga seus sonhos de propósito",
     "busca_video": "sleep dream night"},
    {"titulo": "O banco roubado por dentro durante um feriadão",
     "gancho": "Os ladrões alugaram a loja ao lado e fizeram um túnel no fim de semana",
     "busca_video": "bank vault money"},
]

TMP = Path("/tmp/tiktok_bot")
TMP.mkdir(exist_ok=True)

# ──────────────────────────── VERIFICAÇÃO ────────────────────────────
def checar_variaveis():
    ok = True
    for nome, val in {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ELEVENLABS_KEY": ELEVENLABS_KEY,
        "PEXELS_KEY": PEXELS_KEY,
    }.items():
        if val:
            log.info(f"  OK: {nome} = {val[:8]}...")
        else:
            log.error(f"  AUSENTE: {nome}")
            ok = False
    return ok

# ──────────────────────────── ROTEIRO ────────────────────────────
async def gerar_roteiro(tema: dict) -> str:
    prompt = f"""Crie um roteiro em português brasileiro para um vídeo TikTok de 50 segundos.
Tema: {tema['titulo']}
Gancho inicial: {tema['gancho']}

Estrutura obrigatória:
[GANCHO] (3 segundos) — frase impactante baseada no gancho
[CONTEXTO] (10 segundos) — quem, quando, onde
[DESENVOLVIMENTO] (25 segundos) — o fato principal com detalhes chocantes
[REVELAÇÃO] (10 segundos) — desfecho surpreendente
[CTA] (2 segundos) — "Segue para mais curiosidades assim"

Escreva APENAS o texto narrado, sem marcações de tempo. Tom misterioso e envolvente.
Máximo 350 palavras."""

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.8,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        roteiro = r.json()["choices"][0]["message"]["content"].strip()
        log.info(f"Roteiro gerado: {len(roteiro)} chars")
        return roteiro

# ──────────────────────────── ÁUDIO ────────────────────────────
async def gerar_audio(texto: str) -> Path:
    audio_path = TMP / "narration.mp3"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        audio_path.write_bytes(r.content)
        log.info(f"Áudio gerado: {audio_path.stat().st_size / 1024:.1f} KB")
        return audio_path

# ──────────────────────────── VÍDEO DE FUNDO ────────────────────────────
async def baixar_video_fundo(busca: str) -> Path:
    video_path = TMP / "background.mp4"
    headers = {"Authorization": PEXELS_KEY}
    params = {"query": busca, "per_page": 10, "orientation": "portrait", "size": "medium"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.pexels.com/videos/search", headers=headers, params=params)
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            raise Exception(f"Nenhum vídeo encontrado para: {busca}")

        video = random.choice(videos[:5])
        arquivos = sorted(video["video_files"], key=lambda x: x.get("width", 9999))
        url_video = arquivos[0]["link"]

        log.info(f"Baixando vídeo de fundo: {url_video[:60]}...")
        r2 = await c.get(url_video, timeout=120, follow_redirects=True)
        r2.raise_for_status()
        video_path.write_bytes(r2.content)
        log.info(f"Vídeo de fundo: {video_path.stat().st_size / 1024:.0f} KB")
        return video_path

# ──────────────────────────── MONTAR VÍDEO ────────────────────────────
def montar_video(audio_path: Path, video_path: Path, titulo: str) -> Path:
    try:
        from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, ColorClip
        import moviepy
        log.info(f"MoviePy versão: {moviepy.__version__}")
    except ImportError as e:
        raise Exception(f"MoviePy não instalado: {e}")

    output_path = TMP / "video_final.mp4"

    audio = AudioFileClip(str(audio_path))
    duracao = audio.duration

    bg = VideoFileClip(str(video_path)).without_audio()
    if bg.duration < duracao:
        from moviepy import concatenate_videoclips
        repeticoes = int(duracao / bg.duration) + 2
        bg = concatenate_videoclips([bg] * repeticoes)
    bg = bg.subclipped(0, duracao)
    bg = bg.resized((720, 1280))

    overlay = ColorClip((720, 1280), color=(0, 0, 0)).with_opacity(0.45).with_duration(duracao)

    try:
        titulo_clip = (
            TextClip(
                font="DejaVu-Sans-Bold",
                text=titulo[:50] + ("..." if len(titulo) > 50 else ""),
                font_size=36,
                color="white",
                stroke_color="black",
                stroke_width=2,
                size=(660, None),
                method="caption",
            )
            .with_duration(duracao)
            .with_position(("center", 80))
        )
    except Exception:
        titulo_clip = None

    clips = [bg, overlay]
    if titulo_clip:
        clips.append(titulo_clip)

    final = CompositeVideoClip(clips).with_audio(audio)
    final.write_videofile(
        str(output_path),
        fps=24,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(TMP / "temp_audio.m4a"),
        remove_temp=True,
        logger=None,
        preset="ultrafast",
    )
    log.info(f"Vídeo final: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    return output_path

# ──────────────────────────── TELEGRAM ────────────────────────────
async def enviar_telegram_texto(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as c:
        await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

async def enviar_video_telegram(video_path: Path, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    with open(video_path, "rb") as f:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"video": f})
            if r.status_code == 200:
                log.info("Vídeo enviado com sucesso pelo Telegram!")
            else:
                log.error(f"Erro Telegram: {r.text[:300]}")

async def enviar_documento_telegram(doc_path: Path, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(doc_path, "rb") as f:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"document": f})
            if r.status_code == 200:
                log.info("Vídeo enviado como documento!")
            else:
                log.error(f"Erro Telegram documento: {r.text[:300]}")

# ──────────────────────────── PIPELINE ────────────────────────────
async def pipeline_tiktok():
    log.info("=== Iniciando pipeline TikTok ===")
    tema = random.choice(TEMAS)
    log.info(f"Tema escolhido: {tema['titulo']}")

    await enviar_telegram_texto(f"🎬 *Gerando vídeo TikTok...*\n\n📌 *{tema['titulo']}*\n\n⏳ Aguarde ~2 minutos...")

    try:
        log.info("Gerando roteiro...")
        roteiro = await gerar_roteiro(tema)

        log.info("Gerando narração...")
        audio_path = await gerar_audio(roteiro)

        log.info("Baixando vídeo de fundo...")
        video_bg = await baixar_video_fundo(tema["busca_video"])

        log.info("Montando vídeo final...")
        video_final = montar_video(audio_path, video_bg, tema["titulo"])

        caption = (
            f"🎬 *{tema['titulo']}*\n\n"
            f"📋 *Roteiro:*\n_{roteiro[:300]}..._\n\n"
            f"✅ Vídeo pronto! Publique no TikTok agora."
        )
        tamanho_mb = video_final.stat().st_size / 1024 / 1024
        if tamanho_mb < 50:
            await enviar_video_telegram(video_final, caption)
        else:
            await enviar_documento_telegram(video_final, caption)

    except Exception as e:
        log.error(f"Erro no pipeline: {e}")
        await enviar_telegram_texto(f"❌ *Erro ao gerar vídeo:* {str(e)[:300]}")

# ──────────────────────────── AGENDADOR ────────────────────────────
async def agendador():
    if MODO_TESTE:
        log.info("MODO TESTE — rodando pipeline agora!")
        await pipeline_tiktok()
        return

    log.info(f"Bot TikTok ativo | Envio diário às {HORA_ENVIO:02d}:00 BRT")
    ultimo_dia = -1
    while True:
        agora = datetime.now()
        if agora.hour == HORA_ENVIO and agora.day != ultimo_dia:
            ultimo_dia = agora.day
            await pipeline_tiktok()
        await asyncio.sleep(30)

# ──────────────────────────── MAIN ────────────────────────────
if __name__ == "__main__":
    log.info("Bot TikTok Automático iniciando...")
    if not checar_variaveis():
        log.error("Variáveis ausentes. Configure no Railway e reinicie.")
        exit(1)
    asyncio.run(agendador())
