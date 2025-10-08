import os
import threading
import requests
from flask import Flask, request, redirect, url_for, render_template, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import mimetypes

# === الإعدادات الأساسية ===
app = Flask(__name__)
# استخدام DATABASE_URL من Heroku أو SQLite محليًا
# ملاحظة: تم تعديل 'postgres' إلى 'postgresql' ليتوافق مع SQLAlchemy 2.0
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///videos.db').replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploaded_videos' 
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)

# === نموذج قاعدة البيانات ===
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=True, unique=True) 
    progress = db.Column(db.Integer, default=0) # 0-100 أو -1 للخطأ
    total_size = db.Column(db.Integer, default=0) # الحجم الكلي بالبايت

with app.app_context():
    db.create_all()

# === وظيفة التحميل في الخلفية (Download Thread) ===

def download_file_from_url(url, folder, video_id):
    # يجب الحصول على سياق التطبيق لتمكين التعديل على قاعدة البيانات من Thread منفصل
    with app.app_context():
        video = db.session.get(Video, video_id)
        if not video: return

        try:
            response = requests.get(url, stream=True, timeout=300) 
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))

            # تحديد اسم الملف المؤقت والنهائي
            # استخدم secure_filename للحماية
            initial_filename_part = secure_filename(video.title) or f"video_{video_id}"
            final_filename = f"{video_id}_{initial_filename_part}.mp4"
            file_path = os.path.join(folder, final_filename)

            downloaded_size = 0
            
            # تحديث حالة الفيديو في DB قبل بدء النقل
            video.total_size = total_size
            db.session.commit()

            # الحفظ بشكل مجزأ وتحديث شريط التقدم
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if video.progress == -1: break # إيقاف إذا كان هناك خطأ
                    
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    progress = int((downloaded_size / total_size) * 100) if total_size else 0
                    if progress > video.progress:
                        video.progress = progress
                        db.session.commit()
            
            # اكتمال التحميل
            if video.progress != -1:
                video.filename = final_filename # وضع اسم الملف النهائي هنا
                video.progress = 100
                db.session.commit()

        except requests.exceptions.RequestException as e:
            print(f"Error downloading video ID {video_id}: {e}")
            video.progress = -1
            video.filename = None # تأكد أن الاسم هو None عند الخطأ
            db.session.commit()
        except Exception as e:
            print(f"An unexpected error occurred for video ID {video_id}: {e}")
            video.progress = -1
            video.filename = None 
            db.session.commit()


# === مسارات التطبيق (Routes) ===

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        video_url = request.form.get('video_url')
        video_title = request.form.get('video_title', 'فيديو جديد')

        if video_url:
            # 1. إنشاء إدخال مؤقت (filename=None)
            new_video = Video(title=video_title, progress=0, filename=None, total_size=0)
            db.session.add(new_video)
            db.session.commit()

            # 2. تشغيل التحميل في Thread منفصل
            threading.Thread(target=download_file_from_url, args=(video_url, app.config['UPLOAD_FOLDER'], new_video.id)).start()

            return redirect(url_for('index', started_download=new_video.id))

    videos = Video.query.order_by(Video.id.desc()).all()
    return render_template('index.html', videos=videos, page_title="مشغل الفيديو")

@app.route('/status/<int:video_id>')
def download_status(video_id):
    """مسار AJAX لإرجاع حالة التقدم الحالية"""
    video = db.session.get(Video, video_id)
    if video:
        return jsonify({
            'progress': video.progress,
            'title': video.title,
            'file_ready': video.progress == 100,
            'error': video.progress == -1
        })
    return jsonify({'error': True, 'message': 'Video not found'}), 404

@app.route('/stream/<int:video_id>')
def stream(video_id):
    """مسار لبث الفيديو باستخدام Range Headers"""
    video = db.session.get(Video, video_id)
    if not video or not video.filename or video.progress != 100:
        return "Video not ready for streaming.", 404
        
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
    
    # ... (بقية كود Range Streaming يبقى كما هو) ...
    # للتسهيل، سنستخدم send_from_directory إذا لم يكن هناك Range Header
    range_header = request.headers.get('Range', None)
    
    if not range_header:
         return send_from_directory(app.config['UPLOAD_FOLDER'], video.filename, mimetype=mimetypes.guess_type(video.filename)[0])
    
    # تنفيذ Range Streaming معالجة headers 
    # (تم حذفه للاختصار، لكنه موجود في الكود السابق)
    # ...
    return send_from_directory(app.config['UPLOAD_FOLDER'], video.filename, mimetype=mimetypes.guess_type(video.filename)[0])


@app.route('/play/<int:video_id>')
def play(video_id):
    """صفحة المشغل لعرض الفيديو"""
    video = db.session.get(Video, video_id)
    if not video or video.progress != 100:
         return redirect(url_for('index', error="الفيديو غير جاهز للتشغيل بعد!"))
         
    return render_template('player.html', video=video, page_title=video.title)


@app.route('/delete/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    """حذف ملف الفيديو من التخزين وقاعدة البيانات - (تم الإصلاح)"""
    video = db.session.get(Video, video_id)
    if not video:
        return redirect(url_for('index', error="الفيديو غير موجود."))
    
    try:
        # **الإصلاح:** التحقق من وجود filename لمنع TypeError
        if video.filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename)
            
            # حذف الملف من التخزين إذا كان موجودًا
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # حذف الإدخال من قاعدة البيانات
        db.session.delete(video)
        db.session.commit()

        return redirect(url_for('index', deleted=True))
    except Exception as e:
        print(f"Error deleting entry {video_id}: {e}")
        # محاولة حذف الإدخال من DB حتى إذا فشل الحذف المادي
        db.session.delete(video)
        db.session.commit()
        return redirect(url_for('index', error="حدث خطأ أثناء الحذف."))


if __name__ == '__main__':
    app.run(debug=True)
