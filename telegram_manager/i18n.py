"""Multi-language support for the bot."""

LANGUAGES = {
    "en": "English",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "th": "ภาษาไทย",
    "vi": "Tiếng Việt",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "hi": "हिन्दी",
    "fil": "Filipino",
}

_STRINGS = {
    "welcome_new": {
        "en": "Hi {name},\n\nI can help you create and manage Telegram userbots for broadcast, saved text templates, group lists, OTP checks, and account tools.\n\nUsername: {username}\nUser ID: {telegram_id}\nFirst Name: {first_name}\nLast Name: {last_name}\nStatus: {status}\nUserbot accounts: {n}\n\nTo create your first userbot, press \"Add Account\" and enter your Telegram phone number in international format, for example +628123456789.",
        "id": "Hi {name},\n\nSaya bisa membantu kamu membuat dan mengelola userbot Telegram untuk broadcast, template text, daftar group, cek OTP, dan pengaturan akun.\n\nUsername: {username}\nUser ID: {telegram_id}\nFirst Name: {first_name}\nLast Name: {last_name}\nStatus: {status}\nAkun userbot: {n}\n\nUntuk membuat userbot baru, tekan tombol \"Tambah Akun\" lalu masukkan nomor Telegram dengan format internasional, contoh +628123456789.",
        "ms": "Selamat datang! Anda perlu login akaun dahulu.\n\nMasukkan nombor telefon (cth: +60123456789):",
        "th": "ยินดีต้อนรับ! คุณต้องเข้าสู่ระบบก่อน\n\nกรอกหมายเลขโทรศัพท์ (เช่น +66812345678):",
        "vi": "Chào mừng! Bạn cần đăng nhập tài khoản trước.\n\nNhập số điện thoại (VD: +84912345678):",
        "zh": "欢迎！请先登录账号。\n\n输入手机号（例：+8613812345678）：",
        "ja": "ようこそ！まずアカウントにログインしてください。\n\n電話番号を入力（例：+819012345678）：",
        "ko": "환영합니다! 먼저 계정에 로그인하세요.\n\n전화번호 입력 (예: +821012345678):",
        "hi": "स्वागत है! पहले अकाउंट लॉगिन करें।\n\nफ़ोन नंबर दर्ज करें (जैसे: +919812345678):",
        "fil": "Maligayang pagdating! Mag-login muna ng account.\n\nIlagay ang phone number (hal: +639123456789):",
    },
    "main_menu": {
        "en": "Telegram Manager ({n} accounts)\n\nChoose a menu below to manage accounts, saved text templates, group lists, or broadcasts.",
        "id": "Telegram Manager ({n} akun)\n\nPilih menu di bawah untuk mengelola akun, template text, daftar group, atau broadcast.",
        "ms": "Telegram Manager ({n} akaun)",
        "th": "Telegram Manager ({n} บัญชี)",
        "vi": "Telegram Manager ({n} tài khoản)",
        "zh": "Telegram Manager ({n} 个账号)",
        "ja": "Telegram Manager ({n} アカウント)",
        "ko": "Telegram Manager ({n} 계정)",
        "hi": "Telegram Manager ({n} अकाउंट)",
        "fil": "Telegram Manager ({n} account)",
    },
    "enter_phone": {
        "en": "Create a userbot account\n\nEnter the Telegram phone number you want to connect. Use international format, for example +628123456789.",
        "id": "Buat akun userbot\n\nMasukkan nomor Telegram yang mau dihubungkan. Gunakan format internasional, contoh +628123456789.",
        "ms": "Masukkan nombor telefon:",
        "th": "กรอกหมายเลขโทรศัพท์:",
        "vi": "Nhập số điện thoại:",
        "zh": "输入手机号：",
        "ja": "電話番号を入力：",
        "ko": "전화번호 입력:",
        "hi": "फ़ोन नंबर दर्ज करें:",
        "fil": "Ilagay ang phone number:",
    },
    "code_sent": {
        "en": "Code sent to {phone}.\nDevice: {device}\n\nEnter the 5-digit code:",
        "id": "Kode dikirim ke {phone}.\nDevice: {device}\n\nMasukkan kode 5 digit:",
        "ms": "Kod dihantar ke {phone}.\nDevice: {device}\n\nMasukkan kod 5 digit:",
        "th": "ส่งรหัสไปที่ {phone}\nDevice: {device}\n\nกรอกรหัส 5 หลัก:",
        "vi": "Mã đã gửi đến {phone}.\nDevice: {device}\n\nNhập mã 5 chữ số:",
        "zh": "验证码已发送至 {phone}。\n设备：{device}\n\n输入5位验证码：",
        "ja": "コードを{phone}に送信しました。\nデバイス：{device}\n\n5桁のコードを入力：",
        "ko": "{phone}로 코드 전송됨.\n기기: {device}\n\n5자리 코드 입력:",
        "hi": "{phone} पर कोड भेजा गया।\nDevice: {device}\n\n5 अंकों का कोड दर्ज करें:",
        "fil": "Code ipinadala sa {phone}.\nDevice: {device}\n\nIlagay ang 5-digit code:",
    },
    "2fa_required": {
        "en": "2FA enabled. Enter your cloud password:",
        "id": "2FA aktif. Masukkan cloud password:",
        "ms": "2FA aktif. Masukkan kata laluan cloud:",
        "th": "เปิดใช้ 2FA กรอกรหัสผ่าน cloud:",
        "vi": "2FA đã bật. Nhập mật khẩu cloud:",
        "zh": "已启用2FA。输入云密码：",
        "ja": "2FA有効。クラウドパスワードを入力：",
        "ko": "2FA 활성화됨. 클라우드 비밀번호 입력:",
        "hi": "2FA सक्रिय। क्लाउड पासवर्ड दर्ज करें:",
        "fil": "2FA enabled. Ilagay ang cloud password:",
    },
    "no_accounts": {
        "en": "No accounts.",
        "id": "Belum ada akun.",
        "ms": "Tiada akaun.",
        "th": "ไม่มีบัญชี",
        "vi": "Chưa có tài khoản.",
        "zh": "没有账号。",
        "ja": "アカウントなし。",
        "ko": "계정 없음.",
        "hi": "कोई अकाउंट नहीं।",
        "fil": "Walang account.",
    },
    "pick_account": {
        "en": "Choose a userbot account to view details, edit profile data, check OTP, or remove it.",
        "id": "Pilih akun userbot untuk melihat detail, edit profil, cek OTP, atau menghapus akun.",
        "ms": "Pilih akaun:",
        "th": "เลือกบัญชี:",
        "vi": "Chọn tài khoản:",
        "zh": "选择账号：",
        "ja": "アカウントを選択：",
        "ko": "계정 선택:",
        "hi": "अकाउंट चुनें:",
        "fil": "Pumili ng account:",
    },
    "broadcast_pick_list": {
        "en": "Choose the group list you want to broadcast to. Group lists are managed from Manage Group; each list can contain usernames, links, invite links, or chat IDs.",
        "id": "Pilih daftar group tujuan broadcast. Daftar ini dibuat dari Manage Group dan bisa berisi username, link, invite link, atau chat ID.",
        "ms": "Pilih senarai:",
        "th": "เลือกรายการ:",
        "vi": "Chọn danh sách:",
        "zh": "选择列表：",
        "ja": "リストを選択：",
        "ko": "목록 선택:",
        "hi": "सूची चुनें:",
        "fil": "Pumili ng list:",
    },
    "broadcast_send_msg": {
        "en": "Send the message (text/media):",
        "id": "Kirim pesan (teks/media):",
        "ms": "Hantar mesej (teks/media):",
        "th": "ส่งข้อความ (ข้อความ/สื่อ):",
        "vi": "Gửi tin nhắn (văn bản/media):",
        "zh": "发送消息（文字/媒体）：",
        "ja": "メッセージを送信（テキスト/メディア）：",
        "ko": "메시지 전송 (텍스트/미디어):",
        "hi": "संदेश भेजें (टेक्स्ट/मीडिया):",
        "fil": "Ipadala ang mensahe (text/media):",
    },
    "broadcast_running": {
        "en": "Running... send 'stop' to stop",
        "id": "Berjalan... kirim 'stop' untuk berhenti",
        "ms": "Berjalan... hantar 'stop' untuk berhenti",
        "th": "กำลังทำงาน... ส่ง 'stop' เพื่อหยุด",
        "vi": "Đang chạy... gửi 'stop' để dừng",
        "zh": "运行中... 发送 'stop' 停止",
        "ja": "実行中... 'stop'で停止",
        "ko": "실행 중... 'stop' 전송으로 중지",
        "hi": "चल रहा है... 'stop' भेजें रोकने के लिए",
        "fil": "Tumatakbo... mag-send ng 'stop' para huminto",
    },
    "broadcast_stopped": {
        "en": "Broadcast stopped.",
        "id": "Broadcast dihentikan.",
        "ms": "Broadcast dihentikan.",
        "th": "หยุดการ Broadcast แล้ว",
        "vi": "Broadcast đã dừng.",
        "zh": "广播已停止。",
        "ja": "ブロードキャスト停止。",
        "ko": "브로드캐스트 중지됨.",
        "hi": "Broadcast रुक गया।",
        "fil": "Broadcast huminto na.",
    },
    "delay_mode": {
        "en": "Delay mode:\n• Per group — delay between each group\n• Per round — delay after all groups done",
        "id": "Mode delay:\n• Per group — delay antar tiap group\n• Per round — delay setelah semua group selesai",
        "ms": "Mod delay:\n• Per group — delay antara setiap group\n• Per round — delay selepas semua group selesai",
        "th": "โหมดดีเลย์:\n• Per group — ดีเลย์ระหว่างแต่ละกลุ่ม\n• Per round — ดีเลย์หลังจบทุกกลุ่ม",
        "vi": "Chế độ delay:\n• Per group — delay giữa mỗi group\n• Per round — delay sau khi xong tất cả",
        "zh": "延迟模式：\n• Per group — 每个群之间延迟\n• Per round — 所有群完成后延迟",
        "ja": "遅延モード：\n• Per group — 各グループ間\n• Per round — 全グループ完了後",
        "ko": "딜레이 모드:\n• Per group — 각 그룹 사이\n• Per round — 모든 그룹 완료 후",
        "hi": "Delay mode:\n• Per group — हर group के बीच\n• Per round — सब group के बाद",
        "fil": "Delay mode:\n• Per group — delay sa bawat group\n• Per round — delay pagkatapos ng lahat",
    },
    "delay_value": {
        "en": "Delay duration?\nPick or type custom (e.g. '5' or '3-8'):",
        "id": "Durasi delay?\nPilih atau ketik manual (misal '5' atau '3-8'):",
        "ms": "Tempoh delay?\nPilih atau taip manual (cth '5' atau '3-8'):",
        "th": "ระยะเวลาดีเลย์?\nเลือกหรือพิมพ์ (เช่น '5' หรือ '3-8'):",
        "vi": "Thời gian delay?\nChọn hoặc nhập (VD '5' hoặc '3-8'):",
        "zh": "延迟时长？\n选择或输入（如 '5' 或 '3-8'）：",
        "ja": "遅延時間？\n選択または入力（例：'5' or '3-8'）：",
        "ko": "딜레이 시간?\n선택 또는 입력 (예: '5' 또는 '3-8'):",
        "hi": "Delay duration?\nचुनें या टाइप करें (जैसे '5' या '3-8'):",
        "fil": "Delay duration?\nPumili o mag-type (hal. '5' o '3-8'):",
    },
    "lang_changed": {
        "en": "Language set to English.",
        "id": "Bahasa diubah ke Bahasa Indonesia.",
        "ms": "Bahasa ditukar ke Bahasa Melayu.",
        "th": "เปลี่ยนภาษาเป็นภาษาไทย",
        "vi": "Đã đổi ngôn ngữ sang Tiếng Việt.",
        "zh": "语言已设置为中文。",
        "ja": "言語を日本語に設定しました。",
        "ko": "언어가 한국어로 설정되었습니다.",
        "hi": "भाषा हिन्दी में बदल दी गई।",
        "fil": "Wika ay naitakda sa Filipino.",
    },
    "choose_lang": {
        "en": "Choose your language:",
        "id": "Pilih bahasa yang kamu inginkan:",
        "ms": "Pilih bahasa anda:",
        "th": "เลือกภาษาของคุณ:",
        "vi": "Chọn ngôn ngữ của bạn:",
        "zh": "选择你的语言：",
        "ja": "言語を選択してください：",
        "ko": "언어를 선택하세요:",
        "hi": "अपनी भाषा चुनें:",
        "fil": "Piliin ang iyong wika:",
    },
    "saved_text_menu": {
        "en": "Manage Text\n\nSave reusable broadcast message templates here. Press \"+ Save Text\" to create a new template, or choose an existing template to preview/delete it.",
        "id": "Kelola Text\n\nSimpan template pesan broadcast yang bisa dipakai ulang di sini. Tekan \"+ Save Text\" untuk membuat template baru, atau pilih template yang sudah ada untuk preview/hapus.",
    },
    "group_list_menu": {
        "en": "Manage Group\n\nCreate and edit target group lists for broadcasts. A list can contain @username, t.me links, private invite links, addlist links, or numeric chat IDs.",
        "id": "Manage Group\n\nBuat dan kelola daftar target group untuk broadcast. List bisa berisi @username, link t.me, invite private, addlist, atau chat ID angka.",
    },
}

# Default language
_user_lang: dict = {}


def set_lang(user_id: int, lang: str) -> None:
    _user_lang[user_id] = lang


def get_lang(user_id: int) -> str:
    return _user_lang.get(user_id, "id")


def t(key: str, user_id: int, **kwargs) -> str:
    """Get translated string."""
    lang = get_lang(user_id)
    strings = _STRINGS.get(key, {})
    text = strings.get(lang) or strings.get("en", key)
    if kwargs:
        text = text.format(**kwargs)
    return text
