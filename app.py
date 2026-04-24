from flask import Flask, request, render_template
import logging
import sys
import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# إعداد الـ Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def create_driver():
    """إعداد متصفح Chrome للعمل بشكل مخفي وتجاوز اكتشاف الروبوتات"""
    chrome_options = Options()
    
    # إعدادات أساسية لهيروكو
    chrome_options.add_argument("--headless=new")  # استخدام المحرك المخفي الجديد لكروم
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    
    # إعدادات مهمة جداً لإخفاء السيلينيوم عن Cloudflare
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # إضافة User-Agent واقعي
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    chrome_bin = os.environ.get("GOOGLE_CHROME_BIN")
    if chrome_bin:
        chrome_options.binary_location = chrome_bin

    driver = webdriver.Chrome(options=chrome_options)
    
    # تنفيذ سكربت إضافي لمسح أي أثر للسيلينيوم من المتصفح
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
        '''
    })
    
    return driver

def ouo_bypass(url):
    logger.info(f"بدء التخطي عبر المتصفح للرابط: {url}")
    driver = create_driver()
    try:
        # تحويل الرابط إذا كان ouo.press
        target_url = url.replace("ouo.press", "ouo.io")
        driver.get(target_url)
        
        # الانتظار لتخطي حماية Cloudflare (الانتظار حتى يظهر زر "I'm a human")
        logger.info("في انتظار تخطي Cloudflare وظهور زر التأكيد...")
        wait = WebDriverWait(driver, 20)
        
        # محاولة الضغط على الزر الأول (I'm a human)
        # ملاحظة: ouo يستخدم أحياناً زر داخل Form
        try:
            btn = wait.until(EC.element_to_be_clickable((By.ID, "btn-main")))
            logger.info("تم العثور على الزر الأول، جاري الضغط...")
            # أحياناً يحتاج الأمر لسكربت للضغط إذا كان هناك عنصر فوق الزر
            driver.execute_script("arguments[0].click();", btn)
        except Exception as e:
            logger.warning("لم يتم العثور على زر btn-main، قد يكون الرابط تخطى تلقائياً أو تغيرت الصفحة")

        # الانتظار للخطوة الثانية (زر Get Link)
        time.sleep(2) # انتظار بسيط للتأكد من تحميل الصفحة التالية
        logger.info("في انتظار زر Get Link...")
        
        btn_go = wait.until(EC.element_to_be_clickable((By.ID, "btn-main")))
        driver.execute_script("arguments[0].click();", btn_go)
        
        # الانتظار حتى يتغير الرابط إلى الرابط النهائي
        time.sleep(2)
        final_url = driver.current_url
        
        if "ouo.io" in final_url or "ouo.press" in final_url:
            # إذا ما زال الرابط هو نفسه، قد يكون هناك توجيه لم يكتمل
            logger.info(f"الرابط الحالي هو {final_url}، جاري فحص العنوان النهائي...")
            # في بعض الحالات الرابط النهائي يظهر في الـ URL مباشرة بعد الضغط
            
        logger.info(f"تم التخطي بنجاح! الرابط النهائي: {final_url}")
        return final_url

    except Exception as e:
        logger.exception("حدث خطأ أثناء محاكاة المتصفح:")
        raise e
    finally:
        driver.quit()

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    error = None
    if request.method == 'POST':
        url = request.form.get('url')
        if url and 'ouo' in url:
            try:
                result = ouo_bypass(url)
            except Exception as e:
                error = f"فشل التخطي: {str(e)}"
        else:
            error = "الرجاء إدخال رابط OUO صحيح"
            
    return render_template('index.html', result=result, error=error)

if __name__ == '__main__':
    # هيروكو يحدد بورت تلقائي، نستخدم 5000 محلياً
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

