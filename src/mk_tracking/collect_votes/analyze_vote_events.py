import os
import json
import csv
import re
from collections import Counter
from datetime import datetime
from google.cloud import bigquery

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(OUTPUT_DIR, "vote_events_past_4_years.csv")
JSON_FILE = os.path.join(OUTPUT_DIR, "vote_events_past_4_years.json")
REPORT_FILE = os.path.join(OUTPUT_DIR, "analysis_summary.md")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

print("Initializing BigQuery client...")
client = bigquery.Client(project=PROJECT_ID)

# Deduplicate by title_he, keeping only the latest vote event per title_he
query = f"""
SELECT
  id,
  external_key,
  event_kind,
  bill_id,
  committee_id,
  reading,
  title_he,
  voted_at
FROM `{PROJECT_ID}.mk_tracking.vote_event`
WHERE voted_at >= TIMESTAMP('2022-07-31 00:00:00')
  AND title_he IS NOT NULL
  AND TRIM(title_he) != ''
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY title_he
  ORDER BY voted_at DESC
) = 1
ORDER BY voted_at DESC
"""

print("Fetching latest vote events per bill title from BigQuery for the past 4 years...")
rows = list(client.query(query).result())
total_unique_latest = len(rows)
print(f"Total unique latest vote events retrieved: {total_unique_latest}")

# Convert to list of dicts
events = []
for r in rows:
    v_date = r.voted_at.strftime("%Y-%m-%d %H:%M:%S") if r.voted_at else ""
    events.append({
        "id": r.id,
        "external_key": r.external_key or "",
        "event_kind": r.event_kind or "",
        "bill_id": r.bill_id or "",
        "committee_id": r.committee_id or "",
        "reading": r.reading if r.reading is not None else "",
        "title_he": r.title_he or "",
        "voted_at": v_date
    })

# 1. Save to JSON
with open(JSON_FILE, "w", encoding="utf-8") as f:
    json.dump(events, f, ensure_ascii=False, indent=2)
print(f"Exported JSON dataset to: {JSON_FILE}")

# 2. Save to CSV
with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "external_key", "event_kind", "bill_id", "committee_id", "reading", "title_he", "voted_at"])
    writer.writeheader()
    writer.writerows(events)
print(f"Exported CSV dataset to: {CSV_FILE}")

# 3. Categorization & Trend Analysis
year_counts = Counter()
kind_counts = Counter()
reading_counts = Counter()

categories = {
    "תקציב, חוק ההסדרים וכלכלה (Budget & Fiscal Policy)": [
        "תקציב", "הכספים", "הכלכלית", "יעדי", "מס", "מיסוי", "ארנונה", "שוק ההון", "בנק", "התייעלות", "פנסיה"
    ],
    "הרפורמה המשפטית ומערכת המשפט (Judiciary & Judicial Reform)": [
        "השפיטה", "חוק-יסוד", "חוק יסוד", "בתי המשפט", "שופטים", "סבירות", "בית המשפט העליון", "הסניגוריה", "סדר הדין"
    ],
    "חרבות ברזל, חירום וביטחון (Iron Swords War & National Security)": [
        "חרבות ברזל", "טרור", "מילואים", "שירות ביטחון", "צה\"ל", "פדיון שבויים", "סמכויות אכיפה", "מצב חירום", "פינוי", "עוטף"
    ],
    "משטרה, שב\"ס וביטחון פנים (Police, Prisons & Public Order)": [
        "פקודת المשטרה", "פקודת בתי הסוהר", "בתי הסוהר", "פלילי", "אכיפה", "מאסר", "משטרה"
    ],
    "חברה, רווחה, בריאות וחינוך (Social Welfare, Health & Education)": [
        "רווחה", "בריאות", "חינוך", "מוגבלות", "קטינים", "ביטוח לאומי", "סיעוד", "תמיכות", "עבודה", "שכר"
    ],
    "תכנון, בנייה ודיור (Planning, Construction & Housing)": [
        "התכנון והבנייה", "תכנון", "בנייה", "מקרקעין", "דיור", "תשתיות", "תחבורה"
    ],
    "ממשל, הצעות אי-אמון ופרוצדורה (Governance & Parliamentary Motions)": [
        "אי-אמון", "אי אמון", "הסרת חסינות", "סדר היום", "מינוי", "פיצול"
    ]
}

cat_counts = Counter()
cat_samples = {cat: [] for cat in categories}

