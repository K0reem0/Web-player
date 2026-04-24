from flask import Flask, request, render_template
import re
from curl_cffi import requests as cc_requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests
import logging
import sys

# إعداد نظام تسجيل الأخطاء (Logging) ليظهر في Heroku
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def RecaptchaV3():
    logger.info("جاري محاولة حل Recaptcha V3...")
    ANCHOR_URL = 'https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Lcr1ncUAAAAAH3cghg6cOTPGARa8adOf-y9zv2x&co=aHR0cHM6Ly9vdW8ucHJlc3M6NDQz&hl=en&v=pCoGBhjs9s8EhFOHJFe8cqis&size=invisible'
    url_base = 'https://www.google.com/recaptcha/'
    post_data = "v={}&reason=q&c={}&k={}&co={}"

    session = requests.Session()
    session.headers.update({
        'content-type': 'application/x-www-form-urlencoded'
    })

    matches = re.findall(r'(api2|enterprise)/anchor\?(.*)', ANCHOR_URL)
    if not matches:
        logger.error("فشل في تحليل رابط Recaptcha الأساسي.")
        raise Exception("فشل في تحليل رابط Recaptcha")

    matches = matches[0]
    url_base += matches[0] + '/'
    params = matches[1]

    try:
        res = session.get(url_base + 'anchor', params=params)
        token_match = re.findall(r'"recaptcha-token" value="(.*?)"', res.text)
        if not token_match:
            logger.error("لم يتم العثور على recaptcha-token في الصفحة.")
            raise Exception("فشل في الحصول على توكن Recaptcha")

        token = token_match[0]
        params = dict(pair.split('=') for pair in params.split('&'))

        post_data = post_data.format(params["v"], token, params["k"], params["co"])
        res = session.post(url_base + 'reload', params=f'k={params["k"]}', data=post_data)

        answer_match = re.findall(r'"rresp","(.*?)"', res.text)
        if not answer_match:
            logger.error(f"فشل في حل Recaptcha، رد السيرفر: {res.text[:100]}")
            raise Exception("فشل في حل Recaptcha")

        logger.info("تم حل Recaptcha بنجاح.")
        return answer_match[0]
    except Exception as e:
        logger.exception("حدث خطأ غير متوقع أثناء حل الكابتشا:")
        raise e

def ouo_bypass(url):
    logger.info(f"بدء عملية التخطي للرابط: {url}")
    url = url.strip()
    tempurl = url.replace("ouo.press", "ouo.io")
    parsed = urlparse(tempurl)
    link_id = tempurl.split('/')[-1]

    client = cc_requests.Session()
    client.headers.update({
        'authority': 'ouo.io',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://google.com/',
        'upgrade-insecure-requests': '1',
    })

    logger.info(f"إرسال الطلب الأول إلى: {tempurl}")
    res = client.get(
        tempurl,
        impersonate="chrome120",
        allow_redirects=False
    )

    if res.headers.get("Location"):
        logger.info(f"تم العثور على توجيه مباشر: {res.headers.get('Location')}")
        return res.headers.get("Location")

    next_url = f"{parsed.scheme}://{parsed.hostname}/go/{link_id}"

    for step in range(2):
        logger.info(f"الخطوة {step + 1}: تحليل الصفحة للبحث عن النموذج (Form)...")
        soup = BeautifulSoup(res.content, 'html.parser')
        form = soup.find("form")

        if not form:
            logger.error(f"لم يتم العثور على نموذج. أول 500 حرف من الصفحة: {res.text[:500]}")
            raise Exception("لم يتم العثور على نموذج (قد يكون الرابط محظوراً من السيرفر أو تغيرت بنية الموقع)")

        inputs = form.find_all("input", {"name": re.compile(r"token$")})
        data = {i.get('name'): i.get('value') for i in inputs}
        logger.info("تم سحب بيانات النموذج، جاري جلب توكن الكابتشا...")

        data['x-token'] = RecaptchaV3()
        headers = {'content-type': 'application/x-www-form-urlencoded'}

        logger.info(f"إرسال طلب POST إلى: {next_url}")
        res = client.post(
            next_url,
            data=data,
            headers=headers,
            allow_redirects=False,
            impersonate="chrome120"
        )

        if res.headers.get("Location"):
            logger.info("تم التخطي بنجاح!")
            break

        next_url = f"{parsed.scheme}://{parsed.hostname}/xreallcygo/{link_id}"

    return res.headers.get("Location")

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    error = None
    
    if request.method == 'POST':
        url = request.form.get('url')
        if url and (url.startswith('http://') or url.startswith('https://')):
            try:
                bypassed_link = ouo_bypass(url)
                if bypassed_link:
                    result = bypassed_link
                else:
                    error = "لم نتمكن من تخطي الرابط. (لا يوجد توجيه Location)"
                    logger.warning(f"فشل التخطي للرابط {url} - لم يتم إرجاع رابط وجهة.")
            except Exception as e:
                logger.exception(f"حدث خطأ أثناء معالجة الرابط {url}:")
                error = "حدث خطأ: " + str(e)
        else:
            error = "الرجاء إدخال رابط صحيح يبدأ بـ http أو https"
            
    return render_template('index.html', result=result, error=error)

if __name__ == '__main__':
    app.run(debug=True)

