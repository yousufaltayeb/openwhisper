# OpenWhisper — أوبن ويسبر

OpenWhisper تطبيق إملاء صوتي لسطح مكتب لينكس، صُمّم للخصوصية ولدعم العربية
والإنجليزية والانتقال الطبيعي بينهما في الجملة نفسها. يعمل محلياً افتراضياً عبر
Faster Whisper، ويمكنك إضافة مفاتيحك الخاصة لمزوّدي الخدمات السحابية المدعومين.

إصدار 0.1 موجّه إلى Linux x86_64 بواجهة Capture مبنية بـ React/Tauri ومحرك
Python خاص. يبقى PySide6 مسؤولاً عن التسجيل وQt Multimedia والبوابات والنافذة
العائمة التي لا تسحب التركيز. هذا إصدار أولي؛ استخدمه في مهامك الشخصية وأبلغ
عن المشاكل القابلة لإعادة الإنتاج. [English README](README.md)

![عرض الإملاء بالإنجليزية والعربية](docs/images/openwhisper-demo.gif)

## المزايا

- ابدأ التسجيل وأوقفه باختصار تبديل، أو استخدم الضغط المستمر للتحدث.
- يتعرّف على العربية والإنجليزية والكتابة المختلطة تلقائياً.
- يبقى Faster Whisper محلياً افتراضياً؛ تُنزّل أوزان النموذج عند أول استخدام فقط.
- يمكن إضافة Cohere وOpenAI وGroq وDeepgram بمفاتيحك الخاصة عند الحاجة.
- النص الخام هو الوضع الافتراضي. تتوفر أوضاع مستقلة للنص الخام والتنظيف والفصحى
  والرسائل والبريد والملاحظات والسياق الذكي والتعليمات المخصصة.
- يدعم مفردات التعرف والمقاطع الجاهزة والتنسيق العربي والإنجليزي وتحويل النص
  المحدد مع المعاينة والتراجع الآمن.
- يحتفظ بسجل بحث محلي للنص الخام والنهائي والبيانات الوصفية غير الحساسة. الاحتفاظ
  بالصوت معطّل افتراضياً؛ وعند تفعيله تكون المدة الافتراضية 7 أيام والقصوى 30.
- الإدراج المباشر مدعوم على X11. في Wayland يستخدم التطبيق وسيلة متاحة أو ينسخ
  النص إلى الحافظة مع إشعار واضح.
- تُحفظ المفاتيح في XDG Secret portal بصيغة مشفرة عند توفره، ولا تُكتب في ملف
  الإعدادات أو السجل أو السجلات التشخيصية.

لا يقدّم OpenWhisper حسابات مستضافة أو مزامنة أو قياس استخدام (telemetry).

## التثبيت (Flatpak)

يوزَّع OpenWhisper كـ Flatpak موقّع لمعمارية x86_64 فقط. يتلقى فرع `beta`
التحديثات من `main`، أما `stable` فيُرقّى من وسم إصدار معتمد بعد اجتياز بوابات
قبول لينكس.

```bash
flatpak remote-add --if-not-exists --from openwhisper \
  https://yousufaltayeb.github.io/openwhisper/openwhisper-beta.flatpakrepo
flatpak install openwhisper io.github.yousufaltayeb.OpenWhisper//beta
flatpak run io.github.yousufaltayeb.OpenWhisper
```

