import os
import requests
import json
import threading
from flask import Flask, request, redirect, url_for, render_template, send_from_directory, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import mimetypes

# تهيئة Flask والتكوين
app = Flask(__name__)
# تكوين قاعدة بيانات SQLite. (للتذكير: التخزين غير دائم على Heroku)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///videos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# المجلد الذي سيتم حفظ الفيديوهات فيه
UPLOAD_FOLDER = 'uploaded_videos'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# إنشاء المجلد إذا لم يكن موجودًا
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# تعريف نموذج قاعدة البيانات لملفات الفيديو (تم إضافة progress و total_size)
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=True, unique=True)
    title = db.Column(db.String(100), nullable=False)
    progress = db.Column(db.Integer, default=0)    # 0-100 أو -1 للخطأ
    total_size = db.Column(db.Integer, default=0) # لتخزين حجم الملف بالبايت

# إنشاء الجداول عند بدء التشغيل
with app.app_context():
    db.create_all()

# --- وظائف المساعدة ---

def get_video_list():
    """يحضر قائمة كل الفيديوهات من قاعدة البيانات"""
    # ترتيب الفيديوهات حسب الـ ID لعرض الأحدث في الأسفل
    return Video.query.order_by(Video.id.desc()).all()

def download_file_from_url(url, folder, video_id):
    """يحمل الملف من رابط مباشر ويحفظه مع تحديث التقدم في قاعدة البيانات"""
    # الحصول على سياق التطبيق لتمكين التعديل على قاعدة البيانات من Thread منفصل
    with app.app_context():
        video = Video.query.get(video_id)
        if not video: return

        try:
            # زيادة الوقت المسموح للتحميل
            response = requests.get(url, stream=True, timeout=300) 
            response.raise_for_status()
            
            # الحصول على الحجم الكلي للملف
            total_size = int(response.headers.get('content-length', 0))

            filename = secure_filename(url.split('/')[-1])
            if not filename or '.' not in filename:
                # إذا لم يكن هناك امتداد واضح، نفترض mp4 ونستخدم الـ ID كاسم
                filename = f"video_{video_id}.mp4"

            file_path = os.path.join(folder, filename)

            downloaded_size = 0
            
            # حفظ اسم الملف وحجمه الكلي في قاعدة البيانات
            video.filename = filename
            video.total_size = total_size
            db.session.commit()

            # الحفظ بشكل مجزأ وتحديث شريط التقدم
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    # التأكد من عدم وجود خطأ خارجي
                    if video.progress == -1: break 
                    
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    # حساب نسبة التقدم وتحديثها
                    progress = int((downloaded_size / total_size) * 100) if total_size else 0
                    if progress > video.progress:
                        video.progress = progress
                        db.session.commit()
            
            # للتأكد من أن التقدم وصل لـ 100% في النهاية
            if video.progress != -1:
                video.progress = 100
                db.session.commit()

        except requests.exceptions.RequestException as e:
            print(f"Error downloading video ID {video_id}: {e}")
            # تعيين التقدم إلى -1 للدلالة على وجود خطأ
            video.progress = -1
            # إزالة اسم الملف حتى لا يتم محاولة تشغيله
            video.filename = None 
            db.session.commit()
        except Exception as e:
            print(f"An unexpected error occurred for video ID {video_id}: {e}")
            video.progress = -1
            video.filename = None 
            db.session.commit()


# --- الـ Routes (المسارات) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    """الصفحة الرئيسية: لعرض قائمة الفيديوهات وبدء التحميل"""
    if request.method == 'POST':
        video_url = request.form.get('video_url')
        video_title = request.form.get('video_title', 'فيديو جديد')

        if video_url:
            # 1. إنشاء إدخال مؤقت في قاعدة البيانات (حالة 'قيد التحميل')
            new_video = Video(title=video_title, progress=0, filename=None, total_size=0)
            db.session.add(new_video)
            db.session.commit()
            
            # 2. تشغيل عملية التحميل في خلفية منفصلة باستخدام Threading
            # ملاحظة: هذا الحل جيد للمشاريع الصغيرة، لكن في الإنتاج، استخدم Celery.
            threading.Thread(target=download_file_from_url, args=(video_url, app.config['UPLOAD_FOLDER'], new_video.id)).start()

            return redirect(url_for('index', started_download=new_video.id))

    videos = get_video_list()
    return render_template('index.html', videos=videos, page_title="مشغل الفيديو")

@app.route('/status/<int:video_id>')
def download_status(video_id):
    """مسار AJAX لإرجاع حالة التقدم الحالية"""
    video = Video.query.get(video_id)
    if video:
        # إرجاع نسبة التقدم واسم الملف النهائي إذا اكتمل
        return jsonify({
            'progress': video.progress,
            'title': video.title,
            'file_ready': video.progress == 100,
            'error': video.progress == -1
        })
    return jsonify({'error': True, 'message': 'Video not found'}), 404


@app.route('/stream/<int:video_id>')
def stream(video_id):
    """مسار لتشغيل الفيديو في المتصفح (مع Range Headers لـ Streaming سريع)"""
    video = Video.query.get_or_404(video_id)
    
    # يجب أن يكون التحميل قد اكتمل لكي يكون هناك اسم ملف
    if not video.filename or video.progress != 100:
        return "Video not ready for streaming.", 404
        
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
    
    if not os.path.exists(file_path):
        # حذف الإدخال من DB إذا كان الملف غير موجود
        db.session.delete(video)
        db.session.commit()
        return "Video file not found. It might have been deleted from the server.", 404

    # تنفيذ HTTP Range Streaming
    range_header = request.headers.get('Range', None)
    if not range_header:
        # إذا لم يكن هناك Range Header، إرسال الملف بالكامل (للمشغلين القدامى)
        return send_from_directory(app.config['UPLOAD_FOLDER'], video.filename, mimetype=mimetypes.guess_type(video.filename)[0])

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
    if video.progress != 100:
         return redirect(url_for('index', error="الفيديو غير جاهز للتشغيل بعد!"))
         
    return render_template('player.html', video=video, page_title=video.title)


@app.route('/delete/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    """حذف ملف الفيديو من التخزين وقاعدة البيانات"""
    video = Video.query.get_or_404(video_id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)

    try:
        # 1. حذف الملف من التخزين
        if video.filename and os.path.exists(file_path):
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
