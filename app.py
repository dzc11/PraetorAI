import os
import sqlite3
import google.generativeai as genai
from flask import Flask, render_template, request, jsonify
from docx import Document
import PyPDF2  # Library untuk membaca PDF

app = Flask(__name__)

# ==========================================
# KONFIGURASI
# ==========================================
# Masukkan API Key Google Gemini Anda di sini
API_KEY = "GANTI_DENGAN_API_KEY_ANDA"

# Nama folder tempat menyimpan file UU
UU_FOLDER = "UU"

# Nama database lokal untuk history
DB_NAME = "history.db"

# Konfigurasi Gemini
genai.configure(api_key=API_KEY)
# Menggunakan model Flash agar cepat dan hemat token
model = genai.GenerativeModel('gemini-1.5-flash')

# Variabel Global untuk menyimpan teks UU di memori (RAM)
LEGAL_CONTEXT = ""

# ==========================================
# BAGIAN DATABASE (SQLITE)
# ==========================================
def init_db():
    """Inisialisasi database dan tabel jika belum ada."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Tabel untuk menyimpan sesi chat (judul, waktu)
    c.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            title TEXT, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabel untuk menyimpan detail pesan (user & bot)
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            chat_id INTEGER, 
            sender TEXT, 
            content TEXT, 
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# ==========================================
# BAGIAN PEMBACA FILE (PDF & WORD)
# ==========================================
def load_laws_from_folder():
    """Membaca semua file .docx dan .pdf dari folder UU."""
    global LEGAL_CONTEXT
    combined_text = ""
    
    # Buat folder jika belum ada
    if not os.path.exists(UU_FOLDER):
        os.makedirs(UU_FOLDER)
        print(f"[INFO] Folder '{UU_FOLDER}' dibuat. Silakan masukkan file PDF/Word ke dalamnya.")
        return

    print("[INFO] Sedang memindai dan membaca dokumen hukum...")
    files = os.listdir(UU_FOLDER)
    
    if not files:
        print("[WARN] Folder UU kosong. AI tidak memiliki konteks hukum.")
        LEGAL_CONTEXT = "Belum ada dokumen undang-undang yang dimuat."
        return

    loaded_files = 0
    
    for filename in files:
        file_path = os.path.join(UU_FOLDER, filename)
        file_content = ""
        
        try:
            # --- JIKA FILE WORD (.docx) ---
            if filename.lower().endswith('.docx'):
                doc = Document(file_path)
                for para in doc.paragraphs:
                    if para.text.strip():
                        file_content += para.text + "\n"
                loaded_files += 1
                print(f"  [OK] Membaca Word: {filename}")

            # --- JIKA FILE PDF (.pdf) ---
            elif filename.lower().endswith('.pdf'):
                with open(file_path, 'rb') as pdf_file:
                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                    # Loop semua halaman
                    for page in pdf_reader.pages:
                        text = page.extract_text()
                        if text:
                            file_content += text + "\n"
                loaded_files += 1
                print(f"  [OK] Membaca PDF: {filename}")
            
            # Lewati file lain (misal .txt atau .jpg)
            else:
                continue

            # Gabungkan ke konteks global dengan penanda nama file
            if file_content:
                combined_text += f"\n\n=== SUMBER DOKUMEN: {filename} ===\n{file_content}\n"

        except Exception as e:
            print(f"  [ERR] Gagal membaca {filename}: {e}")
    
    LEGAL_CONTEXT = combined_text
    print(f"[INFO] Selesai. Total {loaded_files} dokumen dimuat ke memori.")

# ==========================================
# ROUTES FLASK (API & WEB)
# ==========================================

@app.route('/')
def index():
    """Halaman utama."""
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
    """Endpoint utama untuk mengirim pesan ke AI."""
    data = request.json
    user_message = data.get('message')
    chat_id = data.get('chat_id')

    if not user_message or not chat_id:
        return jsonify({"error": "Pesan atau Chat ID kosong"}), 400

    # 1. Simpan pesan USER ke Database
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, sender, content) VALUES (?, ?, ?)", (chat_id, 'user', user_message))
    
    # Update judul chat otomatis berdasarkan pesan pertama
    c.execute("SELECT count(*) FROM messages WHERE chat_id = ?", (chat_id,))
    if c.fetchone()[0] == 1:
        # Ambil 30 karakter pertama untuk judul
        new_title = (user_message[:30] + '...') if len(user_message) > 30 else user_message
        c.execute("UPDATE chats SET title = ? WHERE id = ?", (new_title, chat_id))
    
    conn.commit()

    # 2. Proses AI (Gemini)
    # Prompt System: Menginstruksikan AI bertindak sebagai ahli hukum dengan konteks file
    system_prompt = f"""
    ROLE: Anda adalah AI Legal Assistant yang ahli dalam Hukum Indonesia.
    
    TUGAS: Jawab pertanyaan pengguna HANYA berdasarkan konteks dokumen hukum yang diberikan di bawah ini.
    
    ATURAN:
    1. Jika jawaban ada di dalam konteks, jawab dengan jelas dan sebutkan nama dokumen sumbernya jika mungkin.
    2. Jika jawaban TIDAK ADA di dalam konteks, katakan dengan jujur: "Maaf, informasi tersebut tidak ditemukan dalam dokumen yang tersedia." jangan mengarang hukum.
    3. Gunakan bahasa Indonesia yang formal dan sopan.

    KONTEKS HUKUM (DATABASE):
    {LEGAL_CONTEXT}
    """

    try:
        # Memulai chat session dengan Gemini (Stateless request untuk hemat token di backend sederhana)
        response = model.generate_content([
            system_prompt,
            f"User: {user_message}",
            "Assistant:"
        ])
        
        bot_reply = response.text.strip()

    except Exception as e:
        bot_reply = f"Maaf, terjadi kesalahan pada server AI: {str(e)}"
        print(f"[API ERROR] {e}")

    # 3. Simpan balasan BOT ke Database
    c.execute("INSERT INTO messages (chat_id, sender, content) VALUES (?, ?, ?)", (chat_id, 'bot', bot_reply))
    conn.commit()
    conn.close()

    return jsonify({"response": bot_reply})

@app.route('/api/history', methods=['GET'])
def get_history():
    """Mengambil daftar semua chat history."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title FROM chats ORDER BY id DESC")
    chats = [{"id": r[0], "title": r[1]} for r in c.fetchall()]
    conn.close()
    return jsonify(chats)

