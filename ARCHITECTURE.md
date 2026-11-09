# MotoGateway v2 — Architecture Document

> מסמך ארכיטקטורה ותכנון. גרסה: 0.1 | תאריך: 2026-11-09

---

## 1. חזון

להפוך את MotoGateway מכלי המרה ספציפי (Priority → TECOM) לפלטפורמת אינטגרציה גנרית שמאפשרת למשתמש קצה — גם ללא רקע טכני — לבנות ממשקים בין מערכות בעצמו.

המערכת נבנית על בסיס הקוד הקיים (v1.3.3) עם refactor משמעותי, תוך שמירה על תאימות אחורה מלאה ללקוחות קיימים.

## 2. עקרונות מנחים

- **גנרי קודם** — הארכיטקטורה לא תלויה ב-Priority או בפורמט ספציפי. Priority הוא first-class citizen אבל לא הנחת יסוד.
- **דקלרטיבי** — כל integration מוגדר כ-JSON config, לא כקוד. המנוע מפרש את ההגדרה.
- **AI-assisted** — מיפוי שדות נעשה באמצעות LLM (OpenAI/Claude API) ומוצג ויזואלית לאישור המשתמש.
- **On-premise** — המערכת רצה על שרת הלקוח, ליד המערכות שהיא מתממשקת אליהן. אין תלות בענן (למעט קריאות AI API אופציונליות).
- **פשטות למשתמש** — ממשק wizard שמוביל צעד אחר צעד. אין צורך בכתיבת קוד.

## 3. מודל הליבה: Integration

כל ממשק שמשתמש בונה הוא **Integration** — הגדרה דקלרטיבית עם שלושה חלקים:

```
┌──────────┐      ┌─────────────┐      ┌──────────────┐
│  Source   │ ──>  │  Transform  │ ──>  │ Destination  │
│  (קלט)   │      │  (מיפוי)    │      │   (פלט)      │
└──────────┘      └─────────────┘      └──────────────┘
```

### 3.1 Source — מאיפה מגיע המידע

| סוג         | תיאור                                                        | שלב |
|-------------|--------------------------------------------------------------|-----|
| **File**    | ניטור תיקייה (כמו היום), קריאת קבצים בפורמטים שונים         | 1   |
| **Webhook** | המערכת מחוללת URL ייחודי, חברה חיצונית שולחת אליו נתונים    | 2   |
| **API Poll**| המערכת קוראת ל-API חיצוני בתזמון קבוע ושואבת נתונים         | 2   |
| **Upload**  | המשתמש מעלה קובץ ידנית דרך הממשק                            | 1   |
| **FTP/SFTP**| המערכת שואבת קבצים משרת FTP מרוחק                           | 5   |

### 3.2 Transform — מה עושים עם המידע

הלב של המערכת. מיפוי דקלרטיבי שמגדיר כיצד שדות ממקור הופכים לשדות ביעד.

```json
{
  "field_mappings": [
    {
      "source_path": "invoice.header.invoice_number",
      "target_path": "TXML.Invoice.InvoiceHeader.InvoiceNumber",
      "transform": null
    },
    {
      "source_path": "invoice.header.date",
      "target_path": "TXML.Invoice.InvoiceHeader.InvoiceDate",
      "transform": "date_format('DD/MM/YYYY', 'YYYY-MM-DD')"
    },
    {
      "source_path": "invoice.lines[].unit_price * invoice.lines[].quantity",
      "target_path": "TXML.Invoice.Lines[].LineTotal",
      "transform": "calculate"
    }
  ]
}
```

**יכולות Transform:**
- מיפוי ישיר (field → field)
- המרת פורמטים (תאריכים, מספרים, קידוד)
- חישובים (שדה × שדה, סכימה)
- lookup tables (כמו country codes היום)
- ערכי ברירת מחדל וקבועים
- תנאים (אם שדה X ריק, השתמש ב-Y)

**יצירת המיפוי — תהליך AI:**
1. המשתמש מעלה דוגמא לנתוני מקור (קובץ, JSON, XML)
2. המשתמש מעלה דוגמא/schema של הפורמט הנדרש ביעד
3. המערכת שולחת את שתי הדוגמאות ל-LLM API
4. ה-LLM מחזיר מיפוי מוצע בפורמט JSON
5. המערכת מציגה את המיפוי ויזואלית (source field ↔ target field)
6. המשתמש מאשר, מתקן, או מוסיף כללים
7. המיפוי נשמר כחלק מה-Integration config

