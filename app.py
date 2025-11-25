import os
import sqlite3
import google.generativeai as genai
from flask import Flask, render_template, request, jsonify
from docx import Document

app = Flask(__name__)

# --- KONFIGURASI ---
API_KEY = "PASTE_API_KEY_DISINI" # GANTI DENGAN API KEY ANDA
UU_FOLDER = "UU"
DB_NAME = "history.db"

# Konfigurasi Gemini
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Menggunakan model Flash (cepat & konteks besar)

# Variabel global untuk menyimpan teks Undang-Undang agar tidak dibaca berulang kali
LEGAL_CONTEXT = ""

# --- FUNGSI DATABASE ---
def init_db():
    """Membuat tabel database jika belum ada."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Tabel untuk sesi chat
    c.execute('''CREATE TABLE IF NOT EXISTS chats 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Tabel untuk pesan dalam chat
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender TEXT, content TEXT, 
                 FOREIGN KEY(chat_id) REFERENCES chats(id))''')
    conn.commit()
    conn.close()

# --- FUNGSI MEMBACA WORD (DOCX) ---
def load_laws_from_folder():
    """Membaca semua file .docx di folder UU dan menggabungkannya menjadi satu teks."""
    global LEGAL_CONTEXT
    combined_text = ""
    
    if not os.path.exists(UU_FOLDER):
        os.makedirs(UU_FOLDER)
        print(f"Folder '{UU_FOLDER}' dibuat. Silakan masukkan file .docx.")
        return

    print("Sedang membaca file Undang-Undang...")
    files = [f for f in os.listdir(UU_FOLDER) if f.endswith('.docx')]
    
    if not files:
        print("Tidak ada file .docx ditemukan di folder UU.")
        LEGAL_CONTEXT = "Belum ada undang-undang yang dimuat."
        return

    for filename in files:
        path = os.path.join(UU_FOLDER, filename)
        try:
            doc = Document(path)
            file_text = f"\n--- SUMBER: {filename} ---\n"
            for para in doc.paragraphs:
                if para.text.strip():
                    file_text += para.text + "\n"
            combined_text += file_text
            print(f"Berhasil memuat: {filename}")
        except Exception as e:
            print(f"Gagal memuat {filename}: {e}")
    
    LEGAL_CONTEXT = combined_text
    print("Selesai memuat semua Undang-Undang.")

# --- ROUTES (FUNGSI WEBSITE) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/new_chat', methods=['POST'])
def new_chat():
    """Membuat sesi chat baru."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO chats (title) VALUES (?)", ("Chat Baru",))
    chat_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({"chat_id": chat_id})

@app.route('/api/chat', methods=['POST'])
def chat():
    """Mengirim pesan ke Gemini dengan konteks hukum."""
    data = request.json
    user_message = data.get('message')
    chat_id = data.get('chat_id')

    if not user_message or not chat_id:
        return jsonify({"error": "Data tidak lengkap"}), 400

    # 1. Simpan pesan user ke DB
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, sender, content) VALUES (?, ?, ?)", (chat_id, 'user', user_message))
    
    # Update judul chat jika ini pesan pertama
    c.execute("SELECT count(*) FROM messages WHERE chat_id = ?", (chat_id,))
    count = c.fetchone()[0]
    if count == 1:
        # Buat judul pendek dari pesan pertama (maks 30 char)
        short_title = (user_message[:30] + '..') if len(user_message) > 30 else user_message
        c.execute("UPDATE chats SET title = ? WHERE id = ?", (short_title, chat_id))
    
    conn.commit()

    # 2. Siapkan Prompt untuk Gemini (RAG Sederhana)
    # Kita menyuntikkan isi file Word ke dalam sistem prompt
    system_instruction = f"""
    Anda adalah Asisten Hukum AI Profesional.
    Tugas Anda adalah menjawab pertanyaan pengguna HANYA berdasarkan konteks hukum berikut ini.
    Jika jawaban tidak ditemukan dalam konteks, katakan "Maaf, informasi tersebut tidak ditemukan dalam dokumen Undang-Undang yang tersedia."
    Sebutkan nama file sumber jika memungkinkan.

    KONTEKS HUKUM:
    {LEGAL_CONTEXT}
    """
    
    try:
        # Mengirim chat ke Gemini
        # Catatan: Untuk aplikasi produksi besar, kita harus memotong history agar tidak terlalu panjang.
        # Di sini kita kirim pesan saat ini + konteks sistem.
        chat_session = model.start_chat(history=[
            {"role": "user", "parts": [system_instruction]},
            {"role": "model", "parts": ["Dimengerti. Saya akan menjawab berdasarkan konteks undang-undang yang diberikan."]},
        ])
        
        response = chat_session.send_message(user_message)
        bot_reply = response.text

    except Exception as e:
        bot_reply = f"Terjadi kesalahan koneksi ke AI: {str(e)}"

    # 3. Simpan respon bot ke DB
    c.execute("INSERT INTO messages (chat_id, sender, content) VALUES (?, ?, ?)", (chat_id, 'bot', bot_reply))
    conn.commit()
    conn.close()

    return jsonify({"response": bot_reply})

@app.route('/api/history', methods=['GET'])
def get_history():
    """Mengambil daftar riwayat chat."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title FROM chats ORDER BY id DESC")
    chats = [{"id": row[0], "title": row[1]} for row in c.fetchall()]
    conn.close()
    return jsonify(chats)

@app.route('/api/history/<int:chat_id>', methods=['GET'])
def get_chat_detail(chat_id):
    """Mengambil isi pesan dari satu sesi chat."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT sender, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    messages = [{"sender": row[0], "content": row[1]} for row in c.fetchall()]
    conn.close()
    return jsonify(messages)

@app.route('/api/search', methods=['POST'])
def search_history():
    """Mencari history chat berdasarkan keyword."""
    keyword = request.json.get('keyword', '')
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Mencari di judul chat atau isi pesan
    query = f"%{keyword}%"
    c.execute('''
        SELECT DISTINCT c.id, c.title 
        FROM chats c
        JOIN messages m ON c.id = m.chat_id
        WHERE c.title LIKE ? OR m.content LIKE ?
        ORDER BY c.id DESC
    ''', (query, query))
    results = [{"id": row[0], "title": row[1]} for row in c.fetchall()]
    conn.close()
    return jsonify(results)

if __name__ == '__main__':
    init_db()
    load_laws_from_folder() # Baca file saat aplikasi mulai
    app.run(debug=True, port=5000)