for ev in events:
    v_at = ev["voted_at"]
    if v_at:
        year_counts[v_at[:4]] += 1
    
    kind_counts[ev["event_kind"] or "unspecified"] += 1
    reading_counts[str(ev["reading"]) if ev["reading"] != "" else "unspecified"] += 1

    title = ev["title_he"]
    for cat, kws in categories.items():
        if any(kw in title for kw in kws):
            cat_counts[cat] += 1
            if len(cat_samples[cat]) < 5 and title not in cat_samples[cat]:
                cat_samples[cat].append(title)

# Keyword frequency analysis
stop_words = {'של', 'את', 'על', 'לפי', 'לשנת', 'התשפ', 'התשפ"ג', 'התשפ"ד', 'התשפ"ב', 'התשפ"א', 'התשפ"ה', 'התשפ"ו', 'חוק', 'הצעת', 'תיקון', 'מס', 'בדבר', 'או', 'אם', 'כי', 'גם', 'רק', 'עד', 'נגד', 'לבין', 'בין', 'כדי', 'מה', 'מי', 'כל', 'אשר', 'עם', 'זה', 'בפני', 'אלו', 'בגין', 'הוראת', 'שעה', 'הוראות', 'מעבר', 'כללי', 'שונים', 'מספר', 'סעיף', 'חוקים', 'התשפ"ו-2026'}

all_words = []
for ev in events:
    w_list = re.findall(r'[\u0590-\u05FF]+', ev["title_he"])
    filtered = [w for w in w_list if len(w) > 2 and w not in stop_words]
    all_words.extend(filtered)

word_counts = Counter(all_words)

# Generate Markdown Report
report_md = f"""# ניתוח אירועי ההצבעה הבלעדיים האחרונים בכנסת (2022 - 2026)
*תאריך הפקה:* {datetime.now().strftime('%Y-%m-%d')}  
*מקור נתונים:* מסד הנתונים BigQuery (`mk_tracking.vote_event`)

---

## 1. סיכום כמותי (לאחר ניכוי כפילויות – הצבעה אחרונה בלבד לכל חוק/נושא)
* **סה"כ אירועי הצבעה ייחודיים ב-4 השנים האחרונות (הצבעה אחרונה לפי שם):** **{total_unique_latest:,}** חוקים/הצבעות נפרדות (מתוך 7,556 הצבעות כוללות).
* **שיטת הסינון:** עברו ניכוי כפילויות על לפי שם החוק/ההצבעה (`title_he`) תוך שמישת **ההצבעה המאוחרת ביותר בלבד** לכל חוק (`QUALIFY ROW_NUMBER() OVER (PARTITION BY title_he ORDER BY voted_at DESC) = 1`).

### התפלגות לפי שנים (הצבעה אחרונה לחוק):
| שנה | חוקים/אירועים ייחודיים | אחוז מסך החוקים הייחודיים |
|---|---|---|
"""

for y, c in sorted(year_counts.items()):
    pct = (c / total_unique_latest) * 100
    report_md += f"| **{y}** | {c:,} | {pct:.1f}% |\n"

report_md += f"""
---

## 2. התפלגות נושאים מרכזיים ומגמות חקיקה בכנסת

"""

for cat, count in cat_counts.most_common():
    pct = (count / total_unique_latest) * 100
    report_md += f"### {cat}\n"
    report_md += f"* **כמות חוקים/אירועים ייחודיים:** {count:,} ({pct:.1f}% מכלל החוקים הייחודיים)\n"
    report_md += "* **דוגמאות לחוקים בולטים:**\n"
    for s in cat_samples[cat]:
        report_md += f"  * `{s}`\n"
    report_md += "\n"

report_md += """---

## 3. מילות מפתח שכיחות ביותר בכותרות החוקים הייחודיים
| מילת מפתח | מופעים |
|---|---|
"""

for word, count in word_counts.most_common(20):
    report_md += f"| **{word}** | {count:,} |\n"

report_md += """
---

## 4. מנגנון ומבנה הנתונים
* הקבצים המודכנים שנשמרו בתיקייה זו:
  * **[vote_events_past_4_years.json](vote_events_past_4_years.json)** - מאגר הנתונים המעודכן (הצבעה אחרונה בלבד לכל חוק) בפורמט JSON JSON Lines.
  * **[vote_events_past_4_years.csv](vote_events_past_4_years.csv)** - טבלת CSV המכילה אך ורק את ההצבעה המאוחרת ביותר לכל חוק.
"""

with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write(report_md)

print(f"Generated summary report at: {REPORT_FILE}")
print("All tasks completed successfully!")
