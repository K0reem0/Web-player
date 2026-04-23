import os
import re
import requests
import json
import threading
import time
from flask import Flask, request, redirect, url_for, render_template, send_from_directory, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import mimetypes
from urllib.parse import urlparse
from sqlalchemy.exc import OperationalError

# مكتبات التخطي
from curl_cffi import requests as cc_requests
from bs4 import BeautifulSoup

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
    total_size = db.Column(db.Integer, default=0) 
    start_time = db.Column(db.Float, nullable=True) 

# إنشاء الجداول عند بدء التشغيل

# إنشاء الجداول عند بدء التشغيل مع تفادي تصادم الـ Workers
with app.app_context():
    try:
        db.create_all()
    except OperationalError:
        # إذا قام Worker آخر بإنشاء الجدول في نفس اللحظة، تجاهل الخطأ
        pass
    except Exception as e:
        print(f"Error creating database: {e}")



# --- وظائف المساعدة ---

def get_video_list():
    return Video.query.order_by(Video.id.desc()).all()

def parse_cookies(cookie_string):
    cookies = {}
    if not cookie_string:
        return cookies

    if ';' in cookie_string and '\n' not in cookie_string:
        try:
            for pair in cookie_string.split(';'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    cookies[key.strip()] = value.strip()
            if cookies:
                return cookies
        except:
            pass 

    for line in cookie_string.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if '\t' in line:
            parts = line.split('\t')
            if len(parts) >= 7:
                name = parts[5].strip()
                value = parts[6].strip()
                if name and value:
                    cookies[name] = value
        
        elif '=' in line:
            try:
                key, value = line.split('=', 1)
                value_clean = value.strip().split(';')[0]
                cookies[key.strip()] = value_clean
            except:
                continue

    return cookies


# --- دوال تخطي OUO ---

def RecaptchaV3():
    ANCHOR_URL = 'https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Lcr1ncUAAAAAH3cghg6cOTPGARa8adOf-y9zv2x&co=aHR0cHM6Ly9vdW8ucHJlc3M6NDQz&hl=en&v=pCoGBhjs9s8EhFOHJFe8cqis&size=invisible&cb=ahgyd1gkfkhe'
    url_base = 'https://www.google.com/recaptcha/'
    post_data = "v={}&reason=q&c={}&k={}&co={}"
    
    lclient = requests.Session()
    lclient.headers.update({
        'content-type': 'application/x-www-form-urlencoded'
    })
    
    matches = re.findall(r'([api2|enterprise]+)/anchor\?(.*)', ANCHOR_URL)[0]
    url_base += matches[0] + '/'
    params = matches[1]
    
    res = lclient.get(url_base + 'anchor', params=params)
    token = re.findall(r'"recaptcha-token" value="(.*?)"', res.text)[0]
    params = dict(pair.split('=') for pair in params.split('&'))
    post_data = post_data.format(params["v"], token, params["k"], params["co"])
    
    res = lclient.post(url_base + 'reload', params=f'k={params["k"]}', data=post_data)
    answer = re.findall(r'"rresp","(.*?)"', res.text)[0]
    return answer


def ouo_bypass(url):
    print(f"🚀 بدء عملية فك الرابط المختصر باستخدام curl_cffi: {url}")
    url = url.strip()
    tempurl = url.replace("ouo.press", "ouo.io")
    p = urlparse(tempurl)
    id = tempurl.split('/')[-1]

    # إنشاء جلسة جديدة لكل طلب
    client = cc_requests.Session()
    client.headers.update({
        'authority': 'ouo.io',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'cache-control': 'max-age=0',
        'referer': 'http://www.google.com/ig/adde?moduleurl=',
        'upgrade-insecure-requests': '1',
    })

    try:
        res = client.get(tempurl, impersonate="chrome110")
        next_url = f"{p.scheme}://{p.hostname}/go/{id}"

        for _ in range(2):
            if res.headers.get('Location'):
                break

            bs4 = BeautifulSoup(res.content, 'lxml')
            
            # --- التأكد من وجود النموذج قبل استخراج البيانات (تفادي خطأ NoneType) ---
            if bs4.form is None:
                print("⚠️ فشل التخطي: لم يتم العثور على نموذج (Form) في الصفحة المحملة. (قد يكون الموقع يطلب كابتشا معقدة أو حماية).")
                return None
            # -------------------------------------------------------------

            inputs = bs4.form.findAll("input", {"name": re.compile(r"token$")})
            data = {input.get('name'): input.get('value') for input in inputs}
            data['x-token'] = RecaptchaV3()

            h = {
                'content-type': 'application/x-www-form-urlencoded'
            }

            res = client.post(next_url, data=data, headers=h, allow_redirects=False, impersonate="chrome110")
            next_url = f"{p.scheme}://{p.hostname}/xreallcygo/{id}"

        bypassed_link = res.headers.get('Location')
        return bypassed_link
    except Exception as e:
        print(f"❌ خطأ أثناء فك الرابط: {e}")
        return None

# --- دالة التحميل ---

def download_file_from_url(url, folder, video_id, cookies_dict=None):
    global DOWNLOAD_STATE
    
    with app.app_context():
        video = Video.query.get(video_id)
        if not video: 
            if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]
            return

        # ----------------------------------------------------
        # التحقق مما إذا كان الرابط هو رابط ouo مختصر
        if "ouo.io" in url or "ouo.press" in url:
            video.title = video.title + " (جاري فك الرابط...)"
            db.session.commit()
            
            # استدعاء دالة التخطي الجديدة للحصول على الرابط المباشر
            bypassed_url = ouo_bypass(url)
            
            # إزالة نص "جاري فك الرابط..." بعد الانتهاء
            video.title = video.title.replace(" (جاري فك الرابط...)", "")
            db.session.commit()

            if bypassed_url:
                url = bypassed_url
                print(f"✅ تم فك الرابط بنجاح! الرابط النهائي: {url}")
            else:
                print("❌ فشل فك الرابط.")
                video.progress = -1
                db.session.commit()
                if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]
                return
        # ----------------------------------------------------

        try:
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
            
            video.filename = filename
            video.total_size = total_size
            video.progress = 1 
            db.session.commit()

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if video.progress == -1: break 
                    
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    DOWNLOAD_STATE[video_id] = downloaded_size 

            if video.progress != -1:
                video.progress = 100
                db.session.commit()
            
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


