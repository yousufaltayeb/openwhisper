# OpenWhisper

OpenWhisper نظام إملاء مفتوح ومحلي أولاً، مصمم للعربية والإنجليزية والتنقل
بينهما أثناء العمل البرمجي. تعتمد إعادة كتابة الإصدار 1.0 على خدمة Rust تعمل
في الخلفية، وواجهة طرفية مبنية بـ Bun وOpenTUI.

> **هذه نسخة تأسيسية تجريبية.** تعمل الخدمة والبروتوكول والحالة والأوامر
> والواجهة الطرفية والإشراف على عامل التفريغ واختبارات الخصوصية حالياً. ما زال
> التقاط الصوت والإدراج والطبقة العائمة وحزم التفريغ الموقّعة والمثبتات واختبارات
> المنصات الثماني ضمن متطلبات الإصدار المستقر. راجع
> [حالة الإصدار 1.0](docs/rewrite/RELEASE_STATUS.md). لا تستخدم هذه النسخة لعرض
> ادعاءات تنافسية عن الأداء.

```text
openwhisper                واجهة الأوامر وOpenTUI
      │ اتصال محلي خاص بإصدار محدد — من دون صوت الميكروفون
openwhisperd               خدمة Rust لكل مستخدم، وهي الكاتب الوحيد للحالة
      ├─ عامل تفريغ أصلي معزول وتحت الإشراف
      └─ طبقة عائمة أصلية تشترك في حالة التسجيل فقط
```

## الاستخدام

يفتح الأمر `openwhisper` الواجهة الطرفية عند تشغيله في طرفية تفاعلية. إذا كان
الإدخال أو الإخراج محولاً إلى أنبوب، يعرض المساعدة من دون تشغيل الخدمة أو محرك
العرض.

```text
openwhisper [ui]
openwhisper record start|stop|toggle|cancel|status [--wait]
openwhisper transcribe <path|-> [--mode raw|clean|code] [--insert]
openwhisper history list|search|show|copy|delete|clear|export
openwhisper modes list|show|select
openwhisper vocab list|add|remove|import|export
openwhisper snippets list|add|remove|run|import|export
openwhisper models list|install|remove|verify|select|import
openwhisper providers list|configure|test|unset
openwhisper config list|get|set
openwhisper service install|start|stop|restart|status|uninstall
openwhisper setup | doctor | logs | completion | update | version
```

تظهر النتائج في `stdout` والتشخيصات في `stderr`. تدعم الأوامر الخيارات
`--plain` و`--json` و`--jsonl` و`--no-color` و`--no-start` والمتغير
`NO_COLOR` بطريقة موحدة.

## البناء والتحقق

```bash
bun --cwd cli install --frozen-lockfile
npm run rewrite:protocol
npm run rewrite:check
npm run rewrite:build
```

لا يتضمن التطبيق أي نموذج صوتي. يظل النموذج المرشح `large-v3-turbo Q5`
محجوباً حتى اعتماد فهرسه الموقّع وترخيصه ونتائج العربية وزمن الاستجابة.

## الخصوصية والبيانات

ينشئ الإصدار 1.0 ملفي `config.toml` و`state.sqlite3` جديدين في مسارات خاصة
ومحددة بالإصدار. لا يفتح ملفات INI القديمة أو السجل أو التخصيصات أو بيانات
الاعتماد أو ذاكرة النماذج، ولا ينقلها أو يعدلها أو يحذفها. يفحص `doctor` وجود
المسارات القديمة من خلال بيانات نظام الملفات فقط، ثم يوضح أنها لم تتغير.

يتطلب الاتصال بمزود سحابي أو فحص التحديث إجراءً صريحاً من المستخدم. تمنع
سياسة «محلي فقط» أي اتصال شبكي. وتظل المزودات السحابية معطلة إذا لم يتوفر
مخزن أسرار معتمد أو بديل مشفر بكلمة مرور ومفعّل صراحة. لا توجد بيانات قياس أو
تحليلات أو تقارير أعطال تلقائية، ولا يجوز أن تحتوي السجلات على نصوص الإملاء أو
الصوت أو محتوى الحافظة أو مفاتيح API.

اقرأ [البنية](docs/rewrite/ARCHITECTURE.md) و[حدود البيانات](docs/rewrite/DATA_BOUNDARY.md)
و[خط الأساس المؤرشف](docs/rewrite/BASELINE.md) قبل المساهمة.

## الترخيص والمصدر

OpenWhisper مرخص برخصة MIT ويحافظ على إشعارات Soupawhisper الأصلية. راجع
[LICENSE](LICENSE) و[NOTICE.md](NOTICE.md). يظل تطبيق Python/Tauri السابق متاحاً
كمرجع سلوكي في الفرع `archive/pre-cli-rewrite-2026-08-19` عند الالتزام
`d05b851`.

[English](README.md)