للنسخة المستقرة استبدل `openwhisper-beta.flatpakrepo` و`//beta` بـ
`openwhisper-stable.flatpakrepo` و`//stable`. يتحقق Flatpak من توقيع المستودع
قبل التثبيت والتحديث. لا يستخدم المشروع Flathub حالياً بسبب سياسة المحتوى
[المُساعَد بالذكاء الاصطناعي](https://docs.flathub.org/docs/for-app-authors/requirements#generative-ai-policy).

بصمة مفتاح توقيع الإصدارات:
`9DFE F9AB 055B 9CC8 A4D1 6DBB B6BF 3FE6 2C7E 797D`.

## الخصوصية والتخزين

| البيانات | الموقع | ملاحظة |
| --- | --- | --- |
| التفضيلات | مجلد إعدادات Flatpak | لا يحتوي مفاتيح API. |
| السجل | مجلد بيانات Flatpak | النص الخام والنهائي محلياً. |
| الصوت المؤقت | مجلد ذاكرة Flatpak المؤقتة | يُحذف بعد كل عملية. |
| الصوت المحتفظ به | مجلد بيانات Flatpak | معطّل افتراضياً؛ 7 أيام افتراضياً و30 يوماً كحد أقصى. |
| مفاتيح API | غلاف مشفّر عبر XDG Secret portal | متغيرات البيئة وذاكرة الجلسة بديلان. |

إن وُجد ملف الإعداد القديم `~/.config/whisper/config.ini` ولم يوجد إعداد جديد،
ينقل OpenWhisper التفضيلات المتوافقة مرة واحدة فقط. لا يحذف الملف أو المجلد
القديم ولا يعدّله.

## الإعداد والاستخدام

استخدم شاشة **Settings** لاختيار الموفّر والنموذج والاختصار. يوجد المثال المكافئ
في [config.example.ini](config.example.ini).

- القيمة `language = auto` مناسبة للعربية والإنجليزية. يمكن اختيار `ar` أو
  `ar-SA` أو `en` عند الحاجة.
- القيمة `mode = raw` في قسم `[cleanup]` تحافظ على النص كما أعاده الموفّر.
- القيمة `mode = toggle` في قسم `[shortcuts]` تبدأ التسجيل بضغطة وتوقفه بالضغطة
  التالية. اختر `push-to-talk` للضغط المستمر.
- كل مصادر السياق معطلة افتراضياً لكل وضع. إرسال السياق إلى موفّر سحابي يحتاج
  موافقة منفصلة وصريحة.
- لا تضع مفتاح API في ملف INI أو تقرير مشكلة. أضفه من
  **Settings → Provider setup** أو استخدم متغير البيئة الخاص بالموفّر، مثل
  `OPENAI_API_KEY` أو `COHERE_API_KEY`.

لاستخدام نموذج Cohere العربي المحلي، وافق أولاً على شروط النموذج المحجوب في
[Hugging Face](https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026)،
ثم ثبّت امتداد Flatpak الاختياري من مستودع OpenWhisper واختر
**Settings → Provider setup → Install managed pack**. يفحص التطبيق توفر بطاقة
رسومية مدعومة أو ذاكرة نظام لا تقل عن 8 GiB، ويتطلب اختيار `ar` أو `en` صراحةً.
رمز Hugging Face المستخدم للتنزيل لا يحفظه OpenWhisper.

```bash
flatpak install openwhisper \
  io.github.yousufaltayeb.OpenWhisper.CohereLocal//beta
```

استخدم `//stable` عندما يكون التطبيق مثبتاً من فرع stable.

يتضمن Flatpak خادم `llama.cpp` للمعالجة المحلية على المعالج، ويمكن تنزيل أوزان
Qwen3 4B بصيغة GGUF Q4_K_M عند الطلب للتنظيف والتحويل من دون إرسال النص للسحابة.

## التطوير والمساهمة

للتطوير من المصدر استخدم Python 3.12 وNode 24 وRust، ثم نفّذ
`uv sync --extra dev` و`npm --prefix frontend ci` و`npm run tauri:dev`.
يمكن تشغيل حالات واجهة حتمية بلا تسجيل أو شبكة عبر `npm run frontend:dev`،
كما يفحص `npm run e2e:build` ثم `npm run e2e` نافذة Tauri الحقيقية باستخدام
WebKitGTK وaxe؛ إضافات الاختبار لا تدخل بناء الإصدار.
تبقى الواجهة القديمة متاحة مؤقتاً للمقارنة عبر `uv run openwhisper` حتى ينجح
اختبار التكافؤ على GNOME وKDE. للمساهمة وفتح بلاغات المشاكل، راجع
[CONTRIBUTING.md](CONTRIBUTING.md) و[قوالب
البلاغات](.github/ISSUE_TEMPLATE). لا يوجد مسار توزيع بديل أو مثبّت محلي.

المعرّف الرسمي للتطبيق هو `io.github.yousufaltayeb.OpenWhisper`، واسم أمر الطرفية
هو `openwhisper`. الترخيص MIT؛ راجع [LICENSE](LICENSE) و[NOTICE.md](NOTICE.md).