# --- الـ Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    global DOWNLOAD_STATE
    
    if request.method == 'POST':
        video_url = request.form.get('video_url')
        video_title = request.form.get('video_title', 'فيديو جديد')
        cookies_str = request.form.get('cookies_data', '')

        if video_url:
            cookies_dict = parse_cookies(cookies_str)
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
            DOWNLOAD_STATE[new_video.id] = 0
            
            threading.Thread(
                target=download_file_from_url, 
                args=(video_url, app.config['UPLOAD_FOLDER'], new_video.id, cookies_dict)
            ).start()

            return redirect(url_for('index', started_download=new_video.id))

    videos = get_video_list()
    return render_template('index.html', videos=videos, page_title="مشغل الفيديو")

@app.route('/status/<int:video_id>')
def download_status(video_id):
    global DOWNLOAD_STATE
    video = Video.query.get(video_id)
    
    if video:
        progress_db = video.progress
        if progress_db == 100 or progress_db == -1:
            return jsonify({'progress': progress_db, 'title': video.title, 'file_ready': progress_db == 100, 'error': progress_db == -1, 'speed_kbps': 0, 'eta_seconds': None})

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

        return jsonify({'progress': progress_estimate, 'title': video.title, 'file_ready': False, 'error': progress_db == -1, 'speed_kbps': speed_kbps, 'eta_seconds': int(eta_seconds) if eta_seconds is not None else None})

    return jsonify({'error': True, 'message': 'Video not found'}), 404

@app.route('/stream/<int:video_id>')
def stream(video_id):
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

    rv = Response(data, 206, mimetype=mimetypes.guess_type(video.filename)[0], direct_passthrough=True)
    rv.headers.add('Content-Range', 'bytes {0}-{1}/{2}'.format(byte1, byte2, size))
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv

@app.route('/play/<int:video_id>')
def play(video_id):
    video = Video.query.get_or_404(video_id)
    if video.progress != 100:
         return redirect(url_for('index', error="الفيديو غير جاهز للتشغيل بعد!"))
    return render_template('player.html', video=video, page_title=video.title)

@app.route('/delete/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    global DOWNLOAD_STATE
    video = Video.query.get_or_404(video_id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], video.filename) if video.filename else ""
    
    if video_id in DOWNLOAD_STATE: del DOWNLOAD_STATE[video_id]
    
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
