Import os
import requests
import json
import threading
import time
from flask import Flask, request, redirect, url_for, render_template, send_from_directory, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import mimetypes

# تهيئة Flask والتكوين
app = Flask(__name__)
# تكوين قاعدة بيانات SQLite.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# المجلد الذي سيتم حفظ الفيديوهات فيه
UPLOAD_FOLDER = 'uploaded_videos'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# إنشاء المجلد إذا لم يكن موجودًا
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# قاموس عالمي لتتبع حالة التحميل بالبايت في الذاكرة
DOWNLOAD_STATE = {}

# تعريف نموذج قاعدة البيانات لملفات الفيديو
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=True, unique=True)
    title = db.Column(db.String(100), nullable=False)
    progress = db.Column(db.Integer, default=0)    # 0: قيد الإعداد، 100: اكتمل، -1: خطأ
    total_size = db.Column(db.Integer, default=0) # لتخزين حجم الملف بالبايت
    start_time = db.Column(db.Float, nullable=True) # وقت بدء التحميل الفعلي

# إنشاء الجداول عند بدء التشغيل
with app.app_context():
    db.create_all()

# --- وظائف المساعدة (المعدلة) ---

def get_video_list():
    """يحضر قائمة كل الفيديوهات من قاعدة البيانات"""
    return Video.query.order_by(Video.id.desc()).all()

def parse_cookies(cookie_string):
    """
    تحويل سلسلة الكوكيز (التي قد تكون بتنسيق HTTP أو Netscape) إلى قاموس Python.
    المدخل: 'key1=value1; key2=value2' أو محتوى ملف Netscape
    المخرج: {'key1': 'value1', 'key2': 'value2'}
    """
    cookies = {}
    if not cookie_string:
        return cookies

    # محاولة معالجة التنسيق البسيط (HTTP Header style)
    # إذا كان يحتوي على ;، نفترض أنه تنسيق بسيط في البداية
    if ';' in cookie_string and '\n' not in cookie_string:
        try:
            for pair in cookie_string.split(';'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    cookies[key.strip()] = value.strip()
            if cookies:
                return cookies
        except:
            pass # في حالة الفشل، نواصل محاولة المعالجة كتنسيق ملف

    # معالجة تنسيق ملف Netscape أو قوائم key=value مفصولة بأسطر
    for line in cookie_string.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if '\t' in line:
            # تنسيق Netscape: domain	flag	path	secure	expiration	name	value
            parts = line.split('\t')
            if len(parts) >= 7:
                name = parts[5].strip()
                value = parts[6].strip()
                if name and value:
                    cookies[name] = value
        
        elif '=' in line:
            # تنسيق key=value بسيط
            try:
                key, value = line.split('=', 1)
                # إزالة أي شيء بعد أول ; إذا كان هناك (مثل ;domain=...)
                value_clean = value.strip().split(';')[0]
                cookies[key.strip()] = value_clean
            except:
                continue

    return cookies


def download_file_from_url(url, folder, video_id, cookies_dict=None): # <--- تعديل: إضافة وسيطة cookies_dict
    """يحمل الملف من رابط مباشر ويحفظه، ويحدث الذاكرة فقط أثناء التحميل"""
    global DOWNLOAD_STATE
    
    with app.app_context():
        video = Video.query.get(video_id)
        if not video: 
            if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]
            return

        try:
            # **التعديل هنا:** تمرير قاموس الكوكيز إلى الطلب
            response = requests.get(
                url, 
                stream=True, 
                timeout=300, 
                cookies=cookies_dict if cookies_dict else {}
            ) 
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))

            filename = secure_filename(url.split('/')[-1])
            if not filename or '.' not in filename:
                filename = f"video_{video_id}.mp4"

            file_path = os.path.join(folder, filename)
            downloaded_size = 0
            
            # 1. Commit للمعلومات الأساسية مرة واحدة
            video.filename = filename
            video.total_size = total_size
            video.progress = 1 # تغيير الحالة إلى 'بدأ التحميل فعلياً'
            db.session.commit()

            # الحفظ بشكل مجزأ وتحديث الذاكرة
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if video.progress == -1: break 
                    
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    # تحديث الذاكرة فقط في كل خطوة
                    DOWNLOAD_STATE[video_id] = downloaded_size 

            # 2. Commit نهائي عند الانتهاء
            if video.progress != -1:
                video.progress = 100
                db.session.commit()
            
            # تنظيف الذاكرة بعد الانتهاء
            if video_id in DOWNLOAD_STATE:
                del DOWNLOAD_STATE[video_id]

        except requests.exceptions.RequestException as e:
            print(f"Error downloading video ID {video_id}: {e}")
            video.progress = -1
            video.filename = None 
            db.session.commit()
            if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]
        except Exception as e:
            print(f"An unexpected error occurred for video ID {video_id}: {e}")
            video.progress = -1
            video.filename = None 
            db.session.commit()
            if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]


