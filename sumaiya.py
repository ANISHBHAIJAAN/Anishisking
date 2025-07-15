import os
import time
import asyncio
from telegram import Update, Document
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters, CommandHandler
import telegram.error
from uptime_server import keep_alive  # Flask uptime server

# ========== CONFIG ==========
BOT_TOKEN = "7494438232:AAE2MuN7BJDK7Z69tTyMG5D6Dz923GsLvXU"
GROUP_IDS = [-1002452745360, -1002753284196]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBFINDER_PATH = "/usr/local/bin/subfinder"
TEMP_DIR = os.path.join(BASE_DIR, "temp")
FINAL_DIR = os.path.join(BASE_DIR, "output")
MAX_CONCURRENT = 50
NUM_PARALLEL_PROCESSES = 3
CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB for splitting
SEND_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB for sending
# =============================

running_jobs = {}
bot_active = True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 𝗔𝗡𝗜𝗦𝗛 𝗣𝗔𝗜𝗗 𝗦𝗘𝗥𝗩𝗘𝗥 🔥")
    await update.message.reply_text("🚀 Taiyar hoon! Ek .txt file bhejo Anish jaan baki sab pagla !")

async def toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    message = update.message.text.lower().strip()
    if "sumaiya chup ho jao" in message:
        bot_active = False
        await update.message.reply_text("🤐 Sumaiya is now silent.")
    elif "sumaiya chalu ho jao" in message:
        bot_active = True
        await update.message.reply_text("😄 Sumaiya is back online!")

async def handle_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not bot_active:
        await update.message.reply_text("🤖 Bot is currently paused.")
        return

    document: Document = update.message.document
    if not document.file_name.endswith(".txt"):
        await update.message.reply_text("❗ Sirf .txt file bhejo!")
        return

    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(FINAL_DIR, exist_ok=True)

    base_name = os.path.splitext(document.file_name)[0]
    file_path = os.path.join(TEMP_DIR, document.file_name)

    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(custom_path=file_path)

    part_files = []
    part_count = 1
    current_size = 0
    part_lines = []

    with open(file_path, "r") as original:
        for line in original:
            current_size += len(line.encode())
            part_lines.append(line)
            if current_size >= CHUNK_SIZE:
                part_name = os.path.join(TEMP_DIR, f"{base_name}_part{part_count}.txt")
                with open(part_name, "w") as part_file:
                    part_file.writelines(part_lines)
                part_files.append(part_name)
                part_lines = []
                current_size = 0
                part_count += 1

    if part_lines:
        part_name = os.path.join(TEMP_DIR, f"{base_name}_part{part_count}.txt")
        with open(part_name, "w") as part_file:
            part_file.writelines(part_lines)
        part_files.append(part_name)

    await update.message.reply_text(f"✅ File split into {len(part_files)} parts.")

    for idx, part_path in enumerate(part_files, 1):
        await update.message.reply_text(f"⚙️ Processing Part {idx}...")
        new_filename = os.path.basename(part_path).replace(".txt", "_subdomains.txt")
        job_id = f"{update.message.chat_id}_{time.time()}_{idx}"
        running_jobs[job_id] = {"total": 0, "done": 0, "results": {}}
        asyncio.create_task(process_domains(job_id, update, context, part_path, new_filename))

async def run_subfinder(domain, sem, job_id, update):
    async with sem:
        while True:
            try:
                process = await asyncio.create_subprocess_exec(
                    SUBFINDER_PATH, "-d", domain,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    raise Exception(stderr.decode().strip())
                subs = stdout.decode().strip().splitlines()
                running_jobs[job_id]["results"][domain] = subs
                break
            except Exception as e:
                await update.message.reply_text(f"🔁 {domain} failed, retrying... Error: {str(e)}")
                await asyncio.sleep(3)
        running_jobs[job_id]["done"] += 1

async def process_domains(job_id, update, context, file_path, new_filename):
    start_time = time.time()
    progress_msg = await update.message.reply_text("🧠 Processing started...🔥")

    with open(file_path, "r") as f:
        domains = [d.strip() for d in f if d.strip()]

    running_jobs[job_id]["total"] = len(domains)
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    domain_batches = [domains[i::NUM_PARALLEL_PROCESSES] for i in range(NUM_PARALLEL_PROCESSES)]
    tasks = [run_subfinder(domain, sem, job_id, update) for batch in domain_batches for domain in batch]

    async def update_progress():
        bar_length = 20
        while running_jobs[job_id]["done"] < running_jobs[job_id]["total"]:
            await asyncio.sleep(8)
            done = running_jobs[job_id]["done"]
            total = running_jobs[job_id]["total"]
            percent = int((done / total) * 100) if total else 0
            blocks = int(percent / 5)
            progress_bar = "█" * blocks + "░" * (bar_length - blocks)
            msg = f"👮 Progress\n`[{progress_bar}]` {percent}%\n📄 {done}/{total}"
            try:
                await progress_msg.edit_text(msg, parse_mode='Markdown')
            except Exception:
                pass

    await asyncio.gather(update_progress(), *tasks)

    # Save results to temp file
    output_file_path = os.path.join(FINAL_DIR, new_filename)
    with open(output_file_path, "w") as out:
        for domain, subs in running_jobs[job_id]["results"].items():
            out.write("\n".join(subs) + "\n")

    await update.message.reply_text("✅ Done! Sending file in chunks...")

    # Send in chunks
    def split_output_file(filepath):
        chunks = []
        with open(filepath, "rb") as f:
            while True:
                data = f.read(SEND_CHUNK_SIZE)
                if not data:
                    break
                chunks.append(data)
        return chunks

    chunks = split_output_file(output_file_path)
    for i, chunk in enumerate(chunks, 1):
        temp_chunk_path = os.path.join(FINAL_DIR, f"{new_filename}_part{i}.txt")
        with open(temp_chunk_path, "wb") as f:
            f.write(chunk)
        for group_id in GROUP_IDS:
            try:
                await context.bot.send_document(
                    chat_id=group_id,
                    document=open(temp_chunk_path, "rb"),
                    filename=f"{new_filename}_part{i}.txt",
                    caption=f"📦 Part {i}/{len(chunks)}",
                    parse_mode='Markdown')
            except Exception as e:
                await update.message.reply_text(f"⚠️ Error sending part {i}: {str(e)}")

    del running_jobs[job_id]

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_txt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, toggle_bot))
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    main()