### 3.3 Destination — לאן הולך הפלט

| סוג             | תיאור                                                      | שלב |
|-----------------|-------------------------------------------------------------|-----|
| **File**        | כתיבה לתיקייה בפורמט הנדרש (XML, JSON, CSV, XLS...)       | 1   |
| **API**         | שליחת הנתונים ל-API חיצוני (REST)                          | 2   |
| **Priority**    | כתיבה ל-Priority דרך OData API                             | 5   |
| **FTP/SFTP**    | העלאת קובץ הפלט לשרת FTP מרוחק                            | 5   |

## 4. מבנה ה-Integration Config

כל integration נשמר כ-JSON:

```json
{
  "id": "inv-tecom-001",
  "name": "חשבוניות מוטוראד → TECOM",
  "enabled": true,
  "created_at": "2026-11-09T10:00:00",

  "source": {
    "type": "file",
    "config": {
      "watch_dir": "C:/invoices/input",
      "file_pattern": "*.xls",
      "format": "xls_tab_delimited"
    }
  },

  "transform": {
    "field_mappings": [ ... ],
    "lookup_tables": {
      "country_codes": { "ISRAEL": "IL", "GERMANY": "DE" }
    },
    "constants": {
      "message_type": "Invoice",
      "message_function": "Original"
    }
  },

  "destination": {
    "type": "file",
    "config": {
      "output_dir": "C:/invoices/output",
      "format": "xml",
      "template": "txml_2.5"
    }
  },

  "error_handling": {
    "error_dir": "C:/invoices/errors",
    "archive_dir": "C:/invoices/archive",
    "on_error": "move_to_error_dir"
  }
}
```

## 5. תאימות אחורה — Builtin Integrations

ממשקי TECOM ו-OrdRsp הקיימים הופכים ל-builtin integrations. הם:
- מוגדרים מראש במערכת ולא ניתנים למחיקה
- משתמשים בקוד ההמרה הקיים (tecom_xml.py, ordrsp_xml.py) כ-custom transform
- ממשיכים לעבוד בדיוק כמו ב-v1.x, כולל ממשק הפרופילים הנוכחי
- משמשים כדוגמא למשתמשים שרוצים לבנות integrations דומים

## 6. ארכיטקטורת המערכת

```
┌─────────────────────────────────────────────────────────┐
│                     Web UI (Flask)                       │
│  ┌──────────┐ ┌───────────┐ ┌────────┐ ┌────────────┐  │
│  │  Wizard  │ │  Mapping  │ │  Log   │ │  Settings  │  │
│  │ (חדש)   │ │ Editor    │ │(קיים)  │ │  (קיים)    │  │
│  │         │ │ (חדש)     │ │        │ │            │  │
│  └──────────┘ └───────────┘ └────────┘ └────────────┘  │
├─────────────────────────────────────────────────────────┤
│                  Integration Engine                      │
│  ┌────────────┐ ┌─────────────┐ ┌───────────────────┐  │
│  │  Source     │ │  Transform  │ │   Destination     │  │
│  │  Manager   │ │  Engine     │ │   Manager         │  │
│  │            │ │             │ │                   │  │
│  │ - File     │ │ - Mapper    │ │ - File Writer    │  │
│  │ - Webhook  │ │ - Converter │ │ - API Client     │  │
│  │ - API Poll │ │ - Validator │ │ - Priority OData │  │
│  │ - Upload   │ │             │ │ - FTP            │  │
│  │ - FTP      │ │             │ │                   │  │
│  └────────────┘ └─────────────┘ └───────────────────┘  │
├─────────────────────────────────────────────────────────┤
│                   Infrastructure                         │
│  ┌──────┐ ┌────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │Config│ │   DB   │ │ Watcher  │ │  AI Client       │ │
│  │(JSON)│ │(SQLite)│ │(watchdog)│ │ (OpenAI/Claude)  │ │
│  └──────┘ └────────┘ └──────────┘ └──────────────────┘ │
├─────────────────────────────────────────────────────────┤
│              Windows Service (pywin32)                    │
└─────────────────────────────────────────────────────────┘
```

## 7. AI Integration — הגדרת מפתחות