# --- الـ Routes (المسارات) (المعدلة) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """الصفحة الرئيسية: لعرض قائمة الفيديوهات وبدء التحميل"""
    global DOWNLOAD_STATE
    
    if request.method == 'POST':
        video_url = request.form.get('video_url')
        video_title = request.form.get('video_title', 'فيديو جديد')
        cookies_str = request.form.get('cookies_data', '') # <--- جديد: استلام بيانات الكوكيز

        if video_url:
            
            # تحويل سلسلة الكوكيز إلى قاموس
            cookies_dict = parse_cookies(cookies_str)
            
            # 1. إنشاء إدخال مؤقت في قاعدة البيانات وتسجيل وقت البدء
            current_time = time.time()
            new_video = Video(
                title=video_title, 
                progress=0, 
                filename=None, 
                total_size=0,
                start_time=current_time 
            )
            db.session.add(new_video)
            db.session.commit()
            
            # تهيئة حالة التحميل في الذاكرة
            DOWNLOAD_STATE[new_video.id] = 0
            
            # 2. تشغيل عملية التحميل في خلفية منفصلة مع تمرير الكوكيز
            threading.Thread(
                target=download_file_from_url, 
                args=(video_url, app.config['UPLOAD_FOLDER'], new_video.id, cookies_dict) # <--- تعديل: تمرير cookies_dict
            ).start()

            return redirect(url_for('index', started_download=new_video.id))

    videos = get_video_list()
    return render_template('index.html', videos=videos, page_title="مشغل الفيديو")

@app.route('/status/<int:video_id>')
def download_status(video_id):
    """مسار AJAX لإرجاع حالة التقدم التقديرية، السرعة، والوقت التقديري"""
    global DOWNLOAD_STATE
    video = Video.query.get(video_id)
    
    if video:
        progress_db = video.progress
        
        if progress_db == 100 or progress_db == -1:
            return jsonify({
                'progress': progress_db,
                'title': video.title,
                'file_ready': progress_db == 100,
                'error': progress_db == -1,
                'speed_kbps': 0,
                'eta_seconds': None
            })

        # --- حساب السرعة والوقت التقديري (بناءً على الذاكرة) ---
        
        downloaded_size = DOWNLOAD_STATE.get(video_id, 0) 
        total_size = video.total_size
        start_time = video.start_time
        
        speed_kbps = 0           
        eta_seconds = None      
        progress_estimate = 0
        
        if total_size > 0 and start_time:
            elapsed_time = time.time() - start_time
            
            if elapsed_time > 0 and downloaded_size > 0:
                progress_estimate = int((downloaded_size / total_size) * 100)
                speed_bps = downloaded_size / elapsed_time
                speed_kbps = round(speed_bps / 1024, 2)
                
                remaining_size = total_size - downloaded_size
                if speed_bps > 0:
                    eta_seconds = remaining_size / speed_bps

        # --- إرجاع النتائج التقديرية ---
        return jsonify({
            'progress': progress_estimate, 
            'title': video.title,
            'file_ready': False,
            'error': progress_db == -1,
            'speed_kbps': speed_kbps,
            'eta_seconds': int(eta_seconds) if eta_seconds is not None else None
        })

    return jsonify({'error': True, 'message': 'Video not found'}), 404

# ... (باقي المسارات play, stream, delete_video بدون تغيير) ...

@app.route('/stream/<int:video_id>')
def stream(video_id):
    """مسار لتشغيل الفيديو في المتصفح (مع Range Headers لـ Streaming سريع)"""
    video = Video.query.get_or_404(video_id)
    
    if not video.filename or video.progress != 100:
        return "Video not ready for streaming.", 404
        
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
    
    if not os.path.exists(file_path):
        db.session.delete(video)
        db.session.commit()
        return "Video file not found. It might have been deleted from the server.", 404

    range_header = request.headers.get('Range', None)
    if not range_header:
        return send_from_directory(app.config['UPLOAD_FOLDER'], video.filename, mimetype=mimetypes.guess_type(video.filename)[0])

    size = os.path.getsize(file_path)
    byte1, byte2 = 0, size - 1

    m = range_header.replace('bytes=', '').split('-')
    try:
        byte1 = int(m[0])
        if len(m) > 1 and m[1]:
            byte2 = int(m[1])
    except ValueError:
        return 'Invalid Range Header', 416

    length = byte2 - byte1 + 1
    
    with open(file_path, 'rb') as f:
        f.seek(byte1)
        data = f.read(length)

    rv = Response(
        data,
        206, 
        mimetype=mimetypes.guess_type(video.filename)[0],
        direct_passthrough=True
    )
    rv.headers.add('Content-Range', 'bytes {0}-{1}/{2}'.format(byte1, byte2, size))
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv


@app.route('/play/<int:video_id>')
def play(video_id):
    """صفحة المشغل لعرض الفيديو"""
    video = Video.query.get_or_404(video_id)
    if video.progress != 100:
         return redirect(url_for('index', error="الفيديو غير جاهز للتشغيل بعد!"))
         
    return render_template('player.html', video=video, page_title=video.title)


@app.route('/delete/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    """حذف ملف الفيديو من التخزين وقاعدة البيانات"""
    global DOWNLOAD_STATE
    video = Video.query.get_or_404(video_id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
    
    if video_id in DOWNLOAD_STATE:
        del DOWNLOAD_STATE[video_id]
    
    if video.progress < 100 and video.progress != -1:
        video.progress = -1
        db.session.commit()

    try:
        if video.filename and os.path.exists(file_path):
            os.remove(file_path)
        
        db.session.delete(video)
        db.session.commit()

        return redirect(url_for('index', deleted=True))
    except Exception as e:
        print(f"Error deleting file: {e}")
        return redirect(url_for('index', error="حدث خطأ أثناء الحذف."))


if __name__ == '__main__':
    app.run(debug=True)