@app.route('/api/history/<int:chat_id>', methods=['GET'])
def get_chat_detail(chat_id):
    """Mengambil detail pesan dari satu sesi chat."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT sender, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    messages = [{"sender": r[0], "content": r[1]} for r in c.fetchall()]
    conn.close()
    return jsonify(messages)

@app.route('/api/search', methods=['POST'])
def search_history():
    """Mencari riwayat chat berdasarkan keyword."""
    keyword = request.json.get('keyword', '')
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    search_query = f"%{keyword}%"
    
    # Query join untuk mencari di judul chat ATAU isi pesan
    query = '''
        SELECT DISTINCT c.id, c.title 
        FROM chats c
        JOIN messages m ON c.id = m.chat_id
        WHERE c.title LIKE ? OR m.content LIKE ?
        ORDER BY c.id DESC
    '''
    
    c.execute(query, (search_query, search_query))
    results = [{"id": r[0], "title": r[1]} for r in c.fetchall()]
    conn.close()
    return jsonify(results)

# ==========================================
# MAIN ENTRY POINT
# ==========================================
if __name__ == '__main__':
    # 1. Inisialisasi Database
    init_db()
    
    # 2. Baca file UU ke Memori
    load_laws_from_folder()
    
    # 3. Jalankan Aplikasi
    print("[INFO] Aplikasi berjalan di [http://127.0.0.1:5000](http://127.0.0.1:5000)")
    app.run(debug=True, port=5000)

