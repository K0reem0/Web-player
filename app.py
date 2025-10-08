import os
import requests
from flask import Flask, request, redirect, url_for, render_template, send_from_directory, abort, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import mimetypes

# تهيئة Flask والتكوين
app = Flask(__name__)
# تكوين قاعدة بيانات SQLite. (للتذكير: لن تعمل للتخزين الدائم على Heroku)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# المجلد الذي سيتم حفظ الفيديوهات فيه
UPLOAD_FOLDER = 'uploaded_videos'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# إنشاء المجلد إذا لم يكن موجودًا
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# تعريف نموذج قاعدة البيانات لملفات الفيديو
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False, unique=True)
    title = db.Column(db.String(100), nullable=False)

# إنشاء الجداول عند بدء التشغيل
with app.app_context():
    db.create_all()

# --- وظائف المساعدة ---

def get_video_list():
    """يحضر قائمة كل الفيديوهات من قاعدة البيانات"""
    return Video.query.all()

def download_file_from_url(url, folder):
    """يحمل الملف من رابط مباشر ويحفظه"""
    try:
        # إرسال طلب للحصول على محتوى الرابط
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status() # التأكد من نجاح الطلب

        # استخراج اسم الملف من الرابط أو إنشاء اسم افتراضي
        filename = secure_filename(url.split('/')[-1])
        if not filename or '.' not in filename:
            filename = f"video_{Video.query.count() + 1}.mp4"

        # تحديد المسار الكامل للحفظ
        file_path = os.path.join(folder, filename)

        # الحفظ بشكل مجزأ لتوفير الذاكرة
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return filename
    except requests.exceptions.RequestException as e:
        print(f"Error downloading video: {e}")
        return None

# --- الـ Routes (المسارات) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """الصفحة الرئيسية: لعرض قائمة الفيديوهات وتحميل فيديو جديد"""
    if request.method == 'POST':
        video_url = request.form.get('video_url')
        video_title = request.form.get('video_title', 'Video Title')

        if video_url:
            filename = download_file_from_url(video_url, app.config['UPLOAD_FOLDER'])
            if filename:
                # حفظ معلومات الفيديو في قاعدة البيانات
                new_video = Video(filename=filename, title=video_title)
                db.session.add(new_video)
                db.session.commit()
                return redirect(url_for('index', success=True))
            else:
                return render_template('index.html', videos=get_video_list(), error="فشل التحميل. تأكد من صحة الرابط أو حاول لاحقًا.", page_title="مشغل الفيديو")

    videos = get_video_list()
    return render_template('index.html', videos=videos, page_title="مشغل الفيديو")


@app.route('/stream/<int:video_id>')
def stream(video_id):
    """مسار لتشغيل الفيديو في المتصفح"""
    video = Video.query.get_or_404(video_id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
    
    if not os.path.exists(file_path):
        # حذف الإدخال من DB إذا كان الملف غير موجود (قد يحدث على Heroku)
        db.session.delete(video)
        db.session.commit()
        return "Video file not found. It might have been deleted from the server.", 404

    # --- تنفيذ HTTP Range Streaming لتحسين الأداء ---
    range_header = request.headers.get('Range', None)
    if not range_header:
        # إذا لم يكن هناك Range Header، إرسال الملف بالكامل (عادة للمشغلين القدامى)
        return send_from_directory(app.config['UPLOAD_FOLDER'], video.filename, mimetype=mimetypes.guess_type(video.filename)[0])

    # منطق معالجة Range Headers (للتشغيل السريع والبحث داخل الفيديو)
    size = os.path.getsize(file_path)
    byte1, byte2 = 0, size - 1

    # تحليل الـ Range Header
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
        206, # 206 Partial Content هو الكود المطلوب للـ streaming
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
    return render_template('player.html', video=video, page_title=video.title)


@app.route('/delete/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    """حذف ملف الفيديو من التخزين وقاعدة البيانات"""
    video = Video.query.get_or_404(video_id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)

    try:
        # 1. حذف الملف من التخزين
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # 2. حذف الإدخال من قاعدة البيانات
        db.session.delete(video)
        db.session.commit()

        return redirect(url_for('index', deleted=True))
    except Exception as e:
        print(f"Error deleting file: {e}")
        return redirect(url_for('index', error="حدث خطأ أثناء الحذف."))


if __name__ == '__main__':
    app.run(debug=True)