המשתמש מגדיר בהגדרות:
- **AI Provider**: OpenAI / Anthropic (Claude) / None (מיפוי ידני בלבד)
- **API Key**: מפתח ה-API (מוצפן על הדיסק כמו הסיסמה היום)
- **Model**: בחירת מודל (gpt-4o, claude-sonnet, וכו')

כשאין מפתח AI מוגדר, המשתמש עדיין יכול לבנות מיפוי ידנית דרך הממשק הויזואלי.

## 8. פורמטי קבצים נתמכים

### קלט (Source formats):
- XLS / XLSX (tab-delimited ורגיל)
- CSV / TSV
- JSON
- XML
- Fixed-width text

### פלט (Destination formats):
- XML (עם template support)
- JSON
- CSV
- XLS/XLSX

פורמטים נוספים יתווספו לפי דרישה.

## 9. Roadmap — שלבי פיתוח

### שלב 1: Integration Engine Foundation
- Refactor ל-engine.py — מעבר ל-integration config דקלרטיבי
- מודל Integration ב-config (JSON schema)
- Integration runner שקורא config ומבצע source → transform → destination
- עטיפת TECOM + OrdRsp כ-builtin integrations
- בדיקה שהכל עובד כמו קודם (backward compatibility)

### שלב 2: Webhook + API
- Flask routes לחולל ולנהל webhook endpoints
- Webhook receiver — קבלת POST, validation, העברה ל-pipeline
- API polling engine — תזמון קריאות, שמירת state
- UI לניהול webhooks ו-API connections

### שלב 3: AI Mapping
- אינטגרציה עם OpenAI/Claude API
- Parser לדוגמאות קבצים (חילוץ schema אוטומטי)
- Prompt engineering למיפוי שדות
- שמירת מיפוי כ-JSON
- UI ויזואלי להצגת ועריכת מיפוי (source ↔ target)

### שלב 4: Integration Wizard
- UI wizard מלא: source → samples → mapping → destination → activate
- תצוגת dashboard של כל ה-integrations הפעילים
- ניטור ו-monitoring בזמן אמת
- Test run — הרצת integration על דוגמא לפני הפעלה

### שלב 5: Connectors מתקדמים
- FTP/SFTP source + destination
- Priority OData connector (קריאה + כתיבה)
- Connector framework להוספת מערכות נוספות

## 10. מבנה תיקיות מוצע (לאחר refactor)

```
app/
  core/
    integration.py      - מודל Integration + runner
    transform.py        - מנוע המיפוי הדקלרטיבי
    source_manager.py   - ניהול sources (file, webhook, api, upload)
    dest_manager.py     - ניהול destinations (file, api, ftp)
    ai_mapper.py        - חיבור ל-LLM APIs למיפוי אוטומטי
  
  sources/
    file_source.py      - קריאת קבצים (מחליף חלקית את xls_reader)
    webhook_source.py   - קבלת webhooks
    api_source.py       - polling מ-APIs
    ftp_source.py       - קריאה מ-FTP
  
  destinations/
    file_dest.py        - כתיבה לקובץ
    api_dest.py         - שליחה ל-API
    priority_dest.py    - כתיבה ל-Priority OData
    ftp_dest.py         - העלאה ל-FTP
  
  builtin/
    tecom_xml.py        - (קוד קיים) בונה TXML 2.5
    ordrsp_xml.py       - (קוד קיים) בונה OrdRsp XML
    xls_reader.py       - (קוד קיים) קורא XLS
    ordrsp_reader.py    - (קוד קיים) קורא OrdRsp
  
  web/
    webapp.py           - (קיים, מורחב)
    wizard.py           - routes ל-wizard חדש
    api_routes.py       - REST API פנימי + webhook endpoints
  
  config.py             - (קיים, מורחב)
  db.py                 - (קיים, מורחב)
  watcher.py            - (קיים, מותאם)
  i18n.py               - (קיים)
  engine.py             - (קיים, הופך לעטיפה שקוראת ל-core/integration.py)

  static/               - (קיים + חדש)
  templates/            - (קיים + חדש)
```

## 11. החלטות טכניות

| נושא | החלטה | נימוק |
|-------|--------|-------|
| שפת תכנות | Python (כמו היום) | קוד קיים, אקוסיסטם עשיר |
| Web framework | Flask (כמו היום) | עובד, פשוט, מספיק |
| DB | SQLite (כמו היום) | on-prem, בלי תלויות |
| Config | JSON files (כמו היום) | פשוט, קריא, editable |
| AI | OpenAI / Claude API (configurable) | המשתמש מביא מפתח |
| Deployment | Windows Service (כמו היום) | on-prem, ליד Priority |
| מיפוי | JSON דקלרטיבי | לא דורש קוד, ניתן לעריכה ב-UI |
| Webhooks | Flask routes על אותו שרת | פשוט, אין שירות נוסף |
