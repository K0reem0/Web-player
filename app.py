from flask import Flask, request, render_template
import re
from curl_cffi import requests as cc_requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests

app = Flask(__name__)

def RecaptchaV3():
    ANCHOR_URL = 'https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Lcr1ncUAAAAAH3cghg6cOTPGARa8adOf-y9zv2x&co=aHR0cHM6Ly9vdW8ucHJlc3M6NDQz&hl=en&v=pCoGBhjs9s8EhFOHJFe8cqis&size=invisible'
    url_base = 'https://www.google.com/recaptcha/'
    post_data = "v={}&reason=q&c={}&k={}&co={}"

    session = requests.Session()
    session.headers.update({
        'content-type': 'application/x-www-form-urlencoded'
    })

    matches = re.findall(r'(api2|enterprise)/anchor\?(.*)', ANCHOR_URL)
    if not matches:
        raise Exception("فشل في تحليل رابط Recaptcha")

    matches = matches[0]
    url_base += matches[0] + '/'
    params = matches[1]

    res = session.get(url_base + 'anchor', params=params)
    token_match = re.findall(r'"recaptcha-token" value="(.*?)"', res.text)
    if not token_match:
        raise Exception("فشل في الحصول على توكن Recaptcha")

    token = token_match[0]
    params = dict(pair.split('=') for pair in params.split('&'))

    post_data = post_data.format(params["v"], token, params["k"], params["co"])
    res = session.post(url_base + 'reload', params=f'k={params["k"]}', data=post_data)

    answer_match = re.findall(r'"rresp","(.*?)"', res.text)
    if not answer_match:
        raise Exception("فشل في حل Recaptcha")

    return answer_match[0]

def ouo_bypass(url):
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

    res = client.get(
        tempurl,
        impersonate="chrome120",
        allow_redirects=False
    )

    if res.headers.get("Location"):
        return res.headers.get("Location")

    next_url = f"{parsed.scheme}://{parsed.hostname}/go/{link_id}"

    for step in range(2):
        soup = BeautifulSoup(res.content, 'html.parser')
        form = soup.find("form")

        if not form:
            raise Exception("لم يتم العثور على نموذج (قد يكون الرابط محظوراً أو تغيرت بنية الموقع)")

        inputs = form.find_all("input", {"name": re.compile(r"token$")})
        data = {i.get('name'): i.get('value') for i in inputs}

        data['x-token'] = RecaptchaV3()
        headers = {'content-type': 'application/x-www-form-urlencoded'}

        res = client.post(
            next_url,
            data=data,
            headers=headers,
            allow_redirects=False,
            impersonate="chrome120"
        )

        if res.headers.get("Location"):
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
                    error = "لم نتمكن من تخطي الرابط."
            except Exception as e:
                error = str(e)
        else:
            error = "الرجاء إدخال رابط صحيح يبدأ بـ http أو https"
            
    return render_template('index.html', result=result, error=error)

if __name__ == '__main__':
    app.run(debug=True)

