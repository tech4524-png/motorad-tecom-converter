"""
Minimal, dependency-free i18n for the admin UI. One admin uses this system,
so the chosen language is a single app-wide setting (stored in config.json,
changeable from the login screen or Settings) rather than a per-visitor
cookie/session - simpler, and correct for how this tool is actually used.

Usage in templates: {{ t('log_title') }}
Usage in Python: from app.i18n import translate; translate('log_title', lang)
"""

APP_NAME = "MotoGateway"

LANGUAGES = {
    "he": "עברית",
    "en": "English",
}

RTL_LANGUAGES = {"he"}

TRANSLATIONS = {
    "he": {
        "app_name": APP_NAME,
        "app_tagline": "מוטוראד | מערכת המרת חשבוניות",
        "nav_log": "לוג המרות",
        "nav_profiles": "פרופילי המרה",
        "nav_settings": "הגדרות",
        "nav_countries": "מיפוי מדינות",
        "nav_updates": "עדכונים",
        "nav_logout": "התנתקות",

        "login_title": APP_NAME,
        "login_subtitle": "כניסה למערכת",
        "login_username": "שם משתמש",
        "login_password": "סיסמה",
        "login_submit": "התחברות",
        "login_error": "שם משתמש או סיסמה שגויים",
        "language_label": "שפה",

        "log_title": "לוג המרות",
        "log_search_label": "חיפוש (מספר חשבונית / שם לקוח)",
        "log_status_label": "סטטוס",
        "log_status_all": "הכל",
        "log_status_success": "הצלחה",
        "log_status_error": "שגיאה",
        "log_filter_submit": "סינון",
        "log_col_datetime": "תאריך ושעה",
        "log_col_invoice": "מספר חשבונית",
        "log_col_customer": "שם לקוח",
        "log_col_status": "סטטוס",
        "log_col_profile": "פרופיל",
        "log_col_source": "קובץ מקור",
        "log_col_output": "קובץ פלט",
        "log_col_message": "הודעה",
        "log_empty": "אין רשומות עדיין",

        "settings_title": "הגדרות מערכת",
        "settings_seller_h2": "פרטי מוטוראד (Seller)",
        "settings_field_name1": "שם החברה",
        "settings_field_street1": "רחוב ומספר",
        "settings_field_postal_code": "מיקוד",
        "settings_field_city": "עיר",
        "settings_field_country_code": "קוד מדינה (ISO, 2 אותיות)",
        "settings_field_country_name": "שם מדינה",
        "settings_field_tax_registration_number": "מספר עוסק / רישום מס",
        "settings_field_contact_name": "איש קשר - שם",
        "settings_field_contact_phone": "איש קשר - טלפון",
        "settings_field_contact_email": "איש קשר - אימייל",
        "settings_constants_h2": "קבועי XML",
        "settings_field_message_type": "MessageType",
        "settings_field_message_function": "MessageFunction",
        "settings_field_tax_treatment_code": "TaxTreatmentCode",
        "settings_field_maker_code": "MakerCode",
        "settings_bank_h2": "פרטי בנק",
        "settings_field_bank_name": "שם בנק",
        "settings_field_bank_account": "מספר חשבון",
        "settings_field_bank_code_number": "קוד בנק",
        "settings_field_iban": "IBAN",
        "settings_field_swift_bic": "SWIFT/BIC",
        "settings_save": "שמירת הגדרות",
        "settings_password_h2": "שינוי סיסמה",
        "settings_field_current_password": "סיסמה נוכחית",
        "settings_field_new_password": "סיסמה חדשה",
        "settings_field_confirm_password": "אימות סיסמה חדשה",
        "settings_password_save": "עדכון סיסמה",
        "settings_saved": "ההגדרות נשמרו",
        "password_current_wrong": "הסיסמה הנוכחית שגויה",
        "password_mismatch": "הסיסמה החדשה והאימות אינם תואמים",
        "password_updated": "הסיסמה עודכנה בהצלחה",

        "profiles_title": "פרופילי המרה",
        "profiles_intro": "לכל סוג המרה (חברה) מגדירים כאן את נתיבי הקלט, הפלט, הארכיון והשגיאות בשרת.",
        "profiles_field_input_dir": "נתיב תיקיית קלט",
        "profiles_field_output_dir": "נתיב תיקיית פלט (XML)",
        "profiles_field_archive_dir": "נתיב תיקיית ארכיון (XLS מקורי)",
        "profiles_field_error_dir": "נתיב תיקיית שגיאות",
        "profiles_field_enabled": "פעיל",
        "profiles_save": "שמירה",
        "profiles_saved": "פרופיל ההמרה עודכן",

        "countries_title": "מיפוי מדינות (שם מדינה ← קוד ISO)",
        "countries_intro": "המערכת כוללת טבלה מובנית של כ-{count} מדינות. אם קובץ נכשל בגלל שם מדינה שלא זוהה, הוסיפו אותו כאן - זה עוקף/מוסיף לטבלה המובנית בלי צורך בעדכון קוד.",
        "countries_add_h2": "הוספת מיפוי מותאם",
        "countries_field_name": "שם המדינה כפי שמופיע בקובץ מפריוריטי",
        "countries_field_code": "קוד ISO (2 אותיות)",
        "countries_add_submit": "הוספה",
        "countries_overrides_h2": "מיפויים מותאמים (גוברים על הטבלה המובנית)",
        "countries_col_name": "שם מדינה",
        "countries_col_code": "קוד",
        "countries_delete": "מחיקה",
        "countries_added": "נוסף מיפוי: {name} -> {code}",
        "countries_add_error": "יש להזין שם מדינה וקוד ISO בן 2 אותיות",
        "countries_removed": "המיפוי עבור {name} הוסר",

        "updates_title": "עדכוני מערכת",
        "updates_current_version": "גרסה מותקנת כעת:",
        "updates_intro": "העלו כאן קובץ עדכון (zip) שתקבלו מהמפתח. המערכת תבדוק שהגרסה חדשה יותר, תגבה את הקבצים הקיימים, תחליף אותם, ותפעיל מחדש את השירות אוטומטית - בלי צורך בהתקנה מחדש.",
        "updates_field_package": "קובץ עדכון (.zip)",
        "updates_submit": "העלאה והחלה",
        "updates_backups_h2": "גיבויים קודמים (לצורך שחזור ידני במקרה הצורך)",
        "updates_missing_file": "יש לבחור קובץ עדכון",
        "updates_applied_restarted": "העדכון לגרסה {version} הותקן. השירות יופעל מחדש תוך מספר שניות - רענן/י את הדף אז",
        "updates_applied_manual_restart": "העדכון לגרסה {version} הותקן, אך יש להפעיל מחדש את השירות ידנית (שירות: {service})",
        "updates_failed": "העדכון נכשל: {error}",
    },
    "en": {
        "app_name": APP_NAME,
        "app_tagline": "Motorad | Invoice Conversion System",
        "nav_log": "Conversion Log",
        "nav_profiles": "Conversion Profiles",
        "nav_settings": "Settings",
        "nav_countries": "Country Mapping",
        "nav_updates": "Updates",
        "nav_logout": "Log out",

        "login_title": APP_NAME,
        "login_subtitle": "Sign in",
        "login_username": "Username",
        "login_password": "Password",
        "login_submit": "Sign in",
        "login_error": "Incorrect username or password",
        "language_label": "Language",

        "log_title": "Conversion Log",
        "log_search_label": "Search (invoice number / customer name)",
        "log_status_label": "Status",
        "log_status_all": "All",
        "log_status_success": "Success",
        "log_status_error": "Error",
        "log_filter_submit": "Filter",
        "log_col_datetime": "Date & time",
        "log_col_invoice": "Invoice number",
        "log_col_customer": "Customer name",
        "log_col_status": "Status",
        "log_col_profile": "Profile",
        "log_col_source": "Source file",
        "log_col_output": "Output file",
        "log_col_message": "Message",
        "log_empty": "No records yet",

        "settings_title": "System Settings",
        "settings_seller_h2": "Motorad Details (Seller)",
        "settings_field_name1": "Company name",
        "settings_field_street1": "Street & number",
        "settings_field_postal_code": "Postal code",
        "settings_field_city": "City",
        "settings_field_country_code": "Country code (ISO, 2 letters)",
        "settings_field_country_name": "Country name",
        "settings_field_tax_registration_number": "Tax registration number",
        "settings_field_contact_name": "Contact - name",
        "settings_field_contact_phone": "Contact - phone",
        "settings_field_contact_email": "Contact - email",
        "settings_constants_h2": "XML Constants",
        "settings_field_message_type": "MessageType",
        "settings_field_message_function": "MessageFunction",
        "settings_field_tax_treatment_code": "TaxTreatmentCode",
        "settings_field_maker_code": "MakerCode",
        "settings_bank_h2": "Banking Details",
        "settings_field_bank_name": "Bank name",
        "settings_field_bank_account": "Account number",
        "settings_field_bank_code_number": "Bank code",
        "settings_field_iban": "IBAN",
        "settings_field_swift_bic": "SWIFT/BIC",
        "settings_save": "Save settings",
        "settings_password_h2": "Change Password",
        "settings_field_current_password": "Current password",
        "settings_field_new_password": "New password",
        "settings_field_confirm_password": "Confirm new password",
        "settings_password_save": "Update password",
        "settings_saved": "Settings saved",
        "password_current_wrong": "Current password is incorrect",
        "password_mismatch": "New password and confirmation do not match",
        "password_updated": "Password updated successfully",

        "profiles_title": "Conversion Profiles",
        "profiles_intro": "For each conversion type (partner), configure the input, output, archive, and error folder paths on the server.",
        "profiles_field_input_dir": "Input folder path",
        "profiles_field_output_dir": "Output folder path (XML)",
        "profiles_field_archive_dir": "Archive folder path (original XLS)",
        "profiles_field_error_dir": "Error folder path",
        "profiles_field_enabled": "Enabled",
        "profiles_save": "Save",
        "profiles_saved": "Conversion profile updated",

        "countries_title": "Country Mapping (country name -> ISO code)",
        "countries_intro": "The system includes a built-in table of about {count} countries. If a file fails because a country name isn't recognized, add it here - this overrides/extends the built-in table without needing a code change.",
        "countries_add_h2": "Add a custom mapping",
        "countries_field_name": "Country name as it appears in the Priority file",
        "countries_field_code": "ISO code (2 letters)",
        "countries_add_submit": "Add",
        "countries_overrides_h2": "Custom mappings (override the built-in table)",
        "countries_col_name": "Country name",
        "countries_col_code": "Code",
        "countries_delete": "Delete",
        "countries_added": "Mapping added: {name} -> {code}",
        "countries_add_error": "Please enter a country name and a 2-letter ISO code",
        "countries_removed": "Mapping for {name} removed",

        "updates_title": "System Updates",
        "updates_current_version": "Currently installed version:",
        "updates_intro": "Upload an update package (zip) provided by the developer here. The system will check the version is newer, back up the current files, replace them, and automatically restart the service - no reinstall needed.",
        "updates_field_package": "Update package (.zip)",
        "updates_submit": "Upload and apply",
        "updates_backups_h2": "Previous backups (for manual restore if needed)",
        "updates_missing_file": "Please choose an update file",
        "updates_applied_restarted": "Update {version} installed. The service will restart within a few seconds - refresh the page then",
        "updates_applied_manual_restart": "Update {version} installed, but you must restart the service manually (service: {service})",
        "updates_failed": "Update failed: {error}",
    },
}


def is_rtl(lang):
    return lang in RTL_LANGUAGES


def translate(key, lang, **kwargs):
    lang_table = TRANSLATIONS.get(lang, TRANSLATIONS["he"])
    text = lang_table.get(key, TRANSLATIONS["he"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def make_translator(lang):
    def t(key, **kwargs):
        return translate(key, lang, **kwargs)
    return t
